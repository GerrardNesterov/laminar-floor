"""Hydraulic surface screening, not room CFD.

Units at the API boundary: polygons/length in mm; face velocity in m/s.
The common-pressure network conserves volume flow. A spatial extraction map
integrates channel flow over fixed inspection regions of the actual panel.
Uniform-flow *pressure demand* is a diagnostic, not a solved pressure field.

Darcy/Haaland reference:
https://doc.comsol.com/5.6/doc/com.comsol.help.pipe/PipeFlowModuleUsersGuide.pdf
Pipe transition limits are not a criterion for room/jet laminarity:
https://swh.princeton.edu/~asmits/Bicycle_web/transition.html
"""
import math
import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union

RHO = 1.204
MU = 1.81e-5
# Fixed approximate sharp-entry + exit loss, not a user tuning parameter.
ENTRY_EXIT_K = 1.5


def _pressure(speed, diameter, length):
    """Monotone smooth-wall Darcy loss with an explicit transition interpolation."""
    speed = np.asarray(speed, dtype=float)
    re = RHO * speed * diameter / MU
    laminar = 32 * MU * length / diameter**2 * speed
    # The turbulent law is used only above Re=2300; no log(0).
    turbulent_re = np.maximum(re, 2300.)
    friction = (-1.8 * np.log10(6.9 / turbulent_re))**-2
    turbulent = friction * length / diameter * RHO * speed**2 / 2
    blend = np.clip((re - 2300.) / 1700., 0., 1.)
    viscous = (1-blend)*laminar + blend*turbulent
    return viscous + ENTRY_EXIT_K * RHO * speed**2 / 2


def _speeds(pressure, diameter, length):
    if pressure == 0:
        return np.zeros_like(diameter)
    linear=32*MU*length/diameter**2
    quadratic=ENTRY_EXIT_K*RHO/2
    analytic=2*pressure/(linear+np.sqrt(linear**2+4*quadratic*pressure))
    laminar=RHO*analytic*diameter/MU<2300
    if np.all(laminar):
        return analytic
    # Entrance/exit loss is a lower bound on total pressure loss.
    lo = np.zeros_like(diameter)
    hi = np.full_like(diameter, math.sqrt(2*pressure/(RHO*ENTRY_EXIT_K)))
    for _ in range(42):
        mid = (lo+hi)/2
        below = _pressure(mid, diameter, length) < pressure
        lo = np.where(below, mid, lo)
        hi = np.where(below, hi, mid)
    return np.where(laminar,analytic,(lo+hi)/2)


def _solve(areas, diameters, length, target_flow):
    if target_flow == 0:
        return 0., np.zeros_like(diameters)
    mean_speed = target_flow / areas.sum()
    lo = 0.
    # At this pressure each channel is at least as fast as the required mean.
    hi = float(np.max(_pressure(np.full_like(diameters, mean_speed), diameters, length)))
    for _ in range(52):
        mid = (lo+hi)/2
        if np.dot(areas, _speeds(mid, diameters, length)) < target_flow:
            lo = mid
        else:
            hi = mid
    pressure = (lo+hi)/2
    return pressure, _speeds(pressure, diameters, length)


def _cv(values, weights):
    mean = float(np.average(values, weights=weights))
    if mean == 0:
        return 0.
    return float(np.sqrt(np.average((values-mean)**2, weights=weights))/mean)


def _spatial_map(holes, speed, diameter, domain, velocity, length):
    """Exact geometric integration within 6x6 cells; sampling resolution is stated."""
    x0,y0,x1,y1 = domain.bounds
    records, face_speeds, cell_areas, demands, demand_areas = [], [], [], [], []
    for iy in range(6):
        for ix in range(6):
            zone = domain.intersection(box(x0+(x1-x0)*ix/6, y0+(y1-y0)*iy/6,
                                           x0+(x1-x0)*(ix+1)/6, y0+(y1-y0)*(iy+1)/6))
            if zone.area <= 1e-10:
                continue
            overlap = np.array([p.intersection(zone).area for p in holes])*1e-6
            local_flow = float(np.dot(overlap,speed))
            zone_area = zone.area*1e-6
            local_speed = local_flow/zone_area
            nonzero = overlap > 0
            if np.any(nonzero):
                # These are fractions of the original channels, not narrower
                # hydraulic channels: retain each original hydraulic diameter.
                demand,_ = _solve(overlap[nonzero],diameter[nonzero],length,velocity*zone_area)
                demands.append(demand)
                demand_areas.append(zone_area)
            else:
                demand = 0. if velocity == 0 else None
            center = zone.representative_point()
            records.append(dict(center_mm=[center.x,center.y], area_mm2=zone.area,
                                open_fraction=float(overlap.sum()/zone_area),
                                flow_m3_s=local_flow, superficial_speed_m_s=local_speed,
                                uniform_flow_pressure_demand_pa=demand,
                                blocked=not bool(np.any(nonzero))))
            cell_areas.append(zone_area)
            face_speeds.append(local_speed)
    coefficient = _cv(np.array(face_speeds),np.array(cell_areas))
    blocked_area = sum(r['area_mm2'] for r in records if r['blocked'])
    return dict(grid=[6,6], cells=records,
                superficial_speed_cv=coefficient, extraction_uniformity=max(0.,1-coefficient),
                blocked_area_fraction=blocked_area/domain.area,
                integrated_flow_m3_s=sum(r['flow_m3_s'] for r in records),
                pressure_demand_range_pa=[min(demands),max(demands)] if demands else None,
                pressure_demand_cv=_cv(np.asarray(demands),np.asarray(demand_areas)) if demands else None,
                uniform_target_extraction_possible=bool(velocity==0 or blocked_area==0),
                pressure_demand_interpretation='Разный перепад, который потребовался бы каждой зоне для одинаковой скорости отсоса; не фактическое поле статического давления.')


def evaluate(holes, area, velocity, thickness, *, domain=None):
    """Evaluate hydraulic loss and jet/extraction proxies, optionally spatially.

    ``thickness`` is the hydraulic path length represented by the analyzed
    working surface. No unknown room/plenum dimensions are invented.
    ``domain`` enables area-weighted spatial extraction diagnostics; without it
    channel-centroid mapping is still supplied, but no room outline is inferred.
    """
    for name,value,positive in [('area',area,True),('velocity',velocity,False),('thickness',thickness,False)]:
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or (value<=0 if positive else value<0):
            raise ValueError(f'{name}: некорректное значение аэродинамического расчёта')
    holes = list(holes)
    if domain is not None:
        if domain.is_empty or not domain.is_valid or domain.geom_type not in ('Polygon','MultiPolygon'):
            raise ValueError('Некорректная область аэродинамического расчёта')
        if not math.isclose(domain.area,area,rel_tol=1e-6,abs_tol=1e-6):
            raise ValueError('Площадь панели не совпадает с областью расчёта')
    conditions = dict(temperature_c=20., atmospheric_pressure_pa=101325.,
                      density_kg_m3=RHO, dynamic_viscosity_pa_s=MU)
    if not holes:
        return dict(pressure_pa=0. if velocity==0 else None, open_fraction=0., uniformity=None,
                    reynolds_max=0., channel_speeds_m_s=[], channel_map=[], spatial=None,
                    target_flow_m3_s=velocity*area*1e-6, actual_flow_m3_s=0.,
                    flow_residual_m3_s=-velocity*area*1e-6, conditions=conditions,
                    turbulence='Нет открытых каналов', valid=velocity==0,
                    warnings=[] if velocity==0 else ['Ненулевой расход через закрытую поверхность невозможен'])
    for p in holes:
        if p.geom_type!='Polygon' or not p.is_valid or p.is_empty or p.area<=0 or p.length<=0:
            raise ValueError('Воздушные каналы должны быть корректными непустыми полигонами')
        if domain is not None and p.difference(domain).area > max(1e-6, p.area*1e-8):
            raise ValueError('Воздушный канал выходит за границу панели')
    summed_area=sum(p.area for p in holes)
    if summed_area > area+max(1e-6,area*1e-8):
        raise ValueError('Открытая площадь превышает площадь панели')
    if abs(unary_union(holes).area-summed_area) > max(1e-6,summed_area*1e-8):
        raise ValueError('Воздушные каналы перекрываются')
    a = np.array([p.area for p in holes])*1e-6
    d = np.array([4*p.area/p.length for p in holes])*1e-3
    length = thickness*1e-3
    phi = float(a.sum()/(area*1e-6))
    flow = velocity*area*1e-6
    pressure,speed = _solve(a,d,length,flow)
    re = RHO*speed*d/MU
    dynamic = RHO*speed**2/2
    cv = _cv(speed,a)
    volume_weights=a*speed
    actual_flow=float(volume_weights.sum())
    high_fraction=float(volume_weights[re>=2300].sum()/actual_flow) if actual_flow else 0.
    jet_power=float(np.dot(volume_weights,dynamic))
    warnings = [
        'Общий перепад принят одинаковым для всех каналов; реальное давление помещения и пленума не решается.',
        'Гидравлический диаметр и гладкие стенки — приближение нерегулярных FDM-каналов; шероховатость и форма входа не откалиброваны.',
        'Пороговые числа Рейнольдса относятся к внутреннему течению. Ламинарность помещения и интенсивность турбулентности из них не следуют.'
    ]
    if np.any(re>=2300):
        warnings.append('Есть переходные/турбулентные каналы по трубной оценке; точность потерь и струй требует CFD либо измерений.')
    if np.any(speed>34.3):
        warnings.append('Скорость в каналах выше 0.1 Маха; предположение о несжимаемом воздухе требует проверки.')
    channels=[]
    for p,u,r,q,diameter in zip(holes,speed,re,dynamic,d):
        center=p.centroid
        regime='нулевой расход' if u==0 else ('ламинарная трубная оценка' if r<2300 else ('переходная трубная оценка' if r<4000 else 'турбулентная трубная оценка'))
        channels.append(dict(center_mm=[center.x,center.y],area_mm2=p.area,hydraulic_diameter_mm=float(diameter*1000),
                             speed_m_s=float(u),reynolds=float(r),dynamic_pressure_pa=float(q),
                             flow_m3_s=float(p.area*1e-6*u),regime=regime))
    spatial = _spatial_map(holes,speed,d,domain,velocity,length) if domain is not None else None
    return dict(pressure_pa=float(pressure),open_fraction=phi,uniformity=max(0.,1-cv),
                channel_speed_cv=cv,reynolds_max=float(max(re)),channel_speeds_m_s=speed.tolist(),
                channel_map=channels,spatial=spatial,conditions=conditions,
                target_flow_m3_s=flow,actual_flow_m3_s=actual_flow,flow_residual_m3_s=actual_flow-flow,
                dynamic_pressure_range_pa=[float(min(dynamic)),float(max(dynamic))],
                hydraulic_resistance_pa_s_m3=float(pressure/flow) if flow else None,
                loss_power_w=float(pressure*flow),
                turbulence=dict(proxy='Струйная кинетическая мощность и режим по гидравлическому Re; не интенсивность турбулентности',
                                jet_kinetic_power_w=jet_power,transition_or_turbulent_flow_fraction=high_fraction,
                                intensity=None,room_laminarity=None),
                actual_static_pressure_uniformity=None,
                model='Параллельные каналы Darcy: ламинарный 64/Re, переход 2300–4000, гладкостенный Haaland; K входа/выхода=1.5',
                valid=True,warnings=warnings)
