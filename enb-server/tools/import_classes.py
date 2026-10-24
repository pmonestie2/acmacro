"""Export all nine class skill ceilings from public SQL, without executing SQL.

Supported ability handlers retain the existing verified ability records. Other
native abilities retain their identity/name but explicitly reject activation
until their actual gameplay handler has been implemented.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import TypedDict
from build import ROOT
from import_sector import SqlRow, number, public_rows
from import_skills import Skill, Ability

COLUMNS = ('enforcer', 'tradesman', 'scout', 'defender', 'seeker', 'explorer',
           'warrior', 'privateer', 'sentinal')

class ClassSkills(TypedDict):
    class_index: int
    skills: list[Skill]
    abilities: list[Ability]

def export_classes(dump: Path, output: Path) -> None:
    rows: dict[str, list[SqlRow]] = {name: [] for name in ('skills','skill_levels','skill_abilities')}
    for table, row in public_rows(dump, frozenset(rows)): rows[table].append(row)
    supported: dict[int, Ability] = {row['id']: row for row in json.loads((ROOT/'data/je_skills.json').read_text())['abilities']}
    names = {int(number(row,'ability_id')): str(row.get('name') or '') for row in rows['skill_abilities']}
    names[0] = 'Advanced Cloak'
    classes: list[ClassSkills] = []
    for index, column in enumerate(COLUMNS):
        skills: list[Skill] = [{'id': int(number(row,'skill_id')), 'name': str(row.get('name') or ''),
            'level': int(number(row,column+'_max_level')), 'active': bool(number(row,'is_activated'))}
            for row in rows['skills'] if number(row,column+'_max_level') > 0]
        by_id = {skill['id']: skill for skill in skills}
        abilities: list[Ability] = []
        for row in rows['skill_levels']:
            identity, owner, rank = (int(number(row,key)) for key in ('skill_level_id','skill_id','level'))
            skill = by_id.get(owner)
            if skill is None or not skill['active'] or not 0 <= identity < 170 or not 1 <= rank <= skill['level']: continue
            known = supported.get(identity)
            if known is not None:
                if known['skill'] != owner or known['rank'] != rank: raise ValueError('Ability identity disagrees with public skill levels')
                abilities.append(known.copy())
            else:
                abilities.append(Ability(id=identity, skill=owner, rank=rank, name=names.get(identity,f'Ability {identity}'),
                    description='Gameplay handler not implemented yet.', kind='unsupported',energy=0.,charge=0.,
                    cooldown=0.,range=0,radius=0,destination=None))
        classes.append(ClassSkills(class_index=index,skills=skills,abilities=abilities))
    if len(classes) != 9 or any(not record['skills'] for record in classes): raise ValueError('Incomplete class export')
    output.write_text(json.dumps(classes,indent=2)+'\n')

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'data/class_skills.json')
    args=parser.parse_args()
    export_classes(args.dump,args.output)
    print('Exported all nine public class skill catalogs')

if __name__=='__main__': main()
