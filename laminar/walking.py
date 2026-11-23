"""Local geometric contact screening in mm; no invented friction coefficients.

The heel patch (70 x 50 mm) and equipment pad (diameter 30 mm) are explicit
screening assumptions. Wheel footprint length is the chord at one print-layer
geometric sink, not a prediction of elastic tire deformation. Sampling is
bounded and cannot prove a continuous worst-case envelope.
"""
from __future__ import annotations

import math
import numpy as np
from shapely import maximum_inscribed_circle
from shapely.affinity import rotate, translate
from shapely.geometry import Point, LineString, box
from shapely.ops import unary_union


def _polygons(shape):
    if shape.is_empty:
        return []
    if shape.geom_type == 'Polygon':
        return [shape]
    if hasattr(shape, 'geoms'):
        return [p for g in shape.geoms for p in _polygons(g)]
    return []


def _lines(shape):
    if shape.is_empty:
        return []
    if shape.geom_type in ('LineString', 'LinearRing'):
        return [shape]
    if hasattr(shape, 'geoms'):
        return [p for g in shape.geoms for p in _lines(g)]
    return []


def _patch(cfg):
    if cfg.load_type == 'wheel':
        radius = cfg.wheel_diameter / 2
        sink = min(cfg.layer, radius * .1)
        chord = 2 * math.sqrt(max(0., 2*radius*sink-sink*sink))
        return box(-chord/2, -cfg.wheel_width/2, chord/2, cfg.wheel_width/2), {
            'kind': 'wheel', 'length_mm': chord, 'width_mm': cfg.wheel_width,
            'geometric_sink_mm': sink,
            'assumption': 'Хорда жёсткого колеса при погружении на один слой; не упругая контактная площадка',
        }
    if cfg.load_type == 'equipment':
        return Point(0, 0).buffer(15, quad_segs=24), {
            'kind': 'equipment', 'diameter_mm': 30., 'assumption': 'Расчётная круглая площадка Ø30 мм',
        }
    if cfg.load_type == 'point':
        radius = max(cfg.extrusion, 1.)
        return Point(0, 0).buffer(radius, quad_segs=16), {
            'kind': 'point', 'diameter_mm': 2*radius,
            'assumption': 'Малая контактная площадка; идеальная математическая точка не имеет площади',
        }
    return box(-35, -25, 35, 25), {
        'kind': 'heel', 'length_mm': 70., 'width_mm': 50.,
        'assumption': 'Расчётная область пятки 70×50 мм; геометрия конкретной обуви не задана',
    }


def sample_positions(domain, surface, limit=40):
    """Interior raster plus centers of the largest actual openings, deterministic."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('Число позиций должно быть целым от 1 до 100')
    x0, y0, x1, y1 = domain.bounds
    positions = [domain.representative_point().coords[0]]
    holes = sorted(_polygons(domain.difference(surface)), key=lambda p: p.area, reverse=True)
    for hole in holes[:12]:
        center = maximum_inscribed_circle(hole, tolerance=.02).coords[0]
        positions.append(center)
    for fy in np.linspace(.05, .95, 5):
        for fx in np.linspace(.05, .95, 5):
            p = (x0 + fx*(x1-x0), y0 + fy*(y1-y0))
            if domain.covers(Point(p)):
                positions.append(p)
    unique = []
    for p in positions:
        xy = [float(p[0]), float(p[1])]
        if not any(np.linalg.norm(np.subtract(xy, old)) < 1e-5 for old in unique):
            unique.append(xy)
        if len(unique) == limit:
            break
    return unique


def _projection(shape):
    """Projection intervals of polygonal contact on the local rolling X axis."""
    intervals = sorted((p.bounds[0], p.bounds[2]) for p in _polygons(shape))
    merged = []
    for left, right in intervals:
        if merged and left <= merged[-1][1]+1e-8:
            merged[-1][1] = max(merged[-1][1], right)
        else:
            merged.append([left, right])
    return merged


def _rolling_gap(domain, surface, position, angle, cfg):
    radius = cfg.wheel_diameter/2
    # Work in local coordinates, x is the rolling direction.
    local_domain = rotate(translate(domain, xoff=-position[0], yoff=-position[1]), -angle, origin=(0, 0))
    local_surface = rotate(translate(surface, xoff=-position[0], yoff=-position[1]), -angle, origin=(0, 0))
    band = box(-radius, -cfg.wheel_width/2, radius, cfg.wheel_width/2)
    on_panel = local_domain.intersection(band)
    contact = local_surface.intersection(band)
    domain_intervals = _projection(on_panel)
    supported = _projection(contact)
    max_gap = 0.
    for left, right in domain_intervals:
        cursor = left
        for a, b in supported:
            if b <= left or a >= right:
                continue
            max_gap = max(max_gap, max(0., min(a, right)-cursor))
            cursor = max(cursor, min(b, right))
        max_gap = max(max_gap, right-cursor)
    # Sagitta only describes bridging geometry when gap <= diameter.
    sagitta = radius-math.sqrt(max(0., radius**2-(max_gap/2)**2)) if max_gap <= 2*radius else None
    return float(max_gap), float(sagitta) if sagitta is not None else None


def _contact(domain, surface, patch, position, angle, cfg):
    placed = translate(rotate(patch, angle, origin=(0, 0)), xoff=position[0], yoff=position[1])
    on_panel = domain.intersection(placed)
    actual = surface.intersection(on_panel)
    area = float(actual.area)
    fraction = area/on_panel.area if on_panel.area > 1e-12 else 0.
    center = Point(position)
    hull = actual.convex_hull
    stable = not hull.is_empty and hull.area > 1e-10 and hull.buffer(1e-8).covers(center)
    if hull.is_empty:
        margin = None
        offset = None
        resultant = None
    else:
        margin = center.distance(hull.boundary) if stable else -center.distance(hull)
        offset = center.distance(actual.centroid)
        resultant = [float(actual.centroid.x), float(actual.centroid.y)]
    missing = on_panel.difference(actual)
    largest_void = max((2*maximum_inscribed_circle(p, tolerance=.02).length+.04
                        for p in _polygons(missing)), default=0.)
    row = dict(position_mm=list(position), angle_deg=float(angle),
               contact_area_mm2=area, contact_fraction=float(fraction),
               on_panel_area_mm2=float(on_panel.area), footprint_area_mm2=float(patch.area),
               covered_by_panel_fraction=float(on_panel.area/patch.area),
               stable_resultant=bool(stable), stability_margin_mm=float(margin) if margin is not None else None,
               uniform_pressure_centroid_mm=resultant,
               centroid_offset_mm=float(offset) if offset is not None else None,
               mean_pressure_mpa=float(cfg.load/area) if area > 1e-12 else None,
               max_local_void_diameter_mm=float(largest_void))
    if cfg.load_type == 'wheel':
        gap, sagitta = _rolling_gap(domain, surface, position, angle, cfg)
        row.update(unsupported_rolling_span_mm=gap, bridging_sagitta_mm=sagitta)
    return row


def _edge_indicators(domain, surface):
    """Direction-sensitive edge length, not a material coefficient of friction."""
    holes = _polygons(domain.difference(surface))
    edges = unary_union([p.boundary for p in holes]).difference(domain.boundary) if holes else LineString()
    transverse_x = transverse_y = 0.
    for line in _lines(edges):
        xy = np.asarray(line.coords)
        for dx, dy in np.diff(xy, axis=0):
            transverse_x += abs(float(dy))
            transverse_y += abs(float(dx))
    maximum = max(transverse_x, transverse_y)
    minimum = min(transverse_x, transverse_y)
    return dict(open_area_fraction=float(sum(p.area for p in holes)/domain.area),
                drainage_open_area_mm2=float(sum(p.area for p in holes)),
                internal_edge_length_mm=float(edges.length),
                edge_density_x_per_mm=float(transverse_x/domain.area),
                edge_density_y_per_mm=float(transverse_y/domain.area),
                edge_direction_balance=float(minimum/maximum) if maximum else None,
                edge_direction_balance_description='1 — одинаковая плотность пересекаемых кромок по X/Y; не коэффициент трения',
                drainage_description='Только геометрическая площадь сквозных отверстий; гидравлический расход воды не рассчитан')


def evaluate(domain, surface, cfg, *, measured_friction=None):
    """Return JSON-safe bounded local contact screening (at most 80 poses).

    measured_friction must be a nonnegative experimentally established coefficient
    for the actual shoe/wheel/material/contamination condition. It is never inferred
    from edge density. Its provenance remains the caller's responsibility.
    """
    if domain.geom_type != 'Polygon' or not domain.is_valid or domain.area <= 0:
        raise ValueError('Нужен корректный контур панели')
    if not surface.is_valid:
        raise ValueError('Некорректная контактная поверхность')
    cfg.validate()
    if measured_friction is not None and (isinstance(measured_friction, bool)
            or not isinstance(measured_friction, (int, float))
            or not math.isfinite(measured_friction) or measured_friction < 0):
        raise ValueError('Измеренный коэффициент трения должен быть конечным неотрицательным числом')
    surface = surface.intersection(domain)
    patch, assumptions = _patch(cfg)
    positions = sample_positions(domain, surface)
    angles = [0., 90.] if cfg.load_type in ('wheel', 'person', 'distributed') else [0.]
    samples = [_contact(domain, surface, patch, p, angle, cfg) for p in positions for angle in angles]
    samples.sort(key=lambda s: (s['stable_resultant'], s['contact_fraction'],
                               -(s.get('unsupported_rolling_span_mm', 0.)),
                               -(s['centroid_offset_mm'] or 0.)))
    critical = []
    for sample in samples:
        if not any(sample['position_mm'] == row['position_mm'] for row in critical):
            critical.append(sample)
    worst = samples[0]
    errors = []
    if worst['contact_fraction'] < cfg.min_contact:
        errors.append('Локальная контактная доля ниже заданного минимума')
    if not all(s['stable_resultant'] for s in samples):
        errors.append('В части положений результирующая нагрузки вне опорного контура контакта')
    max_span = max((s.get('unsupported_rolling_span_mm', 0.) for s in samples), default=0.)
    if cfg.load_type == 'wheel' and max_span > min(cfg.max_opening, cfg.wheel_diameter*.2)+1e-8:
        errors.append('Колесо встречает слишком большой неподдержанный пролёт по траектории')
    supported = all(s['stable_resultant'] for s in samples)
    grip = dict(coefficient=measured_friction,
                friction_capacity_n=float(measured_friction*cfg.load) if measured_friction is not None and supported else None,
                capacity_condition='μ × N применимо только при устойчивом контакте во всех проверенных положениях',
                status='Измеренный коэффициент задан; применимость условий испытания проверяет пользователь'
                       if measured_friction is not None else 'Не подтверждено: требуется измеренный коэффициент для конкретной поверхности и условий',
                geometric_indicators=_edge_indicators(domain, surface))
    return dict(contact_fraction=float(surface.area/domain.area),
                min_local_contact_fraction=float(min(s['contact_fraction'] for s in samples)),
                max_local_void_diameter_mm=float(max(s['max_local_void_diameter_mm'] for s in samples)),
                min_stability_margin_mm=min((s['stability_margin_mm'] for s in samples if s['stability_margin_mm'] is not None), default=None),
                all_sampled_resultants_supported=all(s['stable_resultant'] for s in samples),
                max_unsupported_rolling_span_mm=float(max_span) if cfg.load_type == 'wheel' else None,
                sample_count=len(samples), footprint=assumptions, worst_case=worst,
                critical_samples=critical, errors=errors, grip=grip,
                limitations=['Дискретная проверка положений и двух направлений не является непрерывной огибающей.',
                             'Давление вычислено для всей заданной нагрузки на площадку; распределение упругих контактных давлений не моделируется.',
                             'Часть площадки за пределами панели не учитывает соседние панели; в отчёте указана доля площадки на панели.',
                             'Геометрические кромки и дренаж сами по себе не доказывают сопротивление скольжению.'])


def critical_positions(domain, surface, cfg, limit=12):
    """Distinct most adverse local contact locations for a mechanical load envelope."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError('Число критических позиций должно быть целым от 1 до 100')
    report = evaluate(domain, surface, cfg)
    result = []
    for row in report['critical_samples']:
        p = row['position_mm']
        if p not in result:
            result.append(p)
        if len(result) == limit:
            break
    return result
