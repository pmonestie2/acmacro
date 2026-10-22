"""Plan current-sector exploration from per-client discovery packets; no input."""
import argparse
from datetime import datetime
import itertools
import json
import math
from pathlib import Path
import time

from focus import process_identity
from nav_catalog import CAPTURE, STORE, refresh


def route_length(route, origin=None):
    points = ([origin] if origin is not None else []) + [p['raw_xyz'] for p in route]
    return sum(math.dist(a, b) for a, b in zip(points, points[1:]))


def is_gate(point):
    name = (point.get('name') or '').casefold()
    return point.get('nav_type') == 2 and ('gate' in name or name.startswith('accelerator to '))


def order_points(points, origin=None, gates=()):
    def cost(route):
        return route_length(route, origin) + (min(math.dist(route[-1]['raw_xyz'], g['raw_xyz'])
                                                 for g in gates) if route and gates else 0)
    if len(points) <= 9:
        return list(min(itertools.permutations(points),
                        key=cost, default=()))
    remaining, route = list(points), []
    current = origin if origin is not None else remaining[0]['raw_xyz']
    while remaining:
        point = min(remaining, key=lambda p: math.dist(current, p['raw_xyz']))
        remaining.remove(point)
        route.append(point)
        current = point['raw_xyz']
    # Improve long routes without factorial time or a third-party solver.
    improved = True
    while improved:
        improved = False
        current_cost = cost(route)
        for first in range(len(route) - 1):
            for last in range(first + 2, len(route) + 1):
                candidate = route[:first] + route[first:last][::-1] + route[last:]
                value = cost(candidate)
                if value < current_cost - .001:
                    route, current_cost, improved = candidate, value, True
    return route


def split_legs(route):
    """NavType 1 can be a via point; NavType 2 must end a submission."""
    legs, pending = [], []
    for point in route:
        pending.append(point)
        if point['nav_type'] == 2:
            legs.append(pending)
            pending = []
    if pending:
        legs.append(pending)
    return legs


def plan(world, hazards=(), now=None, wells=()):
    now = time.time() if now is None else now
    sector = world['recent']['sector']
    hazards = list(hazards)
    gates = [p for p in world['catalog'] if p.get('kind') == 'map_object'
             and p.get('sector') == sector and p.get('navigation_present')
             and is_gate(p)
             and 'raw_xyz' in p and not p.get('removal_observed') and not p.get('historical')]
    points, blocked = [], []
    contacts = [o for o in world['catalog'] if o.get('kind') == 'ship_or_npc'
                and o.get('hostility') != 'friendly' and 'raw_xyz' in o
                and not o.get('removal_observed')
                and now - o.get('position_seen', 0) <= 30]
    excluded_gates = [g for g in gates if any(math.dist(g['raw_xyz'], o['raw_xyz']) < 15000
                                             for o in [*contacts, *hazards])]
    gates = [g for g in gates if g not in excluded_gates]
    for point in world['catalog']:
        if (point.get('kind') != 'map_object' or point.get('sector') != sector
                or point.get('visited') is not False or point.get('nav_type') not in (1, 2)
                or not point.get('navigation_present') or 'raw_xyz' not in point
                or point.get('removal_observed') or point.get('historical')):
            continue
        dangers = [o.get('name', 'recorded danger') for o in [*contacts, *hazards]
                   if math.dist(point['raw_xyz'], o['raw_xyz']) < 15000]
        if dangers:
            blocked.append(dict(point, blocked_by=dangers))
        else:
            points.append(point)
    player = world['recent'].get('player')
    origin = player['raw_xyz'] if player else None
    route = order_points(points, origin, gates)
    exploration_route = list(route)
    final_origin = route[-1]['raw_xyz'] if route else origin
    gate = min(gates, key=lambda g: math.dist(final_origin, g['raw_xyz'])) if gates and final_origin is not None else None
    gate_dangers = [o.get('name', 'recorded danger') for o in [*contacts, *hazards]
                    if gate and math.dist(gate['raw_xyz'], o['raw_xyz']) < 15000]
    if gate and (not route or route[-1]['id'] != gate['id']):
        route.append(gate)
    gravity_blocker = None
    if wells:
        from gravity_route import avoid_wells
        candidates = [p for p in world['catalog'] if p.get('kind') == 'map_object'
                      and p.get('sector') == sector and p.get('navigation_present')
                      and 'raw_xyz' in p and not p.get('historical') and not p.get('removal_observed')
                      and not any(math.dist(p['raw_xyz'], h['raw_xyz']) < 15000 for h in [*contacts, *hazards])]
        try:
            route = avoid_wells(route, origin, candidates, wells)
        except RuntimeError as error:
            gravity_blocker = str(error)
    return dict(sector=sector, origin=player, origin_is_fresh=bool(player and now-player['last_seen'] <= 15),
                route=route, exploration_route=exploration_route, final_gate=gate,
                excluded_gates=[g['name'] for g in excluded_gates],
                gravity_wells=list(wells), executable=bool(gate and not gate_dangers and not gravity_blocker),
                execution_blocker=gravity_blocker or (('Closest gate has known hazards: ' + ', '.join(gate_dangers)) if gate_dangers
                    else (None if gate else 'No gate with verified navigation coordinates is available')),
                blocked=blocked, distance_km=route_length(route, origin)/1000,
                ordering='shortest route including closest final gate' if len(points) <= 9 else 'nearest-neighbor with 2-opt including final gate',
                legs=[dict(names=[p['name'] for p in leg], via=[p['id'] for p in leg[:-1]],
                           destination=leg[-1]['id']) for leg in split_legs(route)],
                caution='Only observed hazards are known. Recheck live contacts and discovery before each leg; do not click gate entry.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proxy', type=int)
    parser.add_argument('--run', action='store_true', help='Execute the planned legs, finishing at the closest gate without entering it')
    args = parser.parse_args()
    status = json.loads(CAPTURE.with_name('status.json').read_text())
    if status['state'] != 'running' or time.time()-status['updated'] > 15:
        raise RuntimeError('Packet capture is not live')
    proxies = status['active_proxies']
    if args.proxy is None and len(proxies) != 1:
        raise RuntimeError('Select one client with --proxy PID')
    pid = str(args.proxy) if args.proxy is not None else next(iter(proxies))
    process = proxies[pid]
    birth = process_identity(int(pid))
    if birth is None or abs(datetime.fromisoformat(birth).timestamp() -
                            datetime.fromisoformat(process['started'].replace('Z', '+00:00')).timestamp()) > .001:
        raise RuntimeError('Captured proxy has exited or its PID was reused')
    key = pid + '_' + process['started']
    world = json.loads(CAPTURE.read_text())[key]
    saved = refresh()
    wells_path = STORE.with_name('gravity_wells.json')
    wells = json.loads(wells_path.read_text()).get(world['recent']['sector'], []) if wells_path.exists() else []
    from explore_frontier import gate_pass
    result = gate_pass(world, saved.get('hazards', {}).get(world['recent']['sector'], {}).values(), wells=wells)
    result.update(proxy_session=key, generated=time.time())
    output = Path('navigation/exploration_plan.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    print(f"{result['sector']}: {len(result['exploration_route'])} unexplored navpoints; {len(result['blocked'])} blocked; {result['distance_km']:.1f} km")
    for index, leg in enumerate(result['legs'], 1):
        print(f"Leg {index}: " + ' -> '.join(leg['names']))
    print(f'Saved {output}')
    if args.run:
        from explore_sector import execute
        for _ in range(32):
            execute(result)
            # Nav objects can stream in as the ship approaches their area.
            from explore_sector import live_world
            world = live_world(key, result['sector'])
            saved = refresh()
            following = gate_pass(world, saved.get('hazards', {}).get(result['sector'], {}).values(), wells=wells)
            if not following['exploration_route']:
                if following['blocked']:
                    print(f"{len(following['blocked'])} unexplored targets remain blocked by known hazards")
                break
            following.update(proxy_session=key, generated=time.time())
            result = following
            output.write_text(json.dumps(result, indent=2))
            print(f"Follow-up: {len(result['exploration_route'])} newly revealed/unvisited targets", flush=True)
        else:
            raise RuntimeError('Exploration did not converge after 32 tours')
    else:
        print('No navigation sent; add --run to execute')


if __name__ == '__main__':
    main()
