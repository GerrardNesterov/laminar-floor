import numpy as np
from shapely import voronoi_polygons, maximum_inscribed_circle
from shapely.geometry import MultiPoint, Polygon, LineString, Point, box
from shapely.ops import unary_union
from shapely.geometry.polygon import orient
import manifold3d as m
import trimesh


def polygons(shape):
    if shape.is_empty:
        return []
    if shape.geom_type == 'Polygon':
        return [shape]
    return [p for g in shape.geoms for p in polygons(g)] if hasattr(shape, 'geoms') else []


def section(shape):
    contours = []
    for p in polygons(shape):
        p = orient(p, 1)
        contours.append(np.asarray(p.exterior.coords)[:-1])
        contours.extend(np.asarray(r.coords)[:-1] for r in p.interiors)
    return m.CrossSection(contours, m.FillRule.NonZero)


def extrude(shape, height):
    return section(shape).extrude(float(height))


def mesh_of(solid):
    raw = solid.to_mesh64()
    return trimesh.Trimesh(np.asarray(raw.vert_properties)[:, :3], np.asarray(raw.tri_verts), process=True, validate=True)


def network(domain, supports, spacing, seed):
    rng = np.random.default_rng(seed)
    x0,y0,x1,y1 = domain.bounds
    count = max(12, int(domain.area / (spacing*spacing)*2.2))
    if count > 1800:
        raise ValueError('Слишком большая панель для заданных отверстий: более 1800 расчётных ячеек')
    points = []
    for _ in range(count*15):
        p = rng.uniform([x0,y0], [x1,y1])
        if domain.contains(Point(p)):
            points.append(p)
        if len(points) == count:
            break
    if len(points) < 4:
        raise ValueError('Недостаточная площадь панели')
    cells = voronoi_polygons(MultiPoint(points), extend_to=box(x0-1,y0-1,x1+1,y1+1))
    lines = [domain.boundary]
    for cell in cells.geoms:
        cut = cell.intersection(domain)
        lines.extend(p.boundary for p in polygons(cut))
    # Connect custom support positions into the real material network.
    combined = unary_union(lines)
    from shapely.ops import nearest_points
    for s in supports:
        q = nearest_points(Point(s), combined)[1]
        if q.distance(Point(s)) > 1e-6:
            lines.append(LineString([s, q.coords[0]]))
    combined = unary_union(lines)
    coords, lookup, edges = [], {}, set()
    def index(p):
        key = tuple(np.round(p, 5))
        if key not in lookup:
            lookup[key] = len(coords)
            coords.append(key)
        return lookup[key]
    for line in combined.geoms:
        pts = list(line.coords)
        for a,b in zip(pts, pts[1:]):
            i,j = index(a), index(b)
            if i != j and np.linalg.norm(np.array(a)-b) > 1e-4:
                edges.add(tuple(sorted((i,j))))
    # Contract tiny Voronoi edges before assembling stiffness or solid geometry.
    from scipy.spatial import cKDTree
    coordinates=np.asarray(coords)
    parent=list(range(len(coordinates)))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for i,j in sorted(cKDTree(coordinates).query_pairs(spacing*.03)):
        ri,rj=root(i),root(j)
        if ri!=rj:
            # Never merge two distinct boundary points: preserves exact contour.
            bi=domain.boundary.distance(Point(coordinates[ri]))<1e-4
            bj=domain.boundary.distance(Point(coordinates[rj]))<1e-4
            if bi and bj: continue
            if bj: ri,rj=rj,ri
            parent[rj]=ri
    unique=sorted(set(root(i) for i in range(len(coordinates))))
    remap={old:i for i,old in enumerate(unique)}
    contracted=set()
    for i,j in edges:
        a,b=remap[root(i)],remap[root(j)]
        if a!=b: contracted.add(tuple(sorted((a,b))))
    return coordinates[unique],np.asarray(sorted(contracted),dtype=int)


def physical_widths(domain,nodes,edges,widths):
    boundary=np.array([domain.boundary.buffer(1e-4).covers(LineString(nodes[e])) for e in edges])
    return np.asarray(widths)*np.where(boundary,.5,1.)



def footprints(domain, nodes, edges, widths, supports, radius, min_gap=0):
    strips = [LineString(nodes[e]).buffer(float(w)/2, cap_style='round', quad_segs=3).intersection(domain)
              for e,w in zip(edges, widths)]
    pads = [Point(s).buffer(radius, quad_segs=24).intersection(domain) for s in supports]
    surface = unary_union(strips+pads)
    if min_gap:
        # Opening the voids removes sub-nozzle throats as well as small holes.
        # Added material is a thin surface fill, not a full-height pillar.
        holes=polygons(domain.difference(surface))
        printable_voids=unary_union([p.buffer(-min_gap/2,quad_segs=8).buffer(min_gap/2,quad_segs=8).intersection(p)
                                    for p in holes])
        fill=domain.difference(surface).difference(printable_voids)
        fillers=[fill.buffer(0.005,quad_segs=4).intersection(domain)] if not fill.is_empty else []
        pads.extend(fillers)
        surface=unary_union([surface]+fillers)
    return strips, pads, surface


def assemble(domain, strips, pads, heights, profile, supports, total_height, embed, *, min_gap=0):
    # Form nested 2D slices first. Hundreds of overlapping 3D beam booleans
    # otherwise produce sub-micron coincident edges at irregular junctions.
    from shapely import set_precision
    shapes=[set_precision(s,1e-5) for s in strips]+[set_precision(p,1e-5) for p in pads]
    levels=list(heights)+[total_height+embed]*min(len(supports),len(pads))+[float(min(heights))]*max(0,len(pads)-len(supports))
    boundaries=sorted(set(float(h) for h in levels))
    solids=[];previous=0.;previous_footprint=None
    for height in boundaries:
        footprint=unary_union([s for s,h in zip(shapes,levels) if h>=height-1e-8])
        if min_gap:
            # Removing a shallow skin bridge can expose a thin void higher up.
            # Morphological void opening is monotone for nested raw sections.
            # Include exterior air so domain corners are not fabricated solids.
            voids=domain.difference(footprint)
            exterior_air=domain.buffer(min_gap*2,join_style='mitre').difference(domain)
            extended_voids=unary_union([voids,exterior_air])
            printable=extended_voids.buffer(-min_gap/2,quad_segs=8).buffer(min_gap/2,quad_segs=8).intersection(voids)
            footprint=domain.difference(printable)
        if previous_footprint is not None:
            # Strict interior nesting avoids coincident micro-faces. Bound the
            # total setback to 0.001 mm regardless of the number of levels,
            # so mechanical rib widths lose at most 0.002 mm overall.
            footprint=footprint.intersection(previous_footprint)
            voids=domain.difference(footprint)
            footprint=domain.difference(voids.buffer(0.001/max(1,len(boundaries)-1),join_style='mitre'))
        previous_footprint=footprint
        if not footprint.is_empty and height>previous:
            # Adjacent floating-point levels (e.g.7.6) must overlap rather
            # than merely touch: subtraction/translation can leave an ulp gap
            # and two closed bodies with full-area downward interface faces.
            # Nested XY sections make this overlap internal to the existing
            # lower solid, so it changes no designed external geometry.
            z_start=max(0.,previous-0.0001)
            solids.append(extrude(footprint,height-z_start).translate([0,0,z_start]))
        previous=height
    solid=m.Manifold.batch_boolean(solids,m.OpType.Add)
    # The working plane is Z=0 (printed upside down). The external foot top
    # must be exactly at the nominal height, irrespective of adaptive rib depth.
    foot = profile.solid().mirror([0,0,1]).translate([0,0,total_height+profile.height])
    cutters = [foot.translate([x,y,0]) for x,y in supports]
    solid = solid - m.Manifold.batch_boolean(cutters, m.OpType.Add)
    # Recreate coplanar faces across original Boolean operands before
    # simplification; otherwise collinear zero-area triangles can survive.
    solid = solid.as_original().simplify(0.00001)
    if solid.status() != m.Error.NoError:
        raise ValueError(f'Ошибка геометрического ядра: {solid.status()}')
    return solid


def opening_stats(domain, surface):
    holes = polygons(domain.difference(surface))
    diameters = [2*maximum_inscribed_circle(p, tolerance=0.005).length+0.01 for p in holes]
    return holes, max(diameters, default=0.)


def validate_mesh(solid, domain, max_height):
    mesh = mesh_of(solid)
    errors = []
    if not mesh.is_watertight or not mesh.is_winding_consistent or not mesh.is_volume:
        errors.append('Незамкнутая геометрия или некорректные нормали')
    if len(solid.decompose()) != 1:
        errors.append('Панель состоит из несвязанных тел')
    x0,y0,x1,y1 = domain.bounds
    if not np.allclose(mesh.bounds[:, :2], [[x0,y0],[x1,y1]], atol=0.02):
        errors.append('Габариты модели не совпадают с контуром панели')
    if abs(mesh.bounds[0,2]) > 0.01 or abs(mesh.bounds[1,2]-max_height) > 0.01:
        errors.append('Нарушены ориентация печати или положение посадки относительно номинальной толщины')
    bad = (mesh.face_normals[:,2] < -np.sin(np.deg2rad(40))-1e-5) & (mesh.triangles_center[:,2] > 0.02)
    if np.any(bad):
        errors.append(f'Нависания более 40° от вертикали: {int(bad.sum())} граней')
    return mesh, errors, bad
