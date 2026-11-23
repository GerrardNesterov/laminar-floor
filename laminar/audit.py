"""Independent mesh-intersection and sampled-section feature audits.

The mesh test is performed by MeshLab/VCG, independently of Manifold. The planar
check is a morphological minimum-feature screen on the supplied section only;
callers must state their Z sampling and must not present it as a continuum 3D
thickness proof. Dimensions and tolerances are in millimetres.
"""
import math
import threading

import numpy as np
from shapely.geometry import Polygon
from shapely import maximum_inscribed_circle


_NATIVE_LOCK = threading.Lock()


def mesh_self_intersections(mesh, max_faces=300_000):
    """Return intersecting triangle indices from the native independent checker.

    Adjacent manifold faces sharing an edge are not reported as self-intersecting
    by the MeshLab filter. Exceeding the bounded workload is an explicit failed
    audit, never a pass. The original input mesh is not modified.
    """
    result = dict(checked=False, method='MeshLab/VCG triangle self-intersection selection',
                  intersecting_faces=[], count=0, issues=[])
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or not np.isfinite(vertices).all()
            or faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
            or not np.issubdtype(faces.dtype, np.integer)
            or faces.min() < 0 or faces.max() >= len(vertices)):
        result['issues'].append('Независимая проверка: некорректная треугольная сетка')
        return result
    if len(faces) > max_faces:
        result['issues'].append(f'Независимая проверка пересечений не выполнена: {len(faces)} граней, предел {max_faces}')
        return result
    try:
        import pymeshlab
        # MeshLab's native filters use process-global plugin state; serialize calls.
        with _NATIVE_LOCK:
            session = pymeshlab.MeshSet()
            session.add_mesh(pymeshlab.Mesh(vertex_matrix=vertices, face_matrix=faces.astype(np.int32)))
            session.compute_selection_by_self_intersections_per_face()
            selected = np.flatnonzero(session.current_mesh().face_selection_array())
        result.update(checked=True, intersecting_faces=selected.tolist(), count=int(len(selected)))
        if len(selected):
            result['issues'].append(f'Независимая проверка: пересекаются {len(selected)} треугольников')
    except Exception as exc:
        # PyMeshLabException directly inherits Exception; every native failure
        # is an explicit failed audit, not an uncaught app crash or a pass.
        result['issues'].append(f'Независимая проверка пересечений не выполнена: {exc}')
    return result


def _polygons(shape):
    if shape.is_empty:
        return []
    if shape.geom_type == 'Polygon':
        return [shape]
    return [p for part in getattr(shape, 'geoms', ()) for p in _polygons(part)]


def _topology(shape, area_epsilon):
    parts = [p for p in _polygons(shape) if p.area > area_epsilon]
    holes = sum(sum(Polygon(r).area > area_epsilon for r in p.interiors) for p in parts)
    return len(parts), holes


def _feature_audit(shape, minimum, tolerance):
    effective = max(0., minimum-tolerance)
    radius = effective/2
    opened = shape.buffer(-radius, quad_segs=16).buffer(radius, quad_segs=16).intersection(shape) if radius else shape
    lost = shape.difference(opened)
    area_epsilon = max(tolerance*tolerance, 1e-10)
    before, after = _topology(shape, area_epsilon), _topology(opened, area_epsilon)
    # Numerical buffering residue and pointy/rounded corner residue are not
    # equivalent to an unprintable wall. Only retain resolved loss components.
    resolved = lost.buffer(-tolerance/2, quad_segs=8).buffer(tolerance/2, quad_segs=8)
    slivers = []
    for part in _polygons(resolved):
        if part.area <= area_epsilon:
            continue
        diameter = 2*maximum_inscribed_circle(part, tolerance=tolerance/4).length
        rectangle = np.asarray(part.minimum_rotated_rectangle.exterior.coords)
        lengths = np.linalg.norm(np.diff(rectangle, axis=0), axis=1)
        span = float(max(lengths))
        if span > 2*effective and diameter < effective:
            slivers.append(dict(area_mm2=float(part.area), length_mm=span,
                                local_diameter_mm=float(diameter),
                                bounds_mm=list(part.bounds)))
    topology_changed = before != after
    return dict(minimum_mm=minimum, effective_minimum_mm=effective,
                opening_loss_area_mm2=float(lost.area),
                topology_before=dict(components=before[0], holes=before[1]),
                topology_after=dict(components=after[0], holes=after[1]),
                topology_changed=topology_changed,
                long_thin_regions=slivers,
                long_thin_region_count=len(slivers),
                long_thin_area_mm2=float(sum(p['area_mm2'] for p in slivers)),
                passed=not topology_changed and not slivers)


def planar_audit(surface, domain, min_wall, min_gap, tolerance=0.02):
    """Screen one actual solid section for thin material and narrow air necks.

    Opening by a disk detects severed thin bridges, merged holes, vanished narrow
    regions and elongated thin residues. Small corner-rounding residues are
    ignored. `minimum - tolerance` is the tested threshold. Report both the raw
    opening loss (which includes acceptable corners) and actionable thin areas.
    A pass is a bounded morphological screen, not an exact minimum-thickness
    certificate. Apply this to actual post-boolean slices, not design buffers.
    """
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
               and math.isfinite(x) and x > 0 for x in (min_wall, min_gap, tolerance)):
        raise ValueError('Пределы толщины, зазора и допуск должны быть положительными числами')
    if tolerance >= min(min_wall, min_gap)/2:
        raise ValueError('Допуск проверки должен быть меньше половины минимального размера')
    if domain.is_empty or not domain.is_valid or domain.geom_type != 'Polygon':
        raise ValueError('Для проверки сечения нужен корректный контур панели')
    if not surface.is_valid or surface.geom_type not in ('Polygon', 'MultiPolygon', 'GeometryCollection'):
        raise ValueError('Некорректное сечение панели')
    if surface.difference(domain.buffer(tolerance)).area > tolerance*tolerance:
        raise ValueError('Сечение выходит за контур панели')
    solid = surface.intersection(domain)
    apertures = domain.difference(solid)
    material = _feature_audit(solid, float(min_wall), tolerance)
    air = _feature_audit(apertures, float(min_gap), tolerance)
    issues = []
    if solid.is_empty:
        issues.append('Сечение панели пустое')
    if not material['passed']:
        issues.append('В сечении обнаружены стенки или перемычки тоньше технологического минимума')
    if not air['passed']:
        issues.append('В сечении обнаружены узкие горловины или непечатаемые зазоры')
    return dict(checked=True, passed=not issues, tolerance_mm=tolerance,
                method='Морфологическое открытие сечения диском; топология и длинные тонкие остатки',
                scope='Только переданное сечение; без гарантии между проверенными уровнями Z',
                material=material, apertures=air, issues=issues)


def _section_polygon(section):
    """Convert Manifold's oriented contours, retaining holes and nested islands."""
    from shapely.ops import unary_union
    rings = []
    for contour in section.to_polygons():
        points = np.asarray(contour, dtype=float)
        if len(points) < 3:
            continue
        signed_area = .5*float(np.sum(points[:,0]*np.roll(points[:,1],-1)
                                      -np.roll(points[:,0],-1)*points[:,1]))
        if abs(signed_area) <= 1e-12:
            continue
        polygon = Polygon(points)
        if not polygon.is_valid:
            raise ValueError('Контур фактического сечения имеет самопересечения')
        rings.append((abs(signed_area), signed_area > 0, polygon))
    # A positive island inside a negative hole must be added *after* that hole
    # is removed. Subtracting the union of all negative contours would lose it.
    shape = Polygon()
    for _, positive, polygon in sorted(rings, key=lambda item: item[0], reverse=True):
        shape = unary_union([shape, polygon]) if positive else shape.difference(polygon)
    return shape


def solid_sections(solid, domain, min_wall, min_gap, levels):
    """Audit actual post-boolean Manifold slices at explicitly supplied Z levels.

    At most 128 finite heights are allowed. The only skipped empty section is
    at the exact bounding-box top (within 1e-9 mm), where Manifold's half-open
    slice is empty by definition. An empty interior section or an out-of-range
    request fails the audit. At least one nonempty section must be inspected.
    Passing sampled sections does not prove minimum thickness between them.
    """
    result = dict(checked=False, passed=False, sections=[], issues=[],
                  scope='Фактические сечения после булевых операций; только перечисленные уровни Z',
                  max_levels=128, exact_top_tolerance_mm=1e-9)
    try:
        requested = list(levels)
    except TypeError:
        result['issues'].append('Проверка сечений: нужен список уровней Z')
        return result
    if not requested or len(requested) > 128:
        result['issues'].append('Проверка сечений требует от 1 до 128 уровней Z')
        return result
    if not all(isinstance(z, (int, float, np.integer, np.floating))
               and not isinstance(z, (bool, np.bool_)) and math.isfinite(z) for z in requested):
        result['issues'].append('Проверка сечений: уровни Z должны быть конечными числами')
        return result
    bounds = solid.bounding_box()
    zmin, zmax = float(bounds[2]), float(bounds[5])
    result['solid_z_bounds_mm'] = [zmin, zmax]
    checked_sections = 0
    for z in sorted(set(float(z) for z in requested)):
        item = dict(z_mm=z, checked=False, passed=False, skipped=False, issues=[])
        if z < zmin-1e-9 or z > zmax+1e-9:
            item['issues'].append('Уровень сечения вне фактической высоты панели')
        else:
            try:
                surface = _section_polygon(solid.slice(z))
                item['area_mm2'] = float(surface.area)
                if surface.is_empty and abs(z-zmax) <= 1e-9:
                    item.update(skipped=True, passed=True,
                                skip_reason='Пустое сечение точно на верхней границе тела (полуоткрытый slice)')
                elif surface.is_empty:
                    item['issues'].append('Пустое сечение внутри фактической высоты панели')
                else:
                    item.update(planar_audit(surface, domain, min_wall, min_gap))
                    checked_sections += 1
            except Exception as exc:
                item['issues'].append(f'Проверка фактического сечения не выполнена: {exc}')
        result['sections'].append(item)
        result['issues'].extend(f'Z={z:g} мм: {issue}' for issue in item['issues'])
    result['checked_section_count'] = checked_sections
    if not checked_sections:
        result['issues'].append('Не проверено ни одного непустого сечения')
    result['checked'] = checked_sections > 0 and all(s['checked'] or s['skipped'] for s in result['sections'])
    result['passed'] = result['checked'] and not result['issues'] and all(s['passed'] for s in result['sections'])
    return result
