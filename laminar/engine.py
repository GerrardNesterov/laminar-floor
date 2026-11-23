from dataclasses import dataclass, field
import json
import numpy as np
from shapely.geometry import box, Point, LineString
from shapely.ops import unary_union
from . import geometry as g
from .mechanics import solve, solve_cases, envelope, MATERIALS
from .profile import prepare, seat_depth, contact_patch
from .air import evaluate
from . import topology, surface as skin, walking
from .contact import bearing_envelope
from .audit import mesh_self_intersections, solid_sections


@dataclass
class Result:
    mesh: object
    report: dict
    nodes: object
    edges: object
    mechanics: dict
    invalid_faces: object
    _validated_stl: bytes | None = field(default=None,init=False,repr=False)

    def stl(self):
        """Serialize and attach lazy output diagnostics; quality never blocks bytes."""
        if self._validated_stl is not None:
            return self._validated_stl
        if self.mesh is None or not len(self.mesh.vertices) or not len(self.mesh.faces):
            raise ValueError('Нет геометрии для экспорта STL')
        if not np.isfinite(self.mesh.vertices).all():
            raise ValueError('Геометрия содержит неконечные координаты; запись STL невозможна')
        # Engineering/mesh findings remain in report.errors and are advisory.
        # Serialization failures are technical errors, not quality decisions.
        data=self.mesh.export(file_type='stl')
        import io
        import trimesh
        def audit_bytes(payload):
            findings=[]
            try:
                exported=trimesh.load(io.BytesIO(payload),file_type='stl')
                if not exported.is_watertight or not exported.is_volume or len(exported.split())!=1:
                    findings.append('Экспортированный STL имеет проблемы целостности или связности')
                if not np.allclose(exported.bounds,self.mesh.bounds,rtol=0,atol=.02):
                    findings.append('Округление STL изменило габариты модели')
                bad=(exported.face_normals[:,2] < -np.sin(np.deg2rad(40))-1e-5) & (exported.triangles_center[:,2]>.02)
                if np.any(bad):findings.append('Экспортированный STL имеет нависания более 40° от вертикали')
                audit=mesh_self_intersections(exported)
                findings.extend(audit['issues'])
            except Exception as exc:
                findings.append('Проверка записанного STL не выполнена: '+str(exc))
            return findings
        findings=audit_bytes(data)
        if findings:
            # Preserve the existing precision recovery for tiny boolean edges.
            lines=['solid panel']
            for normal,triangle in zip(self.mesh.face_normals,self.mesh.triangles):
                lines.append('facet normal '+' '.join(format(float(v),'.17g') for v in normal))
                lines.append('outer loop')
                lines.extend('vertex '+' '.join(format(float(v),'.17g') for v in vertex) for vertex in triangle)
                lines.extend(['endloop','endfacet'])
            lines.append('endsolid panel')
            precise=('\n'.join(lines)+'\n').encode('ascii')
            precise_findings=audit_bytes(precise)
            if len(precise_findings)<=len(findings):data,findings=precise,precise_findings
        self.report['export_checks']={'issues':findings,'recommended':not findings and not self.report['errors']}
        self.report['errors'].extend(message for message in findings if message not in self.report['errors'])
        self._validated_stl=data
        return data

    def report_json(self):
        return json.dumps(self.report,ensure_ascii=False,indent=2,allow_nan=False)


def _quantize_heights(heights, available, layer):
    """Use at most 40 printable heights, before both mechanics and geometry.

    Keep the four-layer minimum and the greatest complete layer below the
    available height. Upward sizing remains bounded by that maximum; no audit
    plane is discarded to meet the independent section checker's limit.
    """
    maximum = int(np.floor(available/layer+1e-10))
    if maximum < 4:
        raise ValueError('Недостаточно высоты для четырёх слоёв печати')
    requested = np.ceil(np.asarray(heights)/layer-1e-10).astype(np.int64)
    # Preserve the thinnest requested surface member even on very tall panels.
    # One minimum plus up to 39 evenly spaced complete-layer heights is <=40.
    skin_layer = int(np.clip(requested.min(), 4, maximum))
    step = max(1, int(np.ceil((maximum-skin_layer)/38)))
    allowed = np.unique(np.r_[4, np.arange(skin_layer, maximum+1, step), maximum])
    indices = np.minimum(np.searchsorted(allowed, requested), len(allowed)-1)
    return allowed[indices]*layer


def _section_levels(heights, layer, available, embed):
    """Inspect every actual rib transition plus the base and a seat section."""
    return sorted(set([0.]+[float(h)-min(layer/4,.01) for h in heights]
                      +[available+embed/2]))


def _candidate(cfg, domain=None, supports=None):
    cfg.validate()
    if not cfg.profile_points:
        raise ValueError('Импортируйте DXF-профиль башмака')
    profile=prepare(cfg.profile_points,cfg.top_edge,cfg.profile_name,axis_side=cfg.axis_side)
    domain=box(0,0,cfg.width,cfg.depth) if domain is None else domain
    if not domain.is_valid or domain.geom_type!='Polygon':
        raise ValueError('Панель должна быть одним простым контуром')
    supports = supports if supports is not None else (cfg.supports or list(domain.exterior.coords)[:-1])
    supports=np.asarray(supports,dtype=float)
    if supports.ndim != 2 or supports.shape[1]!=2 or len(supports)<3 or not np.isfinite(supports).all():
        raise ValueError('Нужны минимум три корректные опоры')
    if any(not domain.buffer(1e-5).covers(Point(p)) for p in supports):
        raise ValueError('Опоры должны располагаться на контуре или внутри панели')
    for i,position in enumerate(supports):
        if any(np.linalg.norm(position-other)<2*profile.radius-1e-5 for other in supports[i+1:]):
            raise ValueError('Опорные башмаки пересекаются: измените их позиции или профиль')
    available=cfg.max_thickness
    min_width=cfg.extrusion*cfg.perimeters
    if domain.buffer(-min_width).is_empty:
        raise ValueError('Контур панели слишком узкий для сопла и количества периметров')
    if available < max(4*cfg.layer, 2*min_width):
        raise ValueError('Недостаточная номинальная толщина для печатаемой несущей структуры')
    embed=seat_depth(profile,available)
    foot_patches=[contact_patch(profile,s,domain) for s in supports]
    pad_radius=profile.radius+min_width+cfg.outer_flange
    foot_shadow=unary_union([Point(position).buffer(profile.radius,quad_segs=48) for position in supports]).intersection(domain)
    support_zones=[Point(position).buffer(pad_radius,quad_segs=48).intersection(domain) for position in supports]
    clearance_zones=[zone.difference(foot_shadow) for zone in support_zones]
    def clearance_open(surface):
        return [zone.difference(surface).area/max(zone.area,1e-12) for zone in clearance_zones]
    merged_flange_pairs=[[i,j] for i in range(len(supports)) for j in range(i+1,len(supports))
                         if np.linalg.norm(supports[i]-supports[j]) < 2*pad_radius-1e-5]
    top_slice=profile.polygon.intersection(LineString([(-1,profile.height-1e-6),(profile.radius+1,profile.height-1e-6)]))
    contact_radius=top_slice.bounds[2]
    spacing=min(cfg.max_opening*1.5,min(domain.bounds[2]-domain.bounds[0],domain.bounds[3]-domain.bounds[1])/2)
    coarse_nodes,coarse_edges=topology.network(domain,supports,spacing,cfg.seed)
    center=domain.representative_point().coords[0]
    def topology_score(nn,ee):
        factors=g.physical_widths(domain,nn,ee,np.ones(len(ee)))
        nominal=np.full(len(ee),min_width*1.5)/factors
        hh=np.full(len(ee),available*.8)
        mechanics=solve(nn,ee,nominal*factors,hh,supports,cfg,center,domain=domain)
        ss,pp,foot=g.footprints(domain,nn,ee,nominal,supports,pad_radius,max(cfg.nozzle,cfg.extrusion))
        holes,_=g.opening_stats(domain,foot)
        airflow=evaluate(g.polygons(domain.difference(foot.union(foot_shadow))),domain.area,cfg.air_speed,available)
        pressure=airflow['pressure_pa'] if airflow['pressure_pa'] is not None else 1e4
        volume=sum(LineString(nn[e]).length*w*h for e,w,h in zip(ee,nominal*factors,hh))
        return dict(score=float(volume*(1+4*max(0,mechanics['utilization']-1))*(1+pressure/5)),
                    edge_priority=mechanics['member_utilization'],node_priority=np.abs(mechanics['displacement']))
    search=topology.optimize(domain,coarse_nodes,coarse_edges,supports,spacing,cfg.seed,topology_score,
                             rounds=2,max_evaluations=7)
    nodes,edges,structural,skin_audit=skin.refine(domain,search.nodes,search.edges,supports,min_width,
                                               cfg.max_opening,pad_radius)
    structural_graph=unary_union([LineString(nodes[edge]) for edge,is_structural in zip(edges,structural) if is_structural]).buffer(2e-6)
    nodes,edges=topology.seat_nodes(domain,nodes,edges,supports,foot_patches)
    structural=np.array([structural_graph.covers(LineString(nodes[edge])) for edge in edges])
    factors=g.physical_widths(domain,nodes,edges,np.ones(len(edges)))
    middle=nodes[edges].mean(axis=1)
    near=np.min(np.linalg.norm(middle[:,None,:]-supports[None,:,:],axis=2),axis=1)
    field=np.exp(-near/max(spacing*3,1))
    widths=min_width*(1+0.5*field*structural)/factors
    skin_height=min(available,max(4*cfg.layer,min_width*1.5))
    heights=np.where(structural,available*(0.6+0.4*field),skin_height)
    heights=_quantize_heights(heights,available,cfg.layer)
    history=[]
    bearing_only_reference=None
    stop_reason='iteration_limit'
    candidates=[center]
    if cfg.load_type!='distributed':
        # A spatial envelope plus actual least-supported surface positions.
        candidates=walking.sample_positions(domain,domain,limit=26)
    for iteration in range(16):
        strips,pads,surface=g.footprints(domain,nodes,edges,widths,supports,pad_radius,max(cfg.nozzle,cfg.extrusion))
        holes,opening=g.opening_stats(domain,surface)
        contact_patches=[patch.intersection(pads[i]) for i,patch in enumerate(foot_patches)]
        walk=walking.evaluate(domain,surface,cfg,measured_friction=cfg.friction_coefficient)
        if cfg.load_type!='distributed':
            extra=[row['position_mm'] for row in walk['critical_samples'][:4]]
            positions=candidates+extra
        else: positions=candidates
        cases=solve_cases(nodes,edges,widths*factors,heights,supports,cfg,positions,support_radius=contact_radius,domain=domain,support_patches=contact_patches)
        mech=envelope(cases,positions)
        contact=surface.area/domain.area
        bearing_trial=bearing_envelope(domain,nodes,supports,contact_radius,cases,patches=contact_patches)
        bearing_ratio=max(bearing_trial['peak_cell_pressure_mpa'])/mech['allowable_z_mpa']
        history.append(dict(iteration=iteration+1,utilization=float(mech['utilization']),contact=contact,
                            local_contact=walk['min_local_contact_fraction'],max_opening=opening,bearing_ratio=bearing_ratio))
        recent_bearing=[row['bearing_ratio'] for row in history[-3:]]
        bearing_stalled=(len(recent_bearing)==3 and bearing_ratio>1
                         and max(recent_bearing)-min(recent_bearing)<=1e-5*max(recent_bearing))
        if bearing_stalled:
            history[-1]['bearing_adjustment']='Остановлено: изменение рёбер не снижает давление на готовой опоре'
        other_constraints_pass=mech['utilization']<=1 and not walk['errors'] and opening<=cfg.max_opening
        if other_constraints_pass and bearing_ratio<=1:
            stop_reason='constraints_satisfied'
            break
        if other_constraints_pass and bearing_stalled:
            stop_reason='fixed_foot_bearing_limit'
            break
        # A ready-made foot has a fixed bearing area. Thickening every rib can
        # leave a direct-contact pressure limit unchanged while closing all air
        # channels. Retain the lighter design when a bearing-only sizing trial
        # does not actually reduce the pressure violation.
        if other_constraints_pass and bearing_ratio>1:
            if bearing_only_reference is not None and bearing_ratio>=bearing_only_reference[2]*(1-1e-5):
                widths,heights,_=bearing_only_reference
                stop_reason='fixed_foot_bearing_limit'
                history[-1]['action']='Вернута предыдущая геометрия: усиление рёбер не уменьшило превышение давления на готовой опоре'
                break
            bearing_only_reference=(widths.copy(),heights.copy(),float(bearing_ratio))
        else:
            bearing_only_reference=None
        severity=np.maximum.reduce([case['member_utilization'] for case in cases])
        if mech['utilization']>1:
            heights=_quantize_heights(heights*(1.05+0.16*np.minimum(severity,2)),available,cfg.layer)
            widths*=1.05+0.16*np.minimum(severity,2)
        if bearing_ratio>1 and not bearing_stalled:
            widths*=1+0.12*field*min(bearing_ratio,3)
        if walk['errors'] or opening>cfg.max_opening:
            # Material is added near failing contact patches, not to a preset pattern.
            critical=np.asarray([row['position_mm'] for row in walk['critical_samples'][:6]])
            distance=np.min(np.linalg.norm(middle[:,None,:]-critical[None,:,:],axis=2),axis=1)
            widths*=1.04+0.18*np.exp(-distance/max(cfg.max_opening*2,1))
    # Rebuild the contact envelope for the actual final (possibly restored)
    # geometry, rather than reusing positions from a discarded sizing trial.
    strips,pads,surface=g.footprints(domain,nodes,edges,widths,supports,pad_radius,max(cfg.nozzle,cfg.extrusion))
    holes,opening=g.opening_stats(domain,surface)
    walk=walking.evaluate(domain,surface,cfg,measured_friction=cfg.friction_coefficient)
    positions=candidates+[row['position_mm'] for row in walk['critical_samples'][:4]] if cfg.load_type!='distributed' else candidates
    contact_patches=[patch.intersection(pads[i]) for i,patch in enumerate(foot_patches)]
    cases=solve_cases(nodes,edges,widths*factors,heights,supports,cfg,positions,support_radius=contact_radius,domain=domain,support_patches=contact_patches)
    mech=envelope(cases,positions)
    solid=g.assemble(domain,strips,pads,heights,profile,supports,cfg.max_thickness,embed,min_gap=max(cfg.nozzle,cfg.extrusion))
    mesh,errors,bad=g.validate_mesh(solid,domain,available+embed)
    intersections=mesh_self_intersections(mesh)
    errors.extend(intersections['issues'])
    # Cross-sections immediately below every beam-height change and within the seat.
    levels=_section_levels(heights,cfg.layer,available,embed)
    feature_audit=solid_sections(solid,domain,min_width,max(cfg.nozzle,cfg.extrusion),levels)
    errors.extend(feature_audit['issues'])
    walk=walking.evaluate(domain,surface,cfg,measured_friction=cfg.friction_coefficient)
    errors.extend(walk['errors'])
    if mech['utilization']>1: errors.append('Прочность или прогиб не удовлетворяют требованиям в балочной модели')
    if min(c['reactions_n'][i] for c in cases for i in range(len(c['reactions_n']))) < -1e-3:
        errors.append('Расчёт требует удержания от отрыва на опоре; контактная модель не подтверждена')
    if opening>cfg.max_opening: errors.append('Превышен допустимый размер отверстий')
    if surface.area/domain.area < cfg.min_contact: errors.append('Недостаточная контактная площадь')
    if not holes: errors.append('Воздушные каналы закрыты: нагрузка конфликтует с открытостью')
    air_holes=g.polygons(domain.difference(surface.union(foot_shadow)))
    support_open=[zone.difference(surface.union(foot_shadow)).area/max(zone.area,1e-12) for zone in support_zones]
    clearance=clearance_open(surface)
    if min(clearance)<.1:
        errors.append('Вокруг башмака доступно менее 10% свободного проходного сечения; параметры конфликтуют с воздухопроницаемостью')
    top_slice=profile.polygon.intersection(LineString([(-1,profile.height-1e-6),(profile.radius+1,profile.height-1e-6)]))
    contact_radius=top_slice.bounds[2]
    bearing_report=bearing_envelope(domain,nodes,supports,contact_radius,cases,patches=contact_patches)
    contact_areas=bearing_report['full_contact_area_mm2']
    bearing=bearing_report['peak_cell_pressure_mpa']
    if max(bearing)>mech['allowable_z_mpa']:
        errors.append('Локальная оценка давления на посадке превышает допустимую Z-прочность')
    air=evaluate(air_holes,domain.area,cfg.air_speed,available,domain=domain)
    air.update(path_length_mm=float(available),path_length_model='Полная доступная высота рёбер; одинаковая длина каналов в поиске и итоговой оценке',
               support_obstruction_model='Из сквозных каналов исключена полная вертикальная проекция внешних башмаков; боковое обтекание не моделируется',
               depth_limitation='Каналы постоянного сечения на полной глубине — консервативное приближение; расширение потока над тонкими перемычками и объёмные струи не решаются.')
    mechanical={k:v for k,v in mech.items() if k not in ('element_stress','displacement','member_utilization')}
    mechanical.update(load_n=cfg.load,load_type=cfg.load_type,model='Timoshenko grillage + torsion',load_positions_mm=[list(p) for p in positions],case_count=len(cases))
    report=dict(status='Предварительный расчёт; не производственная аттестация',errors=errors,
        geometry_checks=dict(self_intersections=intersections,sections=feature_audit,
            numerics=dict(max_total_interior_setback_mm=.001,max_total_width_loss_mm=.002,layer_overlap_mm=.0001,boolean_simplify_mm=.00001)),
        geometry=dict(dimensions_mm=mesh.extents.tolist(),nominal_thickness_mm=cfg.max_thickness,
                      working_plane_z_mm=0.,external_foot_top_z_mm=available,outer_flange_mm=cfg.outer_flange,
                      available_height_mm=available,volume_cm3=mesh.volume/1000,open_fraction=air['open_fraction'],surface_open_fraction=1-surface.area/domain.area,
                      blind_open_area_mm2=domain.difference(surface).intersection(foot_shadow).area,
                      max_opening_mm=opening,connected_bodies=len(solid.decompose())),
        material=dict(name=cfg.material,mmf=cfg.mmf,fos=cfg.fos,base=MATERIALS[cfg.material],effective_E_mpa=MATERIALS[cfg.material]['E']*cfg.mmf,
                      calibration='Исходные оценочные значения; нужны испытания конкретного филамента'),
        mechanics=mechanical,aerodynamics=air,
        printing=dict(nozzle_mm=cfg.nozzle,layer_mm=cfg.layer,extrusion_mm=cfg.extrusion,perimeters=cfg.perimeters,
                      min_rib_width_mm=float(min(g.physical_widths(domain,nodes,edges,widths))),overhang_limit_deg=40,orientation='Рабочая поверхность Z=0',
                      mass_g=mesh.volume/1000*MATERIALS[cfg.material]['density'],infill='Сплошные рёбра; 100%',
                      time_hours_lower_bound=mesh.volume/(cfg.extrusion*cfg.layer*40)/3600),
        supports=dict(profile=profile.name,count=len(supports),positions_mm=supports.tolist(),preview_radius_mm=pad_radius,merged_flange_pairs=merged_flange_pairs,
                      axis_side=cfg.axis_side,axis_original=profile.axis_original,open_fraction=support_open,clearance_open_fraction=clearance,
                      blocked_projection_area_mm2=foot_shadow.area,
                      bottom_original=profile.bottom_original,profile_height_mm=profile.height,seat_depth_mm=embed,
                      contact_area_mm2=contact_areas,bearing_pressure_mpa=bearing,contact_pressure=bearing_report),
        walking=walk,topology_optimization=search.history,working_surface=skin_audit,
        optimization=history,sizing_stop_reason=stop_reason,
        limitations=['Балочная модель коротких рёбер не заменяет объёмную: концентраторы, ползучесть, усталость и пространственная устойчивость не подтверждены.',
                     'Среднее давление на посадке проверено; локальная контактная прочность и концентраторы не подтверждены.',
                     'Проверена пространственная сетка нагрузок и критические контактные положения; разрешение дискретизации указано в отчёте.',
                     'Воздух: гидравлическая модель каналов и карта отсоса; фактическая турбулентная интенсивность требует CFD/измерений. Трение — только по введённому измеренному коэффициенту.',
                     'STL пригоден для прототипа. Для несущего пола нужны испытания и верификация расчётной модели.'])
    return Result(mesh,report,nodes,edges,mech,bad)


def generate(cfg, domain=None, supports=None):
    """Compare irregular layouts by material and flow loss, under hard constraints."""
    from dataclasses import replace
    cfg.validate()
    candidates=[];candidate_numbers=[];failures=[]
    for i in range(8):
        # Compare three seeds first; if all fail strict checks, search a bounded
        # five additional graphs and stop on the first fully admissible result.
        if i>=3 and any(not r.report['errors'] for r in candidates):
            break
        try:
            candidates.append(_candidate(replace(cfg,seed=(cfg.seed+i) % 2**32),domain,supports))
            candidate_numbers.append(i)
        except ValueError as exc:
            failures.append(dict(candidate=i,error=str(exc)))
    if not candidates:
        raise ValueError('; '.join(f"Вариант {f['candidate']+1}: {f['error']}" for f in failures))
    def score(result):
        r = result.report
        dp = r['aerodynamics']['pressure_pa']
        return r['geometry']['volume_cm3'] * (1 + (dp if dp is not None else 1e6)/5) * (2-(r['aerodynamics'].get('spatial') or {}).get('extraction_uniformity',0))
    viable = [r for r in candidates if not r.report['errors']]
    chosen = min(viable or candidates, key=lambda r: (len(r.report['errors']), score(r)))
    chosen.report['failed_candidates']=failures
    chosen.report['topology_search'] = [dict(candidate=candidate_numbers[i],seed=(cfg.seed+candidate_numbers[i]) % 2**32,score=score(r), errors=r.report['errors'])
                                        for i,r in enumerate(candidates)]
    chosen.report['selected_candidate'] = next(candidate_numbers[i] for i,r in enumerate(candidates) if r is chosen)
    chosen.report['selected_seed']=(cfg.seed+chosen.report['selected_candidate']) % 2**32
    chosen.report['candidate_attempt_count']=len(candidates)+len(failures)
    # Candidate ranking uses geometry/engineering checks. Audit the chosen file
    # before returning so UI and other consumers see its final diagnostics.
    chosen.stl()
    return chosen
