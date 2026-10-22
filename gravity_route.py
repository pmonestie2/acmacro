"""Avoid verified spherical gravity-well zones using captured warp-path navs.

Coordinates/radii are raw game units. Unknown map coverage is not proof that a
sector has no wells. Never invent packet IDs for arbitrary detour coordinates.
"""
import heapq
import math


def crosses_well(a, b, well):
    center = well['raw_xyz']
    direction = [y-x for x, y in zip(a, b)]
    length2 = sum(v*v for v in direction)
    t = max(0, min(1, sum((c-x)*d for c, x, d in zip(center, a, direction))/length2)) if length2 else 0
    nearest = [x+t*d for x, d in zip(a, direction)]
    return math.dist(nearest, center) <= well['radius'] + well.get('margin', 5000)


def avoid_wells(route, origin, candidates, wells):
    if not wells:
        return route
    if origin is None:
        raise RuntimeError('Gravity-well routing requires a known player position')
    result, current = [], origin
    for target in route:
        # Only ordinary warp-path points can serve as injected intermediates.
        nodes = [dict(id='origin', raw_xyz=current), target]
        nodes += [p for p in candidates if p['id'] != target['id'] and p.get('nav_type') == 1
                  and not any(crosses_well(p['raw_xyz'], p['raw_xyz'], w) for w in wells)]
        queue, costs = [(0, 0, [])], {0: 0}
        found = None
        while queue:
            cost, index, path = heapq.heappop(queue)
            if cost != costs[index]:
                continue
            if index == 1:
                found = path
                break
            for nxt in range(1, len(nodes)):
                a, b = nodes[index]['raw_xyz'], nodes[nxt]['raw_xyz']
                if nxt == index or any(crosses_well(a, b, w) for w in wells):
                    continue
                value = cost + math.dist(a, b)
                if value < costs.get(nxt, float('inf')):
                    costs[nxt] = value
                    heapq.heappush(queue, (value, nxt, path+[nxt]))
        if found is None:
            raise RuntimeError('No captured navpoint route avoids gravity wells to ' + target['name'])
        result.extend(nodes[i] for i in found)
        current = target['raw_xyz']
    return result
