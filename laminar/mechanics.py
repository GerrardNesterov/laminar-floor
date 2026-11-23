"""Linear grillage FEM: vertical displacement and two rotations per node.
Timoshenko bending + Saint-Venant torsion. A screening model, not solid FEA.
"""
import warnings
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu, MatrixRankWarning

# Conservative screening defaults, NOT grade-specific certified properties.
# MPa, g/cm³. Must be calibrated with XY/Z coupons for the actual print process.
MATERIALS = {
    'PETG': dict(E=1800., xy=35., z=18., density=1.27),
    'PP': dict(E=900., xy=22., z=12., density=0.90),
    'PA6': dict(E=1200., xy=40., z=20., density=1.14),
}


def element(a,b,width,height,E):
    delta = b-a
    L = np.linalg.norm(delta)
    c,s = delta/L
    # Local axes: longitudinal rotation and transverse rotation.
    T = np.zeros((6,6))
    for start in (0,3):
        T[start,start] = 1
        T[start+1,start+1:start+3] = [c,s]
        T[start+2,start+1:start+3] = [-s,c]
    I = width*height**3/12
    k = np.zeros((6,6))
    G=E/(2*1.35)
    phi=12*E*I/((5/6)*G*width*height*L**2)
    bending = E*I/(L**3*(1+phi))*np.array([[12,6*L,-12,6*L],
        [6*L,(4+phi)*L*L,-6*L,(2-phi)*L*L],
        [-12,-6*L,12,-6*L], [6*L,(2-phi)*L*L,-6*L,(4+phi)*L*L]])
    ix = [0,2,3,5]
    k[np.ix_(ix,ix)] = bending
    small,large = sorted((width,height))
    J = large*small**3*(1/3-0.21*(small/large)*(1-small**4/(12*large**4)))
    k[np.ix_([1,4],[1,4])] = E/(2*1.35)*J/L*np.array([[1,-1],[-1,1]])
    return T.T@k@T, k, T, I, L


def tributary_areas(nodes, domain=None):
    """Nearest-node quadrature cells clipped to the actual gross panel domain.

    Cell area, not member length or valence, determines a node's share of a
    uniform load. Without an explicit domain only the node convex hull is known;
    callers with nonconvex panels or obstacles must supply the actual polygon.
    """
    from shapely import voronoi_polygons
    from shapely.geometry import MultiPoint, Point
    xy=np.asarray(nodes,dtype=float)
    if xy.ndim!=2 or xy.shape[1]!=2 or len(xy)<3 or not np.isfinite(xy).all():
        raise ValueError('Для распределённой нагрузки нужны корректные узлы плоской панели')
    if len(np.unique(xy,axis=0))!=len(xy):
        raise ValueError('Расчётные узлы распределённой нагрузки должны быть различными')
    source='Заданный контур панели' if domain is not None else 'Выпуклая оболочка узлов: контур панели не передан'
    if domain is None:
        domain=MultiPoint(xy).convex_hull
    if domain.geom_type!='Polygon' or not domain.is_valid or domain.area<=0:
        raise ValueError('Для распределённой нагрузки нужен корректный площадной контур')
    if any(not domain.buffer(1e-5).covers(Point(p)) for p in xy):
        raise ValueError('Узел распределённой нагрузки находится за контуром панели')
    cells=voronoi_polygons(MultiPoint(xy),extend_to=domain.envelope,ordered=True)
    area=np.array([cell.intersection(domain).area for cell in cells.geoms],dtype=float)
    if len(area)!=len(xy) or np.any(area<=0) or not np.isclose(area.sum(),domain.area,rtol=1e-9,atol=1e-8):
        # GEOS can return overlapping cells for almost cocircular contact nodes.
        # Break only that numerical degeneracy, without moving any FEM nodes.
        from scipy.spatial import cKDTree
        separation=float(cKDTree(xy).query(xy,k=2)[0][:,1].min())
        epsilon=min(1e-6,separation*.01)
        sites=xy+np.random.default_rng(0).uniform(-epsilon,epsilon,xy.shape)
        cells=voronoi_polygons(MultiPoint(sites),extend_to=domain.envelope,ordered=True)
        clipped=[cell.intersection(domain) for cell in cells.geoms]
        area=np.array([cell.area for cell in clipped],dtype=float)
        from shapely.ops import unary_union
        if (len(area)!=len(xy) or np.any(area<=0)
                or not np.isclose(area.sum(),domain.area,rtol=1e-9,atol=1e-8)
                or domain.difference(unary_union(clipped)).area>1e-7):
            raise ValueError('Площади узловых областей не покрывают контур панели')
        source+='; численная регуляризация центров интегрирования ≤1e-6 мм по координате'
    return area,source


def _assemble(nodes, edges, widths, heights, cfg, domain=None):
    mat = MATERIALS[cfg.material]
    E = mat['E']*cfg.mmf
    n = len(nodes)*3
    rows, cols, data, elements = [], [], [], []
    for edge,w,h in zip(edges,widths,heights):
        kg,k,T,I,L = element(*nodes[edge],w,h,E)
        dofs = np.ravel([np.arange(i*3,i*3+3) for i in edge])
        rows.extend(np.repeat(dofs,6)); cols.extend(np.tile(dofs,6)); data.extend(kg.ravel())
        elements.append((dofs,k,T,I,L))
    K = coo_matrix((data,(rows,cols)), shape=(n,n)).tocsr()
    areas,source=tributary_areas(nodes,domain) if cfg.load_type=='distributed' else (None,None)
    return dict(K=K,elements=elements,initial_factor=None,distributed_areas=areas,domain_source=source)


def _solve(nodes, edges, widths, heights, supports, cfg, load_at, support_radius, assembled, support_patches=None):
    mat=MATERIALS[cfg.material]
    E=mat['E']*cfg.mmf
    n=len(nodes)*3
    K,elements=assembled['K'],assembled['elements']
    distances=np.linalg.norm(np.asarray(nodes)[:,None,:]-np.asarray(supports)[None,:,:],axis=2)
    centers=np.argmin(distances,axis=0)
    if len(set(centers)) != len(supports):
        raise ValueError('Опоры слишком близки: совпали расчётные узлы')
    if len(supports) < 3:
        raise ValueError('Нужно минимум три различные опорные позиции')
    support_xy = nodes[centers]
    if np.linalg.matrix_rank(support_xy-support_xy.mean(axis=0)) < 2:
        raise ValueError('Все опоры лежат на одной прямой')
    if support_radius is None and support_patches is None:
        contact_nodes=np.asarray(centers,dtype=int)
        owners=np.arange(len(supports))
    else:
        if isinstance(support_radius,bool) or not np.isscalar(support_radius) or not np.isfinite(support_radius) or support_radius<=0:
            raise ValueError('Радиус контактной площадки должен быть положительным конечным числом')
        if np.any(distances[centers,np.arange(len(supports))]>1e-5):
            raise ValueError('Для площадного контакта центры опор должны быть узлами расчётной сети')
        if support_patches is not None:
            cached=assembled.get('patch_contact')
            if cached is None:
                from shapely.geometry import Point
                node_points=[Point(p) for p in nodes]
                buffered=[patch.buffer(1e-7) for patch in support_patches]
                membership=np.array([[patch.covers(p) for patch in buffered] for p in node_points])
                for owner in np.flatnonzero(~membership.any(axis=0)):
                    # seat_nodes intentionally merges intervals within 0.0001 mm
                    # of an existing node. Use that same numerical tolerance,
                    # never an arbitrary full-pad/disk constraint.
                    close=support_patches[owner].buffer(1e-4)
                    membership[:,owner]=[close.covers(p) for p in node_points]
                if not membership.any(axis=0).all():
                    raise ValueError('На реальной контактной площадке отсутствуют расчётные узлы')
                contact_nodes=np.flatnonzero(membership.any(axis=1))
                owners=np.argmin(np.where(membership[contact_nodes],distances[contact_nodes],np.inf),axis=1)
                assembled['patch_contact']=(contact_nodes,owners)
            else:
                contact_nodes,owners=cached
        else:
            contact_nodes=np.flatnonzero(np.any(distances<=support_radius+1e-8,axis=1))
            owners=np.argmin(distances[contact_nodes],axis=1)
    contact_dofs=contact_nodes*3
    fixed=contact_dofs.tolist()
    f = np.zeros(n)
    if cfg.load_type == 'distributed':
        weight=assembled['distributed_areas']
        f[::3] = -cfg.load*weight/weight.sum()
    else:
        target = np.array(load_at) if load_at is not None else nodes.mean(axis=0)
        dist = np.linalg.norm(nodes-target,axis=1)
        radius = {'point':0, 'person':35, 'equipment':15, 'wheel':cfg.wheel_width/2}[cfg.load_type]
        chosen = np.flatnonzero(dist <= radius)
        if len(chosen) == 0:
            chosen = [np.argmin(dist)]
        f[np.asarray(chosen)*3] = -cfg.load/len(chosen)
    # Rigid flat support tops at zero displacement. Compression-only contact:
    # u_z >= 0 (no penetration), reaction >= 0, u_z * reaction = 0.
    # No foot elastic modulus is assumed or inferred.
    active=np.ones(len(contact_nodes),dtype=bool)
    seen=set()
    contact_iterations=0
    tolerance_force=max(cfg.load*1e-8,1e-8)
    tolerance_gap=1e-8
    limit=1 if support_radius is None else max(16,4*len(contact_nodes)+8)
    limit=min(limit,400)
    while contact_iterations<limit:
        signature=tuple(np.flatnonzero(active))
        if signature in seen:
            raise ValueError('Контактный расчёт зациклился: требуется уточнение сетки или опор')
        seen.add(signature)
        fixed=contact_dofs[active]
        active_xy=nodes[contact_nodes[active]]
        if len(active_xy)<3 or np.linalg.matrix_rank(active_xy-active_xy.mean(axis=0))<2:
            raise ValueError('Неустойчивый односторонний контакт: панель опрокидывается или опоры расположены на одной линии')
        free=np.setdiff1d(np.arange(n),fixed)
        u=np.zeros(n)
        with warnings.catch_warnings():
            warnings.simplefilter('error', MatrixRankWarning)
            try:
                key=tuple(fixed)
                cached=assembled['initial_factor']
                if contact_iterations==0 and cached is not None and cached[0]==key:
                    factor=cached[1]
                else:
                    factor=splu(K[free][:,free].tocsc())
                    if contact_iterations==0:
                        # All load positions start from the same contact set.
                        # Keep only this one factorization, not a growing cache.
                        assembled['initial_factor']=(key,factor)
                u[free]=factor.solve(f[free])
            except (MatrixRankWarning,RuntimeError) as exc:
                raise ValueError('Неустойчивая расчётная схема') from exc
        if not np.isfinite(u).all():
            raise ValueError('Расчётная схема вырождена')
        contact_iterations+=1
        nodal_residual=K@u-f
        if support_radius is None:
            break
        penetrating=np.flatnonzero((~active)&(u[contact_dofs]<-tolerance_gap))
        if len(penetrating):
            # Reapply the most violated nonpenetration condition first.
            active[penetrating[np.argmin(u[contact_dofs[penetrating]])]]=True
            continue
        tensile=np.flatnonzero(active&(nodal_residual[contact_dofs]<-tolerance_force))
        if len(tensile):
            active[tensile[np.argmin(nodal_residual[contact_dofs[tensile]])]]=False
            continue
        break
    else:
        raise ValueError('Контактный расчёт не сошёлся в пределах допустимого числа итераций')
    stress, shear, torsion, buckling, aspect = [], [], [], [], []
    for (dofs,k,T,I,L),w,h in zip(elements,widths,heights):
        force = k@(T@u[dofs])
        stress.append(max(abs(force[2]),abs(force[5]))*h/(2*I))
        shear.append(1.5*max(abs(force[0]),abs(force[3]))/(w*h))
        small,large=sorted((w,h))
        beta=1/3-0.21*(small/large)*(1-small**4/(12*large**4))
        # T*t/J is a conservative rectangular-section shear estimate.
        torsion.append(max(abs(force[1]),abs(force[4]))/(beta*large*small**2))
        buckling.append(0.43*np.pi**2*E/(12*(1-.35**2))*(w/h)**2/cfg.fos)
        aspect.append(L/h)
    nodal_contact=np.where(active,(K@u-f)[contact_dofs],0.)
    reaction=np.bincount(owners,weights=nodal_contact,minlength=len(supports))
    resultant_positions=[]
    for owner in range(len(supports)):
        force=nodal_contact[owners==owner]
        positive=np.maximum(force,0.)
        resultant_positions.append(np.average(nodes[contact_nodes[owners==owner]],axis=0,weights=positive).tolist()
                                   if positive.sum()>tolerance_force else None)
    active_counts=np.bincount(owners[active],minlength=len(supports))
    candidate_counts=np.bincount(owners,minlength=len(supports))
    allowable = mat['xy']*cfg.mmf/cfg.fos
    allowable_z = mat['z']*cfg.mmf/cfg.fos
    residual=K@u-f
    relative_residual=float(np.linalg.norm(residual[free])/max(np.linalg.norm(f),1e-9))
    if relative_residual>1e-5:
        raise ValueError('Неустойчивый численный расчёт: невязка превышает 1e-5')
    combined=np.asarray(shear)+np.asarray(torsion)
    equivalent=np.sqrt(np.asarray(stress)**2+3*combined**2)
    member_util=np.maximum.reduce([equivalent/allowable,combined/allowable_z,
                                   np.asarray(stress)/np.asarray(buckling)])
    return dict(max_stress_mpa=float(max(stress)), allowable_xy_mpa=allowable,
                max_shear_mpa=float(max(shear)), allowable_z_mpa=allowable_z,
                deflection_mm=float(max(abs(u[::3]))), reactions_n=reaction.tolist(),
                reaction_balance_n=float(reaction.sum()-cfg.load),
                safety_factor=float(cfg.fos/max(max(member_util),1e-12)),
                max_torsional_shear_mpa=float(max(torsion)),
                max_combined_shear_mpa=float(max(combined)),
                max_equivalent_stress_mpa=float(max(equivalent)),
                min_buckling_allowable_mpa=float(min(buckling)),
                min_length_height_ratio=float(min(aspect)),
                model_warning='Короткие рёбра: балочная идеализация требует проверки объёмной моделью' if min(aspect)<2 else '',
                relative_residual=relative_residual,
                contact=dict(model='Жёсткие плоские площадки: односторонний контакт без растяжения' if support_radius is not None else 'Точечные двусторонние опоры',
                             radius_mm=float(support_radius) if support_radius is not None else None,
                             iterations=contact_iterations,converged=True,
                             candidate_node_counts=candidate_counts.tolist(),active_node_counts=active_counts.tolist(),
                             lifted_node_count=int(np.sum(~active)),
                             support_lift_off=(active_counts==0).tolist(),
                             max_gap_mm=float(max(0.,np.max(u[contact_dofs]))),
                             max_penetration_mm=float(max(0.,-np.min(u[contact_dofs]))),
                             min_active_reaction_n=float(np.min(nodal_contact[active])),
                             node_indices=contact_nodes.tolist(),node_support_indices=owners.tolist(),
                             node_active=active.tolist(),node_gaps_mm=u[contact_dofs].tolist(),
                             resultant_positions_mm=resultant_positions,
                             node_reactions_n=nodal_contact.tolist()),
                loading=dict(type=cfg.load_type,total_force_n=float(-f[::3].sum()),
                             nodal_forces_n=(-f[::3]).tolist(),
                             tributary_area_mm2=assembled['distributed_areas'].tolist() if assembled['distributed_areas'] is not None else None,
                             domain_source=assembled['domain_source']),
                utilization=float(max(max(member_util),max(abs(u[::3]))/cfg.max_deflection)),
                member_utilization=member_util,
                element_stress=np.asarray(stress), displacement=u[::3])


def solve(nodes, edges, widths, heights, supports, cfg, load_at=None, *, support_radius=None, domain=None, support_patches=None):
    """Solve one case; radius enables rigid distributed compression-only contact."""
    assembled=_assemble(nodes,edges,widths,heights,cfg,domain)
    return _solve(nodes,edges,widths,heights,supports,cfg,load_at,support_radius,assembled,support_patches)


def solve_cases(nodes, edges, widths, heights, supports, cfg, positions, *, support_radius=None, domain=None, support_patches=None):
    """Solve a load envelope with shared stiffness and initial factorization.

    Geometry/materials must remain fixed for this call. Each load position gets
    its own converged active contact set; no displacement or contact state from
    another position is reused.
    """
    assembled=_assemble(nodes,edges,widths,heights,cfg,domain)
    return [_solve(nodes,edges,widths,heights,supports,cfg,p,support_radius,assembled,support_patches)
            for p in positions]



def envelope(cases, positions=None):
    """Componentwise mechanical envelope; maxima need not occur simultaneously.

    Contact state and legacy reactions_n refer explicitly to the utilization-
    governing case. Per-support min/max reactions and plot arrays cover all cases.
    """
    if not cases:
        raise ValueError('Нагрузочная огибающая не содержит расчётных случаев')
    if positions is None:
        positions=[None]*len(cases)
    if len(positions)!=len(cases):
        raise ValueError('Число позиций не совпадает с числом нагрузочных случаев')
    governing=int(np.argmax([case['utilization'] for case in cases]))
    result=dict(cases[governing])
    result['governing_case_index']=governing
    result['governing_position_mm']=list(positions[governing]) if positions[governing] is not None else None
    result['envelope_case_count']=len(cases)
    metric_cases={}
    max_metrics=('max_stress_mpa','max_shear_mpa','max_torsional_shear_mpa',
                 'max_combined_shear_mpa','max_equivalent_stress_mpa','deflection_mm',
                 'utilization','relative_residual')
    min_metrics=('safety_factor','min_buckling_allowable_mpa','min_length_height_ratio')
    for name in max_metrics+min_metrics:
        values=[case[name] for case in cases]
        index=int(np.argmin(values) if name in min_metrics else np.argmax(values))
        result[name]=float(values[index])
        metric_cases[name]=dict(case_index=index,position_mm=list(positions[index]) if positions[index] is not None else None)
    result['metric_governing_cases']=metric_cases
    reactions=np.asarray([case['reactions_n'] for case in cases],dtype=float)
    result['reactions_min_n']=reactions.min(axis=0).tolist()
    result['reactions_max_n']=reactions.max(axis=0).tolist()
    result['reactions_scope']='reactions_n — случай максимального utilization; min/max — покомпонентная огибающая, не одновременные силы'
    balance_index=int(np.argmax([abs(case['reaction_balance_n']) for case in cases]))
    result['reaction_balance_n']=float(cases[balance_index]['reaction_balance_n'])
    result['reaction_balance_max_abs_n']=abs(result['reaction_balance_n'])
    result['element_stress']=np.max(np.stack([case['element_stress'] for case in cases]),axis=0)
    result['member_utilization']=np.max(np.stack([case['member_utilization'] for case in cases]),axis=0)
    result['displacement']=np.max(np.abs(np.stack([case['displacement'] for case in cases])),axis=0)
    result['plot_scope']='Покомпонентные максимумы напряжений и модулей перемещений по всем случаям; не одновременное состояние'
    result['contact']=dict(cases[governing]['contact'],case_index=governing,
                           report_scope='Контактное состояние случая максимального utilization')
    return result
