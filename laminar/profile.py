from dataclasses import dataclass
import math
from tempfile import TemporaryDirectory
from pathlib import Path
import numpy as np
import ezdxf
from ezdxf.path import make_path
from shapely.geometry import Polygon, LineString
from shapely.errors import ShapelyError
from shapely.geometry.polygon import orient
import manifold3d as m


@dataclass
class Profile:
    polygon: Polygon
    height: float
    radius: float
    bottom_original: list
    name: str
    axis_original: list

    def solid(self):
        # CrossSection coordinates are radius and axial height.
        return m.CrossSection([np.asarray(orient(self.polygon, 1).exterior.coords)[:-1]]).revolve(96)


def read_dxf(raw: bytes):
    try:
        with TemporaryDirectory(prefix='laminar-dxf-') as directory:
            path=Path(directory)/'profile.dxf'
            path.write_bytes(raw)
            doc=ezdxf.readfile(path)
    except (UnicodeError, ezdxf.DXFError, OSError) as exc:
        raise ValueError('Нужен корректный DXF с одним замкнутым контуром') from exc
    units = doc.header.get('$INSUNITS', 0)
    scales = {0: 1, 1: 25.4, 4: 1, 5: 10, 6: 1000}
    if units not in scales:
        raise ValueError('Неподдерживаемые единицы DXF; сохраните в миллиметрах')
    entities = list(doc.modelspace())
    if not entities or any(e.dxftype() not in ('LWPOLYLINE','POLYLINE','SPLINE','LINE','ARC','CIRCLE','ELLIPSE') for e in entities):
        raise ValueError('Ожидается один замкнутый контур без посторонних объектов')
    from shapely.ops import linemerge
    from shapely.geometry import MultiLineString
    lines=[]
    for entity in entities:
        path=make_path(entity)
        xyz=np.array([list(v) for v in path.flattening(0.02 / scales[units])])*scales[units]
        if len(xyz)<2 or np.ptp(xyz[:,2])>1e-6:
            raise ValueError('DXF должен быть плоским в XY')
        lines.append(LineString(np.round(xyz[:,:2],8)))
    joined=lines[0] if len(lines)==1 else linemerge(MultiLineString(lines))
    if joined.geom_type!='LineString' or not joined.is_ring:
        raise ValueError('DXF-контур не замкнут, содержит ветвления или несколько контуров')
    points=np.asarray(joined.coords)
    poly = Polygon(points)
    if not poly.is_valid or poly.area < 1e-4:
        raise ValueError('Контур самопересекается или имеет нулевую площадь')
    return points[:-1].tolist() if np.allclose(points[0], points[-1]) else points.tolist()


def prepare(points, top_edge, name='profile.dxf', axis_side=None):
    p = np.asarray(points, dtype=float)
    if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3 or not np.isfinite(p).all():
        raise ValueError('Некорректные координаты профиля')
    poly = Polygon(p)
    if not poly.is_valid or poly.area < 1e-4:
        raise ValueError('Профиль должен быть простым замкнутым контуром')
    if not 0 <= top_edge < len(p):
        raise ValueError('Выберите существующую верхнюю сторону')
    a, b = p[top_edge], p[(top_edge + 1) % len(p)]
    u = b-a
    if np.linalg.norm(u) < 1e-8:
        raise ValueError('Верхняя сторона имеет нулевую длину')
    u /= np.linalg.norm(u)
    v = np.array([-u[1], u[0]])
    if np.dot(np.asarray(poly.centroid.coords)[0]-a, v) > 0:
        v *= -1
    if axis_side is not None:
        if axis_side not in ('left','right'):
            raise ValueError('Ось: только левый или правый край профиля')
        # Right direction after the selected side has been placed at the top;
        # independent of input polygon winding and selected segment direction.
        u = np.array([v[1],-v[0]])
    rotated = np.column_stack((p@u, p@v))
    if np.max(rotated[:, 1]) > np.dot(a, v)+1e-5:
        raise ValueError('Выбранная сторона не является верхней опорной стороной')
    bottom = p[np.argmin(rotated[:, 1])].tolist()
    origin = rotated.min(axis=0).copy()
    axis_x = float(origin[0])
    rotated -= origin
    h = float(np.ptp(rotated[:, 1]))
    rotated[np.abs(rotated[:,1]-h)<1e-8,1]=h
    width=float(np.ptp(rotated[:,0]))
    def has_axis(coords):
        polygon=Polygon(coords)
        axis=LineString([(0,0),(0,h)])
        return polygon.boundary.buffer(1e-6).covers(axis)
    if axis_side is not None:
        # An explicit extreme axis removes the old ambiguity. A half-section
        # need not have a full-height straight edge on that axis (annular tops
        # are valid); no centre-axis option is exposed in the native UI.
        if axis_side == 'right':
            axis_x += width
            rotated[:,0] = width-rotated[:,0]
        poly=Polygon(rotated)
    elif has_axis(rotated):
        poly=Polygon(rotated)
    else:
        reflected=rotated.copy();reflected[:,0]=width-reflected[:,0]
        if has_axis(reflected):
            axis_x += width
            rotated=reflected;poly=Polygon(rotated)
        elif Polygon(rotated).symmetric_difference(Polygon(reflected)).area <= 1e-5*max(poly.area,1):
            # Symmetric full axial section: use its right half, preserving radius.
            from shapely.geometry import box
            from shapely.affinity import translate
            half=Polygon(rotated).intersection(box(width/2,-1,width+1,h+1))
            if half.geom_type!='Polygon':
                raise ValueError('Профиль распадается при выделении осевого полусечения')
            axis_x += width/2
            poly=translate(half,xoff=-width/2)
            rotated=np.asarray(poly.exterior.coords)[:-1]
        else:
            raise ValueError('Ось неоднозначна: нужен осевой полупрофиль либо симметричный полный профиль')
    r = float(rotated[:, 0].max())
    # Vertical line intersections must be connected: no hidden ledges/cavities.
    xs=np.unique(rotated[:,0])
    for x in (xs[:-1]+xs[1:])/2:
        section = poly.intersection(LineString([(x,-1), (x,h+1)]))
        if section.geom_type != 'LineString':
            raise ValueError('Профиль не проходит проверку вертикальных теней')
    axis = [(axis_x*u + y*v).tolist() for y in (origin[1], origin[1]+h)]
    return Profile(poly, h, r, bottom, name, axis)


def seat_depth(profile, nominal):
    return min(profile.height*.2, nominal*.15)


def validate_seat(profile, nominal):
    """Legacy optional diagnostic, retired from selection/generation filtering.

    Check the actual socket wall in upside-down printing coordinates.

    Only the engaged top band is subtracted from the panel. Its lower-facing
    inner walls must stay within the same 40-degree limit as STL validation.
    """
    from shapely.geometry import box
    depth = seat_depth(profile, nominal)
    band = profile.polygon.intersection(box(-1, profile.height-depth, profile.radius+1, profile.height+1))
    if band.geom_type != 'Polygon' or band.is_empty:
        raise ValueError('Профиль не образует единую посадочную область')
    coords = np.asarray(orient(band, 1).exterior.coords)
    for a,b in zip(coords, coords[1:]):
        # Ignore axial boundary and the two artificial caps. With CCW winding,
        # -dr/ds is the inner-wall Z normal after reflection and subtraction.
        if max(a[0],b[0]) < 1e-7:
            continue
        if abs(a[1]-b[1]) < 1e-8 and (abs(a[1]-profile.height) < 1e-7 or abs(a[1]-(profile.height-depth)) < 1e-7):
            continue
        delta=b-a
        length=np.linalg.norm(delta)
        if length < 1e-10:
            continue
        if -delta[0]/length < -np.sin(np.deg2rad(40))-1e-5:
            raise ValueError('Посадка в этой ориентации имеет нависание более 40° от вертикали')
    return depth


def valid_orientations(points, nominal, name='profile.dxf', axis_side='left'):
    """Preflight contour/revolution geometry; print quality is advisory later."""
    if not math.isfinite(nominal) or nominal <= 0:
        raise ValueError('Номинальная толщина: требуется положительное конечное число')
    valid = {}
    for edge in range(len(points)):
        try:
            profile=prepare(points,edge,name,axis_side=axis_side)
            solid=profile.solid()
            if solid.status()!=m.Error.NoError or solid.volume()<=1e-8 or len(solid.decompose())!=1:
                continue
            valid[edge]=profile
        except (ValueError, ArithmeticError, ShapelyError):
            continue
    if not valid:
        raise ValueError('У профиля нет допустимых верхних сторон: проверьте контур и геометрию тела вращения')
    return valid


def contact_patch(profile, position, domain):
    """Actual top-face annuli of the revolved external support, clipped to panel."""
    from shapely.geometry import Point
    from shapely.ops import unary_union
    cut=profile.polygon.intersection(LineString([(-1,profile.height),(profile.radius+1,profile.height)]))
    lines=[cut] if cut.geom_type=='LineString' else list(getattr(cut,'geoms',()))
    rings=[]
    for line in lines:
        if line.geom_type!='LineString' or line.length<1e-8:continue
        lo,_,hi,_=line.bounds
        ring=Point(position).buffer(hi,quad_segs=48)
        if lo>1e-8:ring=ring.difference(Point(position).buffer(lo,quad_segs=48))
        rings.append(ring)
    patch=unary_union(rings).intersection(domain)
    if patch.is_empty or patch.area<1e-8:
        raise ValueError('Верхняя сторона опоры не имеет площади контакта внутри панели')
    return patch
