"""Conservative route replacement policy; 0x009b stops an active warp.

No background navigator is started by importing this library. A caller must
provide a verified stop acknowledgement before the second submission. Missing
contacts are not proof of safety: this is an observed-clearance rule only.
"""
import math
import time

from explore_sector import check_leg, live_world
from gate_navigation import NavigationUncertain, submit


def change_clearance(world, hazards=(), *, now=None, radius=20000):
    """Return reasons to defer a stop/restart, using the current ship position."""
    now = time.time() if now is None else now
    player = world['recent'].get('player')
    if not player or player.get('historical') or now-player['last_seen'] > 3:
        return {'allowed': False, 'reasons': ['Current player position is stale']}
    if now-world['recent'].get('updated', 0) > 3:
        return {'allowed': False, 'reasons': ['World capture is stale']}
    contacts = [p for p in world['catalog'] if p.get('kind') == 'ship_or_npc'
                and p.get('hostility') != 'friendly' and 'raw_xyz' in p
                and not p.get('historical') and not p.get('removal_observed')
                and now-p.get('position_seen', 0) <= 30]
    reasons = [p.get('name') or 'Unidentified contact' for p in [*contacts, *hazards]
               if math.dist(player['raw_xyz'], p['raw_xyz']) < radius]
    return {'allowed': not reasons, 'reasons': reasons, 'radius': radius}


def replace_warp_route(instance, session, sector, route, *, wait_for_stop,
                       warp_confirmed, hazards=()):
    """Replace an active warp only in an observed clear area, ending at a gate.

    wait_for_stop(stop_submission_time) must verify a NEW stop acknowledgement
    for this same session and return True. Without it, no restart is sent.
    The caller must serialize this entire operation with other navigation input.
    Rejected clearance sends nothing, so the original warp continues.
    """
    if not warp_confirmed:
        raise ValueError('Active warp must be confirmed before a toggle is sent')
    if (not route or route[-1].get('nav_type') != 2
            or 'gate' not in (route[-1].get('name') or '').casefold()
            or any(p.get('nav_type') != 1 for p in route[:-1])):
        raise ValueError('Replacement must use via navpoints and end at a gate')
    if int(session.split('_', 1)[0]) != instance.proxy_pid or not instance.valid():
        raise RuntimeError('Replacement does not belong to the owned proxy')
    hazards = list(hazards)
    world = live_world(session, sector)
    clearance = change_clearance(world, hazards)
    if not clearance['allowed']:
        return dict(state='deferred', **clearance)
    check_leg(world, route, hazards)
    destination = int(route[-1]['id'], 16)
    via = tuple(int(p['id'], 16) for p in route[:-1])
    stop_time = time.time()
    stopped = submit(instance, destination, via=via)
    if stopped.get('state') != 'handler_returned' or not wait_for_stop(stop_time):
        raise NavigationUncertain('Route change stop not confirmed; no second toggle sent')
    # Validate the session and target identities again. Once stopped, do not
    # delay departure with more planning or UI work; restart the checked route.
    check_leg(live_world(session, sector), route, hazards, check_hazards=False)
    restarted = submit(instance, destination, via=via)
    if restarted.get('state') != 'handler_returned':
        raise NavigationUncertain('Replacement warp submission not confirmed')
    return dict(state='restart_submitted', stop=stopped, restart=restarted)
