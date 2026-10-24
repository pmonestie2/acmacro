"""Export deterministic MOB instances from public sector spawn rules.

The archived schema separates a spawn object's location, its count/radius/timer,
and its list of eligible MOB templates. Resolve those relationships explicitly;
never treat a spawn marker as a rendered monster or read player/account tables.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from typing import TypedDict

from build import ROOT
from import_asset_radii import load_radii
from import_sector import SqlRow, number, public_rows


class MobWeapon(TypedDict):
    item: int
    item_type: int
    damage: float
    damage_type: int
    reload: float
    effect: int


class SpawnRegion(TypedDict):
    center: list[float]
    radius: float


class MobSpawn(TypedDict):
    source_id: int
    faction: int
    aggression: int
    spawn_region: SpawnRegion
    mob_type: int
    shield_modifier: float
    damage_modifier: float
    range_modifier: float
    weapon: MobWeapon | None
    template_id: int
    spawn_group: int
    name: str
    level: int
    asset: int
    radius: float
    spawn: list[float]
    scale: float
    hsv: list[float]
    respawn_seconds: float
    aggressive: bool


class PublicSpawns:
    """Index public relationships once for importing an entire universe."""

    def __init__(self, dump: Path, *, asset_radii: Path = ROOT / 'data/asset_radii.json') -> None:
        self.radii = load_radii(asset_radii)
        self.objects: defaultdict[int, list[SqlRow]] = defaultdict(list)
        self.rules: dict[int, SqlRow] = {}
        self.templates: dict[int, SqlRow] = {}
        self.groups: defaultdict[int, list[int]] = defaultdict(list)
        self.missing_models: dict[int, str] = {}
        item_types: dict[int,int] = {}
        details: dict[int,SqlRow] = {}
        candidates: list[SqlRow] = []
        self.weapons: dict[int,MobWeapon] = {}
        tables = frozenset({'sector_objects', 'sector_objects_mob', 'mob_spawn_group', 'mob_base',
            'mob_items','item_base','item_beam','item_missile','item_projectile'})
        for table, row in public_rows(dump, tables):
            if table == 'sector_objects' and int(number(row, 'type')) == 0:
                self.objects[int(number(row, 'sector_id'))].append(row)
            elif table == 'sector_objects_mob':
                self.rules[int(number(row, 'mob_id'))] = row
            elif table == 'mob_base':
                identity = int(number(row, 'mob_id'))
                if number(row, 'base_asset_id') > 0:
                    self.templates[identity] = row
                else:
                    self.missing_models[identity] = row.get('name') or 'Unnamed MOB'
            elif table == 'mob_spawn_group':
                self.groups[int(number(row, 'spawn_group_id'))].append(int(number(row, 'mob_id')))
            elif table == 'item_base': item_types[int(number(row,'id'))] = int(number(row,'type'))
            elif table in ('item_beam','item_missile','item_projectile'): details[int(number(row,'item_id'))]=row
            elif table == 'mob_items': candidates.append(row)
        for row in sorted(candidates,key=lambda row:number(row,'id')):
            mob, item = int(number(row,'mob_id')), int(number(row,'item_base_id'))
            kind=item_types.get(item)
            if mob in self.weapons or kind not in (14,15,16) or item not in details: continue
            source=details[item]
            damage,reload=number(source,'damage_100'),number(source,'reload_100')
            if kind == 14 and (damage <= 0 or reload < .1): continue
            self.weapons[mob]=MobWeapon(item=item,item_type=kind,damage=damage,
                damage_type=int(number(source,'damage_type',1)),reload=reload,effect=int(number(source,'fire_effect')))


    def for_sector(self, sector_id: int) -> list[MobSpawn]:
        return resolve_spawns(self.objects[sector_id], self.rules, self.templates, self.groups,
                              self.weapons, self.radii)


def resolve_spawns(objects: list[SqlRow], rules: dict[int, SqlRow],
                   templates: dict[int, SqlRow], groups: dict[int, list[int]],
                   weapons: dict[int,MobWeapon] | None = None,
                   radii: dict[int, float] | None = None) -> list[MobSpawn]:
    spawns: list[MobSpawn] = []
    for obj in sorted(objects, key=lambda row: number(row, 'sector_object_id')):
        group = int(number(obj, 'sector_object_id'))
        rule = rules.get(group)
        eligible = [templates[key] for key in groups.get(group, []) if key in templates]
        if rule is None or not eligible:
            continue
        count = int(number(rule, 'mob_count'))
        # Source identities reserve 100 slots per group; the public data includes
        # an 80-actor group, so the former 64-actor importer bound lost content.
        if not 0 <= count <= 100:
            raise ValueError(f'Spawn group {group} has unsupported count {count}')
        if count == 0:
            continue
        center = [number(obj, 'position_' + axis) for axis in 'xyz']
        radius = number(rule, 'mob_spawn_radius')
        if not math.isfinite(radius) or radius < 0 or any(not math.isfinite(value) or abs(value) + radius > 2_000_000 for value in center):
            raise ValueError(f'Spawn group {group} has an invalid roaming region')
        region: SpawnRegion = {'center': center, 'radius': radius}
        for index in range(count):
            mob = eligible[index % len(eligible)]
            asset = int(number(mob, 'base_asset_id'))
            if radii is not None and asset not in radii:
                raise ValueError(f'MOB asset {asset} has no native radius')
            faction = int(number(mob, 'faction_id'))
            aggression = int(number(mob, 'aggressiveness'))
            if not 0 <= faction <= 0xffffffff or not 0 <= aggression <= 255:
                raise ValueError(f'MOB {mob.get("mob_id")} has invalid faction/aggression metadata')
            angle = index * math.pi * (3 - math.sqrt(5))
            offset = radius * math.sqrt((index + .5) / count)
            spawn = [center[0] + math.cos(angle) * offset, center[1] + math.sin(angle) * offset, center[2]]
            spawns.append({'source_id': group * 100 + index,
                'faction': faction, 'aggression': aggression, 'spawn_region': region,
                'mob_type': int(number(mob,'type')),
                'shield_modifier': number(mob,'shield_modifier',1),
                'damage_modifier': number(mob,'damage_modifier',1),
                'range_modifier': number(mob,'range_modifier',1),
                'weapon': (weapons or {}).get(int(number(mob,'mob_id'))), 'template_id': int(number(mob, 'mob_id')),
                'spawn_group': group, 'name': mob.get('name') or 'Unnamed MOB', 'level': int(number(mob, 'level')),
                'asset': asset, 'radius': radii[asset] if radii is not None else 0.,
                'spawn': spawn, 'scale': number(mob, 'scale') or 1.,
                'hsv': [number(mob, key) for key in ('h', 's', 'v')],
                'respawn_seconds': number(rule, 'respawn_time') or 30.,
                'aggressive': aggression >= 3})
    if len(spawns) > 256:
        raise ValueError('Sector exceeds the current 256 MOB limit')
    return spawns


def export_spawns(dump: Path, content: Path) -> int:
    data = json.loads(content.read_text())
    spawns = PublicSpawns(dump).for_sector(int(data['id']))
    data['mobs'] = spawns
    content.write_text(json.dumps(data, indent=2) + '\n')
    return len(spawns)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('content', type=Path)
    args = parser.parse_args()
    print(f'Exported {export_spawns(args.dump, args.content)} public MOB instances')


if __name__ == '__main__':
    main()
