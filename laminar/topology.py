"""Free planar rib graphs and bounded, evaluator-driven topology search.

No lattice or tessellation is imposed. Boundary curves are discretised exactly;
interior points are rejection-sampled with support-dependent density, connected
by a visibility tree, then given local branches. Search can remove/add members
and move junctions. Structural and airflow scores belong to the caller, so a
geometric mutation is accepted only after the same engineering evaluation.
Coordinates and lengths use millimetres. This is a local search, not a proof of
optimality. Straight members may form a polyline approximation of a curved path.
"""
from dataclasses import dataclass
import math
from collections.abc import Callable

import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points, unary_union


TOLERANCE = 1e-8


def _lines(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type in ('LineString', 'LinearRing'):
        return [LineString(geometry)]
    return [line for part in getattr(geometry, 'geoms', ()) for line in _lines(part)]


def _connected(nodes, edges):
    if not len(nodes) or not len(edges):
        return False
    adjacent = [[] for _ in nodes]
    for i, j in edges:
        adjacent[i].append(j)
        adjacent[j].append(i)
    visited, pending = {0}, [0]
    while pending:
        for j in adjacent[pending.pop()]:
            if j not in visited:
                visited.add(j)
                pending.append(j)
    return len(visited) == len(nodes)


def graph_from_lines(domain, lines, supports=()):
    """Node all line crossings and supports, retaining the exact room boundary.

    The supplied lines must lie in the domain. Support coordinates are retained
    to 1e-8 mm; an off-line support receives a visible connector. The output is
    a connected indexed graph without duplicate or zero-length members.
    """
    if domain.geom_type != 'Polygon' or domain.is_empty or not domain.is_valid:
        raise ValueError('Для генерации нужен один корректный контур панели')
    supports = np.asarray(supports, dtype=float).reshape((-1, 2))
    if not np.isfinite(supports).all() or any(not domain.buffer(TOLERANCE).covers(Point(p)) for p in supports):
        raise ValueError('Опоры должны находиться в контуре панели')
    all_lines = _lines(domain.boundary)
    allowed = domain.buffer(TOLERANCE)
    for line in lines:
        for part in _lines(line):
            if not allowed.covers(part):
                raise ValueError('Ребро выходит за контур панели')
            if part.length > TOLERANCE:
                all_lines.append(part)
    combined = unary_union(all_lines)
    for support in supports:
        p = Point(support)
        if combined.distance(p) <= TOLERANCE:
            continue
        choices = []
        for line in _lines(combined):
            q = nearest_points(p, line)[1]
            segment = LineString([support, q.coords[0]])
            if allowed.covers(segment):
                choices.append(segment)
        if not choices:
            raise ValueError('Не удалось соединить опору с несущей структурой')
        all_lines.append(min(choices, key=lambda line: line.length))
        combined = unary_union(all_lines)
    lookup, points, edges = {}, [], set()

    def index(point):
        key = tuple(np.round(point, 8))
        if key not in lookup:
            lookup[key] = len(points)
            points.append(key)
        return lookup[key]

    for line in _lines(combined):
        vertices = np.asarray(line.coords)
        for a, b in zip(vertices[:-1], vertices[1:]):
            delta = b-a
            length2 = float(delta@delta)
            if length2 <= TOLERANCE**2:
                continue
            pieces = [(0., a), (1., b)]
            for support in supports:
                t = float((support-a)@delta/length2)
                if 0 < t < 1 and np.linalg.norm(a+t*delta-support) <= TOLERANCE:
                    pieces.append((t, support))
            pieces.sort(key=lambda item: item[0])
            for (_, first), (_, second) in zip(pieces[:-1], pieces[1:]):
                i, j = index(first), index(second)
                if i != j:
                    edges.add(tuple(sorted((i, j))))
    nodes = np.asarray(points, dtype=float)
    edges = np.asarray(sorted(edges), dtype=int).reshape((-1, 2))
    if not _connected(nodes, edges):
        raise ValueError('Несущая структура содержит несвязанные участки')
    if any(np.min(np.linalg.norm(nodes-p, axis=1)) > 2*TOLERANCE for p in supports):
        raise ValueError('Опора отсутствует в расчётной структуре')
    return nodes, edges


def network(domain, supports, spacing, seed):
    """Generate an irregular support-adaptive visibility graph; same API as geometry.network."""
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError('Шаг поиска должен быть положительным')
    supports = np.asarray(supports, dtype=float).reshape((-1, 2))
    if not len(supports) or not np.isfinite(supports).all():
        raise ValueError('Для несущей структуры нужны опоры')
    if domain.geom_type != 'Polygon' or not domain.is_valid or domain.is_empty:
        raise ValueError('Некорректный контур панели')
    if any(not domain.buffer(TOLERANCE).covers(Point(p)) for p in supports):
        raise ValueError('Опора вне панели')
    count = max(8, math.ceil(domain.area/(spacing*spacing)*1.6))
    if count > 1800:
        raise ValueError('Превышен предел 1800 узлов свободной генерации')
    rng = np.random.default_rng(seed)
    points, lookup, boundary_edges = [], {}, set()

    def add(point):
        key = tuple(np.round(point, 8))
        if key not in lookup:
            lookup[key] = len(points)
            points.append(np.asarray(key))
        return lookup[key]

    # Split at original vertices and boundary supports before regular sampling.
    for ring in [domain.exterior, *domain.interiors]:
        coordinates = np.asarray(ring.coords)
        for a, b in zip(coordinates[:-1], coordinates[1:]):
            delta = b-a
            length = np.linalg.norm(delta)
            splits = [0., 1.]
            for support in supports:
                t = float((support-a)@delta/(length*length))
                if 0 < t < 1 and np.linalg.norm(a+t*delta-support) <= TOLERANCE:
                    splits.append(t)
            splits.sort()
            for t0, t1 in zip(splits[:-1], splits[1:]):
                n = max(1, math.ceil(length*(t1-t0)/spacing))
                ids = [add(a+t*delta) for t in np.linspace(t0, t1, n+1)]
                boundary_edges.update(tuple(sorted(pair)) for pair in zip(ids[:-1], ids[1:]))
    for support in supports:
        add(support)
    low, high = np.array(domain.bounds[:2]), np.array(domain.bounds[2:])
    interior_count = 0
    for _ in range(count*60):
        if interior_count >= count:
            break
        point = rng.uniform(low, high)
        if not domain.contains(Point(point)):
            continue
        # Higher local sampling density near supports; no prescribed cell shape.
        distance = float(np.min(np.linalg.norm(supports-point, axis=1)))
        clearance = spacing*(.36+.20*(1-math.exp(-distance/(spacing*2))))
        if np.min(np.linalg.norm(np.asarray(points)-point, axis=1)) < clearance:
            continue
        add(point)
        interior_count += 1
    nodes = np.asarray(points, dtype=float)
    tree = cKDTree(nodes)
    _, neighbors = tree.query(nodes, k=min(12, len(nodes)))
    allowed = domain.buffer(TOLERANCE)
    candidates = {}
    for i, nearby in enumerate(neighbors):
        for j in np.atleast_1d(nearby):
            j = int(j)
            if i == j:
                continue
            pair = tuple(sorted((i, j)))
            if pair in candidates:
                continue
            line = LineString(nodes[list(pair)])
            if allowed.covers(line):
                candidates[pair] = line.length
    parent = list(range(len(nodes)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    edges = set(boundary_edges)
    for i, j in edges:
        parent[root(j)] = root(i)
    for (i, j), _ in sorted(candidates.items(), key=lambda item: (item[1], item[0])):
        a, b = root(i), root(j)
        if a != b:
            edges.add((i, j))
            parent[b] = a
    # Concave rooms and holes can disconnect a bounded nearest-neighbor graph.
    # Connect components using the shortest visible cross-component member.
    while len({root(i) for i in range(len(nodes))}) > 1:
        best = None
        for i in range(len(nodes)):
            other = [j for j in range(i+1, len(nodes)) if root(i) != root(j)]
            if not other:
                continue
            for j in sorted(other, key=lambda j: np.linalg.norm(nodes[i]-nodes[j])):
                distance = float(np.linalg.norm(nodes[i]-nodes[j]))
                if best is not None and distance >= best[0]:
                    break
                if allowed.covers(LineString([nodes[i], nodes[j]])):
                    best = (distance, i, j)
                    break
        if best is None:
            raise ValueError('Не удалось соединить участки внутри контура')
        _, i, j = best
        edges.add((i, j))
        parent[root(j)] = root(i)
    # Local loops carry alternative load paths. Degree varies with support field
    # and random priorities, rather than enforcing triangular/polygonal cells.
    degree = np.bincount(np.asarray(list(edges)).ravel(), minlength=len(nodes))
    target = rng.integers(2, 5, size=len(nodes))
    ranked = sorted(candidates, key=lambda e: candidates[e]*rng.uniform(.85, 1.15))
    for edge in ranked:
        i, j = edge
        if candidates[edge] <= spacing*1.8 and (degree[i] < target[i] or degree[j] < target[j]):
            if edge not in edges:
                edges.add(edge)
                degree[i] += 1
                degree[j] += 1
    lines = [LineString(nodes[list(e)]) for e in sorted(edges)]
    return graph_from_lines(domain, lines, supports)


@dataclass
class SearchResult:
    nodes: np.ndarray
    edges: np.ndarray
    evaluation: dict
    history: list


def optimize(domain, nodes, edges, supports, spacing, seed, evaluate: Callable,
             rounds=2, max_evaluations=9):
    """Evaluate bounded remove/add/move proposals and retain improvements.

    `evaluate(nodes, edges)` returns a dictionary with a finite scalar `score`.
    Lower is better; the caller must include all mechanical/air/print constraints
    in this score. Optional `edge_priority` (one value/member) ranks removals;
    optional `node_priority` (one value/node) ranks reinforcements and movements.
    They can be stresses/utilizations and displacements from the actual solver.
    Each accepted candidate is re-noded and connected; exact boundary/support
    positions are protected. No parameter weights are exposed to the user.
    """
    if max_evaluations < 1 or rounds < 0:
        raise ValueError('Некорректный бюджет поиска топологии')
    rng = np.random.default_rng(seed)
    supports = np.asarray(supports, dtype=float).reshape((-1, 2))
    nodes, edges = graph_from_lines(domain, [LineString(nodes[e]) for e in edges], supports)
    evaluation = evaluate(nodes, edges)
    if not np.isfinite(evaluation['score']):
        raise ValueError('Некорректная целевая функция топологии')
    history = [dict(operation='initial', score=float(evaluation['score']), accepted=True,
                    nodes=len(nodes), edges=len(edges))]
    calls = 1
    for _ in range(rounds):
        if calls >= max_evaluations:
            break
        boundary = np.array([domain.boundary.buffer(TOLERANCE).covers(LineString(nodes[e])) for e in edges])
        fixed = np.array([domain.boundary.distance(Point(p)) < TOLERANCE or
                          np.min(np.linalg.norm(supports-p, axis=1)) < TOLERANCE*2 for p in nodes])
        edge_priority = np.asarray(evaluation.get('edge_priority', np.zeros(len(edges))))
        node_priority = np.asarray(evaluation.get('node_priority', np.zeros(len(nodes))))
        if edge_priority.shape != (len(edges),) or node_priority.shape != (len(nodes),):
            raise ValueError('Размер поля требований не соответствует графу')
        proposals = []
        removable = np.flatnonzero(~boundary)
        for i in sorted(removable, key=lambda i: (edge_priority[i], i))[:2]:
            proposals.append(('remove', nodes.copy(), np.delete(edges, i, axis=0)))
        movable = np.flatnonzero(~fixed)
        if len(movable):
            index = max(movable, key=lambda i: (node_priority[i], -i))
            moved = nodes.copy()
            moved[index] += rng.normal(0, spacing*.18, 2)
            if domain.contains(Point(moved[index])):
                proposals.append(('move', moved, edges.copy()))
        present = {tuple(sorted(e)) for e in edges}
        additions = []
        _, neighbors = cKDTree(nodes).query(nodes, k=min(9, len(nodes)))
        for i in np.argsort(-node_priority, kind='stable')[:min(12, len(nodes))]:
            for j in np.atleast_1d(neighbors[i]):
                pair = tuple(sorted((int(i), int(j))))
                if i == j or pair in present:
                    continue
                line = LineString(nodes[list(pair)])
                if line.length < spacing*2.5 and domain.buffer(TOLERANCE).covers(line):
                    additions.append((-(node_priority[i]+node_priority[j]), line.length, pair))
        if additions:
            pair = min(additions)[2]
            proposals.append(('add', nodes.copy(), np.vstack([edges, pair])))
        best = None
        for operation, trial_nodes, trial_edges in proposals:
            if calls >= max_evaluations:
                break
            record = dict(operation=operation, accepted=False)
            try:
                nn, ee = graph_from_lines(domain, [LineString(trial_nodes[e]) for e in trial_edges], supports)
                calls += 1
                candidate = evaluate(nn, ee)
                score = float(candidate['score'])
                if not math.isfinite(score):
                    raise ValueError('Неконечная оценка топологии')
                record.update(score=score, nodes=len(nn), edges=len(ee))
                if score < evaluation['score']-max(1e-9, abs(evaluation['score'])*1e-6):
                    if best is None or score < best[0]:
                        best = (score, nn, ee, candidate, len(history))
            except (ValueError, RuntimeError) as exc:
                record['reason'] = str(exc)
            history.append(record)
        if best is None:
            break
        _, nodes, edges, evaluation, accepted_index = best
        history[accepted_index]['accepted'] = True
    return SearchResult(nodes, edges, evaluation, history)


def seat_nodes(domain,nodes,edges,supports,patches):
    """Split each rib inside true bearing patches; do not bridge air with pads.

    New FEM nodes lie on the same connected rib centre-lines. Midpoints of
    every contact interval support annular as well as solid foot tops.
    """
    points=np.asarray(nodes).tolist();segments=[]
    for first,last in edges:
        line=LineString(nodes[[first,last]]);cuts=[]
        for patch in patches:
            for part in _lines(line.intersection(patch)):
                if part.length>1e-8:
                    cuts.append(line.project(part.interpolate(.5,normalized=True)))
        previous=int(first);last_distance=0.
        for distance in sorted(cuts):
            if distance-last_distance<1e-4 or line.length-distance<1e-4:
                continue
            index=len(points);points.append(list(line.interpolate(distance).coords[0]))
            segments.append((previous,index));previous=index;last_distance=distance
        segments.append((previous,int(last)))
    return np.asarray(points),np.asarray(segments,dtype=int)
