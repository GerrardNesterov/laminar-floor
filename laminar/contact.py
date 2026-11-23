"""Area-weighted bearing pressure on the actual top-contact footprint."""
import numpy as np
from shapely.geometry import Point,MultiPoint
from shapely import voronoi_polygons


def bearing_envelope(domain,nodes,supports,radius,cases,patches=None):
    first=cases[0]['contact']
    ids=np.asarray(first['node_indices'],dtype=int)
    owners=np.asarray(first['node_support_indices'],dtype=int)
    areas=np.zeros(len(ids))
    full=[];regularized=[]
    for owner,position in enumerate(supports):
        patch=patches[owner] if patches is not None else Point(position).buffer(radius,quad_segs=48).intersection(domain)
        full.append(float(patch.area))
        selected=np.flatnonzero(owners==owner)
        if len(selected)==1:
            areas[selected[0]]=patch.area
        else:
            sites=nodes[ids[selected]]
            cells=voronoi_polygons(MultiPoint(sites),extend_to=patch.envelope,ordered=True)
            clipped=[cell.intersection(patch) for cell in cells.geoms]
            from shapely.ops import unary_union
            def covers_patch(parts):
                return (len(parts)==len(selected) and all(part.area>1e-10 for part in parts)
                        and np.isclose(sum(part.area for part in parts),patch.area,rtol=1e-9,atol=1e-10)
                        and patch.difference(unary_union(parts)).area<=max(1e-10,patch.area*1e-9))
            if not covers_patch(clipped):
                from scipy.spatial import cKDTree
                separation=float(cKDTree(sites).query(sites,k=2)[0][:,1].min())
                epsilon=min(1e-6,separation*.01)
                adjusted=sites+np.random.default_rng(0).uniform(-epsilon,epsilon,sites.shape)
                cells=voronoi_polygons(MultiPoint(adjusted),extend_to=patch.envelope,ordered=True)
                clipped=[cell.intersection(patch) for cell in cells.geoms]
                if not covers_patch(clipped):
                    raise ValueError('Контактные области не покрывают реальную площадку без перекрытий')
                regularized.append(owner)
            for index,cell in zip(selected,clipped):
                areas[index]=cell.area
    if np.any(areas<=1e-10):
        raise ValueError('Контактный узел имеет нулевую расчётную площадку')
    local_peak=np.zeros(len(supports));active_area_min=np.asarray(full)
    mean_peak=np.zeros(len(supports))
    for case in cases:
        contact=case['contact']
        if contact['node_indices']!=first['node_indices'] or contact['node_support_indices']!=first['node_support_indices']:
            raise ValueError('Контактные узлы изменились внутри нагрузочной огибающей')
        reactions=np.maximum(0,np.asarray(contact['node_reactions_n']))
        active=np.asarray(contact['node_active'],dtype=bool)
        for owner in range(len(supports)):
            selected=owners==owner
            active_area=float(areas[selected & active].sum())
            force=float(reactions[selected].sum())
            local_peak[owner]=max(local_peak[owner],float(np.max(reactions[selected]/areas[selected])))
            active_area_min[owner]=min(active_area_min[owner],active_area)
            mean_peak[owner]=max(mean_peak[owner],force/active_area if active_area else 0.)
    return dict(full_contact_area_mm2=full,min_active_contact_area_mm2=active_area_min.tolist(),
                peak_cell_pressure_mpa=local_peak.tolist(),peak_mean_active_pressure_mpa=mean_peak.tolist(),
                node_tributary_area_mm2=areas.tolist(),regularized_support_indices=regularized,
                method='Узловая реакция / область ближайшего контактного узла, обрезанная верхней площадкой DXF, панелью и фактическим материалом рёбер',
                scope='Дискретная оценка давления; не непрерывное решение контактных напряжений')
