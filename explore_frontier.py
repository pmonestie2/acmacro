"""Supervised sector touring when navpoints stream in only at close range.

Advance through verified unexplored navs with a captured safe gate destination.
Each submitted leg finishes at a gate. This does
not enter gates or infer unexplored objects from another character's catalog.
"""
import argparse
import json
import math
import time

from explore_sector import execute, live_world, read_snapshot
from exploration_plan import plan, is_gate
from nav_catalog import STORE, refresh


def gate_pass(world, hazards=(), wells=()):
    """Each submitted leg ends at a captured safe gate, never an ordinary nav.

    Destination-only non-gates cannot be via points. Leave those unexplored.
    The server rejects a route ending at the departure gate even with distant
    vias, so exclude gates within 15 km of the departure position.
    """
    player = world['recent'].get('player')
    omitted = []
    catalog = []
    for point in world['catalog']:
        if point.get('kind') == 'map_object' and point.get('navigation_present') and point.get('nav_type') == 2:
            gate = is_gate(point)
            departure = (gate and player and 'raw_xyz' in point
                         and math.dist(player['raw_xyz'], point['raw_xyz']) < 15000)
            if not gate or departure:
                omitted.append(point)
                continue
        catalog.append(point)
    result = plan(dict(world, catalog=catalog), hazards, wells=wells)
    result['deferred_destination_only'] = [p['name'] for p in omitted
                                          if p.get('visited') is False]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--sector', required=True)
    args = parser.parse_args()
    for iteration in range(64):
        world = live_world(args.session, args.sector)
        # Recorder replacement can briefly deny a catalog read on Windows.
        for attempt in range(10):
            try:
                saved = refresh()
                break
            except (PermissionError, FileNotFoundError):
                if attempt == 9:
                    raise
                time.sleep(.05)
        wells_path = STORE.with_name('gravity_wells.json')
        wells = read_snapshot(wells_path).get(args.sector, []) if wells_path.exists() else []
        result = gate_pass(world, saved.get('hazards', {}).get(args.sector, {}).values(), wells=wells)
        result.update(proxy_session=args.session, generated=time.time())
        print(f"{args.sector}: {len(result['exploration_route'])} unexplored safe targets; "
              f"{len(result['blocked'])} blocked", flush=True)
        if not result['exploration_route']:
            STORE.with_name('frontier_plan.json').write_text(json.dumps(result, indent=2))
            if result['final_gate']:
                execute(result)
            else:
                print('No safe gate is currently captured; staying at the last safe nav.', flush=True)
            print('Sector pass complete; unknown distant navpoints may still exist.', flush=True)
            return
        # Replan at gates. 0x009b toggles warp: sending another route during
        # warp stops the ship rather than updating its remaining waypoints.
        if not result['executable']:
            raise RuntimeError('Gate-ended exploration unavailable: ' + result['execution_blocker'])
        STORE.with_name('frontier_plan.json').write_text(json.dumps(result, indent=2))
        execute(result)
        time.sleep(2)
    raise RuntimeError('Exploration did not converge after 64 passes')


if __name__ == '__main__':
    main()
