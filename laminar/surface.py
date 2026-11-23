"""Requirement-driven thin working surface, independently of the load-path graph."""
import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely import maximum_inscribed_circle
from .geometry import polygons


def _points(shape):
    if shape.is_empty:
        return []
    if shape.geom_type == 'Point':
        return [shape]
    if hasattr(shape, 'geoms'):
        return [p for child in shape.geoms for p in _points(child)]
    return []


def refine(domain, nodes, edges, supports, width, max_opening, pad_radius):
    """Split excessive apertures with short automatically placed surface bridges.

    Bridges follow each current aperture's principal axes, not a prescribed grid.
    They terminate on the load-path graph. All crossings become shared FEM nodes.
    Returns (nodes, edges, structural_mask, audit).
    """
    structural = unary_union([LineString(nodes[e]) for e in edges])
    lines = [structural]
    pads = unary_union([Point(p).buffer(pad_radius, quad_segs=12) for p in supports])
    bridge_count = 0
    limit = 240
    for _ in range(limit):
        graph = unary_union(lines)
        material = graph.buffer(width/2, quad_segs=3).union(pads).intersection(domain)
        holes = polygons(domain.difference(material))
        excessive = []
        for hole in holes:
            circle = maximum_inscribed_circle(hole, tolerance=.01)
            if 2*circle.length+.02 > max_opening:
                excessive.append((circle.length, hole, circle.coords[0]))
        if not excessive:
            break
        _, hole, center = max(excessive, key=lambda p:p[0])
        center = np.asarray(center)
        coordinates = np.asarray(hole.exterior.coords)[:-1]
        _, vectors = np.linalg.eigh(np.cov(coordinates.T))
        # Try both principal directions; retain the shortest span joining ribs.
        choices=[]
        span=np.hypot(domain.bounds[2]-domain.bounds[0],domain.bounds[3]-domain.bounds[1])*2
        for direction in (vectors[:,0],vectors[:,1]):
            ray=LineString([center-direction*span,center+direction*span])
            hits=_points(graph.intersection(ray))
            distances=sorted(float(np.dot(np.asarray(p.coords[0])-center,direction)) for p in hits)
            lower=[d for d in distances if d < -width/2]
            upper=[d for d in distances if d > width/2]
            if lower and upper:
                a,b=center+max(lower)*direction,center+min(upper)*direction
                segment=LineString([a,b])
                if domain.buffer(1e-7).covers(segment):
                    choices.append(segment)
        if not choices:
            raise ValueError('Невозможно автоматически соединить рабочую перемычку с рёбрами')
        lines.append(min(choices,key=lambda line:line.length))
        bridge_count+=1
    else:
        raise ValueError('Превышен предел уточнения рабочей поверхности; увеличьте допустимое отверстие')

    # Snap shared line endpoints before noding; decimal rounding only after
    # intersections can otherwise split a junction into disconnected FEM nodes.
    from shapely import set_precision
    graph=unary_union([set_precision(line,1e-6) for line in lines])
    lookup={};points=[];segments=set()
    for line in (graph.geoms if hasattr(graph,'geoms') else [graph]):
        coords=list(line.coords)
        for a,b in zip(coords,coords[1:]):
            ids=[]
            for p in (a,b):
                key=tuple(np.round(p,7))
                if key not in lookup:
                    lookup[key]=len(points);points.append(key)
                ids.append(lookup[key])
            if ids[0]!=ids[1]:segments.add(tuple(sorted(ids)))
    points=np.asarray(points);segments=np.asarray(sorted(segments),dtype=int)
    # Union may emit the same junction on opposite sides of a rounding bin.
    # Merge only sub-micron coincident endpoints, retaining every real rib.
    from scipy.spatial import cKDTree
    parent=list(range(len(points)))
    def root(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for a,b in cKDTree(points).query_pairs(3e-6):
        a,b=root(a),root(b)
        if a!=b:
            if np.min(np.linalg.norm(np.asarray(supports)-points[b],axis=1))<1e-8:a,b=b,a
            parent[b]=a
    roots=sorted(set(root(i) for i in range(len(points))))
    remap={old:new for new,old in enumerate(roots)}
    segments=np.array(sorted({tuple(sorted((remap[root(a)],remap[root(b)]))) for a,b in segments if root(a)!=root(b)}),dtype=int)
    points=points[roots]
    # A rounded bridge endpoint can lie microscopically beside a rib interior,
    # rather than beside another endpoint. Split that rib at the shared node.
    noded=set()
    for a,b in segments:
        delta=points[b]-points[a];length2=float(delta@delta)
        t=(points-points[a])@delta/length2
        distance=np.linalg.norm(points-(points[a]+t[:,None]*delta),axis=1)
        ids=np.flatnonzero((t>0)&(t<1)&(distance<=3e-6))
        chain=[int(a)]+ids[np.argsort(t[ids])].tolist()+[int(b)]
        noded.update(tuple(sorted((i,j))) for i,j in zip(chain,chain[1:]) if i!=j)
    segments=np.asarray(sorted(noded),dtype=int)
    # Membership in the original structural graph preserves variable beam heights.
    tolerance=structural.buffer(2e-6)
    structural_mask=np.array([tolerance.covers(LineString(points[e])) for e in segments])
    return points,segments,structural_mask,dict(added_surface_bridges=bridge_count,
        structural_members=int(structural_mask.sum()),surface_members=int((~structural_mask).sum()))
