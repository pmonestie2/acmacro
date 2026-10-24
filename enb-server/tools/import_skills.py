"""Compile JE skill/ability IDs from public SQL and installed client descriptions.

The old skill_abilities.skill_id columns are mostly zero. The reliable mapping
is skill_levels: IDs below 170 are native ability indices, and its skill_id and
level identify the owning skill and minimum rank. Never infer that mapping from
array order. The generated JSON is portable and contains no player data.
"""
from __future__ import annotations

import argparse
import configparser
import json
from pathlib import Path
from typing import TypedDict

from build import ROOT
from import_sector import SqlRow, number, public_rows


class Skill(TypedDict):
    id: int
    name: str
    level: int
    active: bool


class Ability(TypedDict):
    id: int
    skill: int
    rank: int
    name: str
    description: str
    kind: str
    energy: float
    charge: float
    cooldown: float
    range: int
    radius: int
    destination: int | None


KINDS = {12: 'cloak', 14: 'contemplation', 15: 'wormhole', 23: 'environment',
         24: 'fold', 31: 'jumpstart', 53: 'leech'}
DESTINATIONS = {67: 1910, 66: 1070, 123: 4120, 131: 1705, 19: 1077, 26: 4520, 37: 2210}


def export_skills(dump: Path, client_ini: Path, output: Path) -> None:
    rows: dict[str, list[SqlRow]] = {name: [] for name in ('skills', 'skill_abilities', 'skill_levels')}
    for table, row in public_rows(dump, frozenset(rows)):
        rows[table].append(row)
    skills: list[Skill] = [{'id': int(number(row, 'skill_id')), 'name': row['name'] or '',
        'level': int(number(row, 'explorer_max_level')), 'active': bool(number(row, 'is_activated'))}
        for row in rows['skills'] if number(row, 'explorer_max_level') > 0]
    by_id = {skill['id']: skill for skill in skills}
    names = {int(number(row, 'ability_id')): row['name'] or '' for row in rows['skill_abilities']}
    names[0] = 'Advanced Cloak'
    descriptions = configparser.ConfigParser(interpolation=None, strict=False)
    descriptions.read(client_ini, encoding='latin1')
    abilities: list[Ability] = []
    for row in rows['skill_levels']:
        index, skill_id, rank = (int(number(row, key)) for key in ('skill_level_id', 'skill_id', 'level'))
        skill = by_id.get(skill_id)
        if skill is None or not skill['active'] or not 0 <= index < 170 or rank > skill['level']:
            continue
        name = names[index]
        section = next((section for section in descriptions if section.casefold() == name.casefold()), None)
        if section is None:
            raise ValueError(f'Client description missing for ability {index}: {name}')
        kind = KINDS[skill_id]
        abilities.append({'id': index, 'skill': skill_id, 'rank': rank, 'name': name,
            'description': descriptions[section].get('desc', ''), 'kind': kind,
            'energy': float(descriptions[section]['cost']) if kind != 'jumpstart' else 140.,
            'charge': 3. if kind in ('cloak', 'wormhole') else 7. if kind == 'jumpstart' else 1.,
            'cooldown': 5. if kind == 'cloak' else 10.,
            'range': 2750 if kind == 'jumpstart' else 6000 if kind == 'fold' else 3000,
            'radius': 3000 if index == 53 else 1500 if index == 109 else 1000 if index in (51, 56) else 0,
            'destination': DESTINATIONS.get(index)})
    if len(skills) != 18 or len(abilities) != 27:
        raise ValueError(f'Unexpected JE catalog: {len(skills)} skills / {len(abilities)} abilities')
    output.write_text(json.dumps({'skills': skills, 'abilities': abilities}, indent=2) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--client-ini', type=Path,
        default=Path('C:/Program Files (x86)/EA GAMES/Earth & Beyond/Data/client/ini/cskill_ability_t.ini'))
    parser.add_argument('--output', type=Path, default=ROOT / 'data/je_skills.json')
    args = parser.parse_args()
    export_skills(args.dump, args.client_ini, args.output)
    print('Exported 18 JE skills and 27 native ability indices')


if __name__ == '__main__':
    main()
