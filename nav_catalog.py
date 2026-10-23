"""Keep discovered navpoint coordinates separate from transient mob positions."""
import csv
import json
import math
from pathlib import Path
import time
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parent
CAPTURE = ROOT / 'protocol/captures/multi_world/catalogs.json'
STORE = ROOT / 'navigation/navpoints.json'


class NavCatalogData(TypedDict):
    sectors: dict[str, dict[str, object]]
    hazards: dict[str, dict[str, object]]
    updated: float


def refresh(source: str | Path = CAPTURE, destination: str | Path = STORE) -> NavCatalogData:
    worlds = json.loads(Path(source).read_text())
    destination = Path(destination)
    saved = json.loads(destination.read_text()) if destination.exists() else {'sectors': {}}
    hazards = saved.setdefault('hazards', {})
    for session, world in worlds.items():
        groups = [(world['epoch'], world['catalog'])]
        groups += [(a['epoch'], a['objects']) for a in world.get('previous_epochs', [])]
        for epoch, objects in groups:
            for obj in objects:
                if ("v'rix" in (obj.get('name') or '').casefold() and obj.get('sector')
                        and 'raw_xyz' in obj):
                    # A position is an observed danger zone, not proof of an
                    # exact spawn point. Keep it across deaths and sessions.
                    zones = hazards.setdefault(obj['sector'], {})
                    xyz = obj['raw_xyz']
                    key = obj['name'] + ':' + ','.join(str(round(v/5000)) for v in xyz)
                    zones.setdefault(key, {'name':obj['name'], 'raw_xyz':xyz,
                        'source':'observed hostile species; possible respawn area',
                        'observed':obj.get('position_seen',obj['last_seen'])})
                if obj.get('kind') != 'map_object' or not obj.get('sector') or 'raw_xyz' not in obj:
                    continue
                name = obj.get('name', '').strip()
                resource_name = name.lower().startswith(('ice', 'rock', 'gas', 'crystal', 'hydrocarbon', 'asteroid'))
                if not name or (resource_name and not (obj.get('navigation_present') and obj.get('nav_type') in (1, 2))):
                    continue
                sector = saved['sectors'].setdefault(obj['sector'], {})
                key = obj['id'] + ':' + name
                if sector.get(key, {}).get('observed', 0) > obj.get('position_seen', 0):
                    continue
                sector[key] = {'id': obj['id'], 'name': name,
                               'raw_xyz': obj['raw_xyz'], 'map_xyz': [v / 1000 for v in obj['raw_xyz']],
                               'observed': obj.get('position_seen', obj['last_seen']),
                               'source': 'captured game map object'}
    for collection in (saved['sectors'], hazards):
        for points in collection.values():
            for obj in points.values():
                obj.pop('proxy_session', None)
                obj.pop('epoch', None)
    saved['updated'] = time.time()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    temporary.write_text(json.dumps(saved, indent=2))
    temporary.replace(destination)
    with destination.with_suffix('.csv').open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['sector', 'name', 'id', 'x', 'y', 'z', 'observed'])
        for sector, points in saved['sectors'].items():
            for obj in points.values():
                writer.writerow([sector, obj['name'], obj['id'], *obj['map_xyz'], obj['observed']])
    return cast(NavCatalogData, saved)


def nearby_contacts(point, objects, now=None, radius=15000, max_age=30, nonaggressive_names=(), nonaggressive_ids=()):
    """Conservatively reject stops near ships/NPCs; unknown hostility is not safe.

    Packet visibility is limited by the client's range. No observed contacts
    means no known danger, not proof that the endpoint is empty.
    """
    now = time.time() if now is None else now
    return [obj for obj in objects
            if obj.get('kind') == 'ship_or_npc'
            and (not obj.get('removal_observed') or "v'rix" in (obj.get('name') or '').casefold())
            and (obj.get('name') or '').casefold() not in nonaggressive_names
            and obj['id'] not in nonaggressive_ids
            and 'raw_xyz' in obj and now - obj.get('position_seen', 0) <=
                (float('inf') if "v'rix" in (obj.get('name') or '').casefold() else max_age)
            and math.dist(point, obj['raw_xyz']) < radius]


if __name__ == '__main__':
    saved = refresh()
    print(f"Saved {sum(len(v) for v in saved['sectors'].values())} navpoints in {len(saved['sectors'])} sectors")
