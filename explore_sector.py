"""Execute one sector's exploration plan using its owned proxy session.

Destination-only navpoints require separate submissions. Never toggle warp or
click gate entry here. Uncertain submission/arrival stops execution; it does not
relogin, retry the packet, or operate on a different client.
"""
from datetime import datetime
import json
import math
import time
from types import SimpleNamespace

from focus import process_identity
from gate_navigation import submit
from nav_catalog import CAPTURE, STORE


def read_snapshot(path):
    # Windows can briefly deny opening a file while the recorder atomically
    # replaces it. Retry the read, never the navigation packet.
    for attempt in range(10):
        try:
            return json.loads(path.read_text())
        except (PermissionError, FileNotFoundError):
            if attempt == 9:
                raise
            time.sleep(.05)


def live_world(session, sector):
    status = read_snapshot(CAPTURE.with_name('status.json'))
    if status.get('state') != 'running' or time.time() - status['updated'] > 15:
        raise RuntimeError('Packet capture is no longer live')
    pid, started = session.split('_', 1)
    active = status['active_proxies'].get(pid)
    birth = process_identity(int(pid))
    if (not active or active['started'] != started or birth is None
            or abs(datetime.fromisoformat(birth).timestamp() -
                   datetime.fromisoformat(started.replace('Z', '+00:00')).timestamp()) > .001):
        raise RuntimeError('Planned game session has exited or changed')
    world = read_snapshot(CAPTURE)[session]
    if world['recent'].get('sector') != sector:
        raise RuntimeError('Client changed sector; this route is no longer valid')
    return world


def check_leg(world, leg, hazards, now=None, check_hazards=True):
    now = time.time() if now is None else now
    objects = {p['id']: p for p in world['catalog']}
    dangers = [p for p in world['catalog'] if p.get('kind') == 'ship_or_npc'
               and p.get('hostility') != 'friendly' and 'raw_xyz' in p
               and not p.get('removal_observed') and now-p.get('position_seen', 0) <= 30]
    for expected in leg:
        point = objects.get(expected['id'])
        if (not point or point.get('historical') or point.get('removal_observed')
                or point.get('sector') != world['recent']['sector']
                or not point.get('navigation_present') or point.get('nav_type') not in (1, 2)
                or point.get('raw_xyz') != expected['raw_xyz']):
            raise RuntimeError('Navigation target changed: ' + expected['name'])
        if check_hazards and any(math.dist(point['raw_xyz'], p['raw_xyz']) < 15000 for p in [*dangers, *hazards]):
            raise RuntimeError('Known danger near target: ' + expected['name'])
    return objects


def leg_complete(world, leg, objects, now=None, position_floor=None):
    now = time.time() if now is None else now
    player = world['recent'].get('player')
    # A handler return alone does not prove movement or discovery.
    fresh = player and (player['last_seen'] >= position_floor if position_floor is not None
                        else now-player['last_seen'] <= 15)
    # Position packets stop before the client's final coast (observed 3.9 km
    # while the UI showed a completed 1 km arrival). Discovery + settled motion
    # below makes this an arrival check, not merely a proximity check.
    radius = 10000 if 'gate' in leg[-1].get('name', '').casefold() else 5000
    return bool(fresh
                and math.dist(player['raw_xyz'], leg[-1]['raw_xyz']) <= radius
                and all(p.get('visited') is not False or objects[p['id']].get('visited') is True
                        for p in leg))


def execute(result, timeout=240, poll=1, resume=False, allow_frontier=False):
    from exploration_plan import split_legs, is_gate
    if allow_frontier:
        raise ValueError('Ordinary-nav endpoints are disabled; every leg must end at a gate')
    legs = split_legs(result['route'])
    if any(not is_gate(leg[-1]) for leg in legs):
        raise ValueError('Every exploration leg must end at a gate')
    if not result['executable']:
        raise RuntimeError(result['execution_blocker'])
    session, sector = result['proxy_session'], result['sector']
    pid = int(session.split('_', 1)[0])
    live_world(session, sector)
    birth = process_identity(pid)
    instance = SimpleNamespace(proxy_pid=pid, proxy_born=birth,
                               valid=lambda: process_identity(pid) == birth)
    hazards = list(json.loads(STORE.read_text()).get('hazards', {}).get(sector, {}).values())
    for index, leg in enumerate(legs, 1):
        world = live_world(session, sector)
        objects = check_leg(world, leg, hazards, check_hazards=not (resume and index == 1))
        if leg_complete(world, leg, objects):
            continue
        position_floor = time.time()
        if resume and index == 1 and world['recent'].get('player'):
            position_floor = world['recent']['player']['last_seen']
        print(f"Exploration leg {index}: " + ' -> '.join(p['name'] for p in leg), flush=True)
        if not (resume and index == 1):
            report = submit(instance, int(leg[-1]['id'], 16),
                            via=tuple(int(p['id'], 16) for p in leg[:-1]))
            if report.get('state') != 'handler_returned':
                raise RuntimeError('Navigation was not submitted: ' + str(report))
        from exploration_plan import route_length
        player = world['recent'].get('player')
        origin = player['raw_xyz'] if player else None
        # A multi-waypoint leg may span most of a sector: budget travel at a
        # conservative 1 km/s plus two minutes rather than a single-hop limit.
        travel_budget = route_length(leg, origin)/1000 + 120
        deadline = time.monotonic() + max(timeout, travel_budget)
        while time.monotonic() < deadline:
            world = live_world(session, sector)
            # New contacts around via points must not interrupt an already
            # submitted warp path. Validate identity/coordinates while moving.
            objects = check_leg(world, leg, hazards, check_hazards=False)
            if leg_complete(world, leg, objects, position_floor=position_floor):
                # The final position packet can arrive before the ship coasts
                # to its stop. Do not replace a route while still warping.
                if time.time()-world['recent']['player']['last_seen'] >= 2:
                    break
            time.sleep(poll)
        else:
            raise RuntimeError('Exploration arrival/discovery not confirmed; no packet retry was sent')
    if result['final_gate']:
        print('Exploration complete; at ' + result['final_gate']['name'] + '; gate entry was not clicked.', flush=True)
    else:
        print('Frontier pass arrived; checking newly streamed navigation objects.', flush=True)
