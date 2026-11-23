import io
from dataclasses import replace
import numpy as np
import pytest
import trimesh
from shapely.geometry import box, Point, LineString
from shapely.ops import unary_union
from laminar.config import Config
from laminar.profile import prepare, valid_orientations, contact_patch, seat_depth
from laminar.geometry import footprints, assemble, mesh_of
from laminar.audit import _section_polygon
from laminar.engine import Result, _candidate

PROFILE=[[0.,0.],[12.,0.],[8.,15.],[0.,15.]]

@pytest.mark.parametrize('side',['left','right'])
def test_all_valid_edges_reach_revolution_and_seat(side):
    options=valid_orientations(PROFILE,45,axis_side=side)
    assert len(options)>1
    for edge,profile in options.items():
        actual=prepare(PROFILE,edge,axis_side=side)
        assert actual.axis_original==profile.axis_original
        assert actual.solid().volume()>0
        domain=box(0,0,80,80)
        patch=contact_patch(actual,[0,0],domain)
        assert patch.area>0
        nodes=np.array([[0,0],[80,0],[80,80],[0,80]],dtype=float)
        edges=np.array([[0,1],[1,2],[2,3],[3,0],[0,2]])
        strips,pads,_=footprints(domain,nodes,edges,np.full(5,4.),nodes,profile.radius+2)
        solid=assemble(domain,strips,pads,np.full(5,30.),profile,nodes,45,seat_depth(profile,45))
        mesh=mesh_of(solid)
        assert mesh.is_volume and mesh.is_watertight
        assert len(solid.decompose())==1
        assert mesh.bounds[1,2]==pytest.approx(45+seat_depth(profile,45),abs=.01)

def test_axis_changes_true_volume_and_contact():
    left=prepare(PROFILE,2,axis_side='left');right=prepare(PROFILE,2,axis_side='right')
    assert left.axis_original!=right.axis_original
    assert left.solid().volume()!=pytest.approx(right.solid().volume())
    assert contact_patch(left,[0,0],box(-30,-30,30,30)).covers(Point(0,0))
    assert not contact_patch(right,[0,0],box(-30,-30,30,30)).covers(Point(0,0))

@pytest.mark.parametrize('side',['left','right'])
def test_non_top_concave_edges_are_not_offered(side):
    points=[[0,0],[20,0],[20,20],[12,20],[12,10],[8,10],[8,20],[0,20]]
    options=valid_orientations(points,45,axis_side=side)
    assert 4 not in options and len(options)<len(points)

def test_seats_have_through_channels_not_disk_caps():
    domain=box(0,0,80,80);nodes=np.array([[0,0],[80,0],[80,80],[0,80]],float)
    edges=np.array([[0,1],[1,2],[2,3],[3,0],[0,2]])
    strips,pads,surface=footprints(domain,nodes,edges,np.full(5,4.),nodes,14,min_gap=.66)
    profile=prepare(PROFILE,2,axis_side='left')
    solid=assemble(domain,strips,pads,np.full(5,30.),profile,nodes,45,3,min_gap=.66)
    for s,pad in zip(nodes,pads):
        zone=Point(s).buffer(14).intersection(domain)
        assert pad.area<zone.area*.75
        air=zone.difference(surface).buffer(-.34)
        assert air.area>0
        # The same open set survives every actual depth, including socket band.
        for z in [.01,15,30,44.9,46.5]:
            assert _section_polygon(solid.slice(z)).intersection(air).area<.01
    assert len(solid.decompose())==1

@pytest.mark.parametrize('side',['left','right'])
@pytest.mark.parametrize('edge',[0,1,2,3])
def test_each_menu_choice_generates_with_true_contact(side,edge):
    cfg=Config(width=80,depth=80,load=30,max_opening=18,profile_points=PROFILE,top_edge=edge,axis_side=side)
    result=_candidate(cfg)
    assert result.mesh.is_volume and len(result.mesh.split())==1
    assert min(result.report['supports']['clearance_open_fraction'])>=.1
    assert result.report['mechanics']['reaction_balance_max_abs_n']<1e-5
    assert result.report['supports']['axis_side']==side
    assert result.report['supports']['axis_original']==prepare(PROFILE,edge,axis_side=side).axis_original
    assert result.report['geometry']['external_foot_top_z_mm']==45
    assert all(a>0 for a in result.report['supports']['contact_area_mm2'])
    assert trimesh.load(io.BytesIO(result.stl()),file_type='stl').faces.shape[0]>0

def test_quality_findings_do_not_block_serialization():
    mesh=trimesh.creation.box();r=Result(mesh,{'errors':['strength','printability']},None,None,{},None)
    data=r.stl()
    assert len(data)>84 and r.report['errors'][:2]==['strength','printability']
    assert r.report['export_checks']['recommended'] is False
    assert r.stl()==data

def test_missing_geometry_is_technical_error():
    with pytest.raises(ValueError,match='Нет геометрии'):
        Result(None,{'errors':[]},None,None,{},None).stl()

def test_removed_features_stay_removed():
    with pytest.raises(ValueError):Config(load_type='wheel').validate()
    with pytest.raises(ValueError):Config(axis_side='center').validate()

@pytest.mark.parametrize('size,load,flange',[(60,30,0),(80,1000,10)])
def test_refined_contact_graph_stays_connected_and_keeps_air(size,load,flange):
    cfg=Config(width=size,depth=size,load=load,outer_flange=flange,max_opening=18,profile_points=PROFILE,top_edge=2)
    result=_candidate(cfg)
    assert len(result.mesh.split())==1
    assert min(result.report['supports']['clearance_open_fraction'])>=.1
    assert result.report['mechanics']['reaction_balance_max_abs_n']<1e-5
    # Hydraulic area comes from actual material, not old support disks.
    bottom=(result.mesh.face_normals[:,2]<-.99)&(np.abs(result.mesh.triangles_center[:,2])<1e-6)
    actual_open=1-result.mesh.area_faces[bottom].sum()/(size*size)
    assert result.report['geometry']['surface_open_fraction']==pytest.approx(actual_open,abs=.001)
    blind=result.report['geometry']['blind_open_area_mm2']/(size*size)
    assert result.report['aerodynamics']['open_fraction']==pytest.approx(actual_open-blind,abs=.001)


@pytest.mark.parametrize('load_type',['person','distributed'])
def test_narrow_ring_gets_real_contact_nodes_and_preserves_pressure_area(load_type):
    from laminar.topology import seat_nodes
    from laminar.mechanics import solve_cases
    from laminar.contact import bearing_envelope
    domain=box(0,0,40,40)
    nodes=np.array([[0,0],[40,0],[40,40],[0,40]],float)
    edges=np.array([[0,1],[1,2],[2,3],[3,0],[0,2]])
    patches=[Point(s).buffer(6.00005,quad_segs=48).difference(Point(s).buffer(6,quad_segs=48)).intersection(domain) for s in nodes]
    refined,segments=seat_nodes(domain,nodes,edges,nodes,patches)
    assert len(refined)>len(nodes)
    cases=solve_cases(refined,segments,np.full(len(segments),2.),np.full(len(segments),10.),nodes,
                      Config(load=1,load_type=load_type),[(20,20),(15,15)],support_radius=6.00005,
                      domain=domain,support_patches=patches)
    report=bearing_envelope(domain,refined,nodes,6.00005,cases,patches=patches)
    assert report['full_contact_area_mm2']==pytest.approx([p.area for p in patches])
    assert all(c['reaction_balance_n']==pytest.approx(0,abs=1e-6) for c in cases)
    assert cases[0]['contact']['node_indices']==cases[1]['contact']['node_indices']
    assert max(report['full_contact_area_mm2'])<.001
    assert sum(report['node_tributary_area_mm2'])==pytest.approx(sum(report['full_contact_area_mm2']),abs=1e-8)


@pytest.mark.parametrize('force_degenerate',[False,True])
def test_six_cocircular_bearing_nodes_do_not_double_count_area(monkeypatch,force_degenerate):
    import laminar.contact as contact
    from shapely.geometry import GeometryCollection
    theta=np.arange(6)*np.pi/3
    nodes=np.column_stack((10+6*np.cos(theta),10+6*np.sin(theta)))
    patch=Point(10,10).buffer(7,quad_segs=48).difference(Point(10,10).buffer(5,quad_segs=48))
    original=contact.voronoi_polygons;calls=[]
    def cells(*a,**kw):
        calls.append(1)
        if force_degenerate and len(calls)==1:return GeometryCollection([patch]*6)
        return original(*a,**kw)
    monkeypatch.setattr(contact,'voronoi_polygons',cells)
    case={'contact':{'node_indices':list(range(6)),'node_support_indices':[0]*6,
                     'node_reactions_n':[1.]*6,'node_active':[True]*6}}
    result=contact.bearing_envelope(box(0,0,20,20),nodes,[[10,10]],7,[case],patches=[patch])
    assert sum(result['node_tributary_area_mm2'])==pytest.approx(patch.area,rel=1e-9)
    assert result['peak_cell_pressure_mpa'][0]==pytest.approx(max(1/a for a in result['node_tributary_area_mm2']))
    if force_degenerate:assert result['regularized_support_indices']==[0]


def test_air_channels_exclude_the_full_external_foot_projection(monkeypatch):
    import laminar.engine as engine
    observed=[];original=engine.evaluate
    def capture(holes,*args,**kwargs):
        observed.append(holes)
        return original(holes,*args,**kwargs)
    monkeypatch.setattr(engine,'evaluate',capture)
    result=engine._candidate(Config(width=80,depth=80,load=30,max_opening=18,profile_points=PROFILE,top_edge=2))
    supports=result.report['supports']['positions_mm']
    blocked=unary_union([Point(s).buffer(12,quad_segs=48) for s in supports]).intersection(box(0,0,80,80))
    for holes in observed:
        assert sum(h.intersection(blocked).area for h in holes)<1e-6
    assert sum(h.area for h in observed[-1])/6400==pytest.approx(result.report['aerodynamics']['open_fraction'])
    assert result.report['geometry']['blind_open_area_mm2']>0
    assert result.report['geometry']['surface_open_fraction']>result.report['geometry']['open_fraction']
