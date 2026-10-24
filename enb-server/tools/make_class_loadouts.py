"""Generate class-legal elite sandbox presets from the imported public catalog.

Beams provide simple default loadouts; launchers and ammo can be equipped separately.
Ranking prefers maximum legal tech, then capacity/warp speed/beam DPS. Devices
prefer the useful support items already exercised by the equipment simulation.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import TypedDict, cast
from build import ROOT
from import_items import ItemDefinition
from import_classes import ClassSkills

class GrantItem(TypedDict):
    slot: int
    template: int
    quality: float

class Loadouts(TypedDict):
    revision: int
    credits: int
    equipment: list[GrantItem]
    by_class: dict[str,list[GrantItem]]

def scalar(item: ItemDefinition, index: int) -> float:
    for field in item['fields']:
        if field['index'] == index:
            value = field['value']['value']
            return float(value) if isinstance(value,(int,float)) else 0.
    return 0.

def generate(root: Path = ROOT) -> None:
    items = cast(list[ItemDefinition],json.loads((root/'data/items.json').read_text()))
    classes = cast(list[ClassSkills],json.loads((root/'data/class_skills.json').read_text()))
    path = root/'data/sandbox_loadout.json'
    presets = cast(Loadouts,json.loads(path.read_text()))
    presets['by_class'] = {}
    for record in classes:
        index = record['class_index']
        code = ('te','tt','ts','jd','js','je','pw','pp','ps')[index]
        if code == 'je':
            presets['by_class'][code] = presets['equipment']
            continue
        race, profession = divmod(index,3)
        skills = {skill['id']:skill['level'] for skill in record['skills']}

        def legal(item: ItemDefinition, skill: int) -> bool:
            return (item['level'] <= skills.get(skill,0)
                and not int(scalar(item,18)) & (1 << race)
                and not int(scalar(item,3)) & (1 << profession)
                and not (race == 1 and scalar(item,17) == 2 or race == 2 and scalar(item,17) == 1)
                and all(scalar(item,field) <= limit for field,limit in ((4,50),(12,50),(32,50),(14,150))))

        def best(kind: int, skill: int, field: int) -> ItemDefinition:
            candidates=[item for item in items if item['item_type']==kind and legal(item,skill)]
            return max(candidates,key=lambda item:(item['level'], scalar(item,field)/(max(.1,scalar(item,21))) if kind==14 else scalar(item,field), -item['id']))

        equipment: list[GrantItem]=[]
        for slot,kind,skill,field in ((0,2,55,24),(1,7,45,16),(2,6,20,33)):
            equipment.append(GrantItem(slot=slot,template=best(kind,skill,field)['id'],quality=2.))
        beam=best(14,1,5)
        for slot in range(3,3+(5,4,4,5,4,3,6,5,4)[index]):
            equipment.append(GrantItem(slot=slot,template=beam['id'],quality=2.))
        preferred=(2455,2357,2320,2332,2370,2362,2316)
        devices=[item for identity in preferred for item in items if item['id']==identity and legal(item,18)]
        remaining=sorted((item for item in items if item['item_type']==11 and legal(item,18) and item not in devices),
            key=lambda item:(item['level'],len(item['equipped']),item['price'],-item['id']),reverse=True)
        devices.extend(remaining)
        for slot,item in zip(range(9,9+(4,5,5,4,5,6,3,4,5)[index]),devices):
            equipment.append(GrantItem(slot=slot,template=item['id'],quality=2.))
        presets['by_class'][code]=equipment
    path.write_text(json.dumps(presets,indent=2)+'\n')
    print('Generated elite legal gear for nine classes (default beam loadouts)')

if __name__=='__main__': generate()
