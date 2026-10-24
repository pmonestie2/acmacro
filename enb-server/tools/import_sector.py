"""Export static sector content from the archived public MySQL dump.

Only sectors, systems, sector_objects and sector_nav_points are read. The dump
is never executed and account/character tables are not imported.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
import json
import math
from pathlib import Path
import re
from typing import TypedDict

from build import ROOT
from import_mob import mysql_rows

SECTOR_TABLES = frozenset({'sectors', 'systems', 'sector_objects', 'sector_nav_points'})
PUBLIC_TABLES = SECTOR_TABLES | frozenset({'skills', 'skill_abilities', 'skill_levels',
    'starbases', 'starbase_rooms', 'starbase_terminals', 'starbase_npcs',
    'starbase_npc_avatar_templates', 'starbase_vendors',
    'starbase_vender_groups', 'starbase_vender_inventory',
    'assets', 'sector_objects_harvestable', 'sector_objects_harvestable_restypes',
    'sector_objects_harvestable_oretypes', 'base_ore_list', 'asteroid_content_selection',
    'mob_base', 'mob_items', 'mob_spawn_group', 'mob_type', 'sector_objects_mob', 'effects',
    'item_base', 'item_engine', 'item_reactor', 'item_shield', 'item_beam', 'item_manufacturer_base',
    'item_projectile', 'item_missile', 'item_ammo', 'item_ammo_type', 'item_device',
    'item_other_req', 'item_effect_base', 'item_effect_container', 'item_effects',
    'item_manufacture', 'item_refine', 'item_type', 'missions', 'factions', 'faction_matrix'})
SqlRow = dict[str, str | None]


def public_rows(path: Path, tables: frozenset[str] = SECTOR_TABLES) -> Iterator[tuple[str, SqlRow]]:
    if not tables <= PUBLIC_TABLES:
        raise ValueError('Only public content tables can be imported')
    columns: dict[str, list[str]] = {}
    current: str | None = None
    with path.open(encoding='utf8', errors='replace') as stream:
        for line in stream:
            match = re.match(r'CREATE TABLE `([^`]+)`', line)
            if match:
                name = match[1]
                current = name if name in tables else None
                if current:
                    columns[current] = []
            elif current and line.startswith('  `'):
                columns[current].append(line.split('`')[1])
            elif current and line.startswith(')'):
                current = None
            match = re.match(r'INSERT INTO `([^`]+)` VALUES ', line)
            if match and match[1] in tables:
                table = match[1]
                for row in mysql_rows(line[match.end():]):
                    if len(row) != len(columns[table]):
                        raise ValueError(f'Content row has wrong field count: {table}')
                    yield table, dict(zip(columns[table], row))


def number(row: SqlRow, key: str, default: float = 0.) -> float:
    value = row.get(key)
    return default if value is None or value.lower() == 'null' else float(value)


class NavigationObject(TypedDict):
    id: int
    name: str
    object_type: int
    asset: int
    position: list[float]
    orientation: list[float]
    hsv: list[float]
    scale: float
    signature: float
    gate_destination: int | None
    source_id: int
    navigation_type: int
    navigation_present: bool
    huge: bool


def export_sector(dump: Path, sector_id: int, output: Path) -> None:
    sector: SqlRow | None = None
    objects: list[SqlRow] = []
    systems: dict[int, str] = {}
    navinfo: dict[int, SqlRow] = {}
    for table, row in public_rows(dump):
        if table == 'sectors' and int(row['sector_id'] or '0') == sector_id:
            sector = row
        elif table == 'systems':
            systems[int(row['system_id'] or '0')] = row['name'] or ''
        elif table == 'sector_objects' and int(number(row, 'sector_id')) == sector_id:
            objects.append(row)
        elif table == 'sector_nav_points':
            navinfo[int(row['sector_object_id'] or '0')] = row
    if sector is None:
        raise ValueError(f'Sector {sector_id} is absent from content dump')
    navpoints: list[NavigationObject] = []
    for row in objects:
        identifier = int(row['sector_object_id'] or '0')
        kind = int(number(row, 'type'))
        # MOB/resource fields are spawning rules, not static map meshes.
        if kind not in (3, 11, 12, 37) and identifier not in navinfo:
            continue
        if kind in (0, 1, 38):
            continue
        gate = int(number(row, 'gate_to'))
        info = navinfo.get(identifier, {})
        nav_type = int(number(info, 'nav_type', 1. if kind in (3, 11, 12) else 0.))
        orientation = [number(row, f'orientation_{axis}') for axis in 'uvwz']
        length = math.sqrt(sum(value*value for value in orientation))
        orientation = [value / length for value in orientation] if length > 0.00001 else [0., 0., 0., 1.]
        navpoints.append({'id': 100000 + identifier, 'source_id': identifier,
                          'name': row['name'] or f'Object {identifier}',
                          # Database object types are not native Create.Type.
                          # Unsupported static content remains decorative until
                          # its behavior has an actor implementation.
                          'object_type': kind if kind in (3, 11, 12, 37) else 37,
                          'asset': int(number(row, 'base_asset_id')),
                          'position': [number(row, f'position_{axis}') for axis in 'xyz'],
                          'orientation': orientation,
                          'hsv': [number(row, field) for field in ('h', 's', 'v')],
                          # Object::SetScale treats database zero as scale 1.
                          'scale': number(row, 'scale', 1.) or 1., 'signature': number(info, 'signature', 5000.),
                          'navigation_type': nav_type,
                          'navigation_present': bool(number(row, 'appears_in_radar')) and nav_type > 0,
                          'huge': bool(number(info, 'is_huge')),
                          'gate_destination': gate if gate else None})
    data = {'id': sector_id, 'name': sector['name'],
            'system': systems[int(number(sector, 'system_id'))],
            'backdrop_asset': int(number(sector, 'backdrop_asset')),
            'bounds': {'min': [number(sector, f'{axis}_min') for axis in 'xyz'],
                       'max': [number(sector, f'{axis}_max') for axis in 'xyz']},
            'navpoints': navpoints, 'mobs': [],
            'source': 'Archived net7 public sector/static-object definitions; dynamic spawns not imported'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, indent=2) + '\n', encoding='utf8')
    print(f'Exported {sector_id} {sector["name"]}: {len(navpoints)} static objects from {len(objects)} sector definitions -> {output}')
    for nav in navpoints:
        if nav['object_type'] in (11, 12):
            print(f'  {nav["id"]}: {nav["name"]}; destination={nav["gate_destination"]}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--sector', type=int, default=1076)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/asteroid_belt_alpha.json')
    args = parser.parse_args()
    export_sector(args.dump, args.sector, args.output)


if __name__ == '__main__':
    main()
