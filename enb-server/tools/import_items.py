"""Export public item templates/effects/recipes without executing the SQL dump.

The field numbers and effect wire metadata follow ItemBaseSQL/BuildItemBasePacket.
Instances and starting equipment slots are separate runtime data, not templates.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import re
from pathlib import Path
from typing import Literal, TypedDict
import unicodedata

from build import ROOT
from import_sector import SqlRow, number, public_rows

FieldKind = Literal['integer', 'float', 'text']


class FieldValue(TypedDict):
    kind: FieldKind
    value: int | float | str


class ItemField(TypedDict):
    index: int
    value: FieldValue


class Modifier(TypedDict):
    stat: str
    operation: int
    value: float


class ItemEffect(TypedDict):
    id: int
    name: str
    description: str
    tooltip: str
    buff_name: str
    flag1: int
    flag2: int
    visual: int
    values: list[float]
    modifiers: list[Modifier]


class Activation(TypedDict):
    recharge: float
    range: float
    energy: float


class CraftingData(TypedDict):
    cost: int
    cost_base: int
    cost_modifier: int
    manufacture_difficulty: int
    analyze_difficulty: int
    dismantle_difficulty: int
    recipe_difficulty: int


class ItemDefinition(TypedDict):
    id: int
    name: str
    description: str
    manufacturer: str
    category: int
    subcategory: int
    item_type: int
    level: int
    icon: int
    asset: int
    price: int
    max_stack: int
    flags: int
    use_effect: int
    fields: list[ItemField]
    activated: list[ItemEffect]
    equipped: list[ItemEffect]
    activation: Activation
    components: list[int]
    refines_into: int
    ammo_type: int
    crafting: CraftingData


# (wire field, SQL column, scalar representation), grouped by item type.
MAPPINGS: dict[int, tuple[str, str, list[tuple[int, str, FieldKind]]]] = {
    2: ('item_shield', 'Shield Tech', [(24,'cap_100','integer'),(26,'recharge_100','float')]),
    6: ('item_engine', 'Engine Tech', [(31,'thrust_100','integer'),(33,'warp','integer'),
                                      (34,'warp_drain_100','float'),(29,'signature_100','integer')]),
    7: ('item_reactor', 'Reactor Tech', [(16,'cap_100','integer'),(20,'recharge_100','float')]),
    10: ('item_ammo', 'Projectile Weapon', [(5,'damage_100','integer'),(6,'damage_type','integer')]),
    11: ('item_device', 'Device Tech', [(9,'energy_100','float'),(7,'range_100','integer')]),
    14: ('item_beam', 'Beam Weapon', [(5,'damage_100','integer'),(6,'damage_type','integer'),
                                    (19,'range_100','integer'),(9,'energy_100','float'),(21,'reload_100','float')]),
    15: ('item_missile', 'Missile Weapon', [(9,'energy_100','float'),(21,'reload_100','float'),(22,'ammo_per_shot','integer')]),
    16: ('item_projectile', 'Projectile Weapon', [(9,'energy_100','float'),(19,'range_100','integer'),
                                              (21,'reload_100','float'),(22,'ammo_per_shot','integer')]),
}


def client_text(value: str | None) -> str:
    return unicodedata.normalize('NFKD', value or '').encode('ascii','ignore').decode('ascii').replace('\0','')


class RefiningLinkIssue(TypedDict):
    item: int
    target: str
    reason: str


def resolve_refining_links(items: list[ItemDefinition]) -> list[RefiningLinkIssue]:
    """Restore SetRefineInfo links without overwriting explicit SQL recipes.

    The archived item_refine table may be empty. Descriptions identify the
    product; its recipe must consist solely of this input. Only an absent recipe
    uses C++'s two-input fallback. Ambiguous names/recipes remain diagnostics.
    """
    names: defaultdict[str, list[ItemDefinition]] = defaultdict(list)
    for item in items: names[item['name'].casefold()].append(item)
    rejected: list[RefiningLinkIssue] = []
    for item in items:
        if item['refines_into']: continue
        description = item['description'].replace('\\n', '\n').replace('\\r', '\r')
        match = re.search(r'(?:^|\n)\s*Refines\s+(?:to|into):\s*([^\n\\]+)', description, re.IGNORECASE)
        if match is None: continue
        name = match[1].strip().rstrip('.')
        targets = names.get(name.casefold(), [])
        if len(targets) != 1 or targets[0]['id'] == item['id']:
            rejected.append({'item': item['id'], 'target': name, 'reason': 'Missing or ambiguous product name'})
            continue
        target = targets[0]
        if target['components'] and set(target['components']) != {item['id']}:
            rejected.append({'item': item['id'], 'target': name, 'reason': 'Product recipe does not consume only this input'})
            continue
        item['refines_into'] = target['id']
        if not target['components']: target['components'] = [item['id'], item['id']]
    return rejected


def field(index: int, kind: FieldKind, value: int | float | str) -> ItemField:
    return {'index': index, 'value': {'kind': kind, 'value': value}}


class PublicItems:
    def __init__(self, dump: Path) -> None:
        tables = frozenset({'item_base','item_manufacturer_base','item_other_req','item_ammo_type',
                            'item_type','item_effect_base','item_effect_container','item_effects',
                            'item_manufacture','item_refine'} | {mapping[0] for mapping in MAPPINGS.values()})
        self.rows: dict[str, dict[int, SqlRow]] = defaultdict(dict)
        self.effects: dict[int, list[SqlRow]] = defaultdict(list)
        for table, row in public_rows(dump, tables):
            if table == 'item_effects':
                self.effects[int(number(row,'ItemID'))].append(row)
                continue
            key = ('EffectID' if table == 'item_effect_base' else
                   'ItemID' if table == 'item_effect_container' else
                   'id' if table in {'item_base','item_manufacturer_base','item_type','item_ammo_type'} else 'item_id')
            self.rows[table][int(number(row,key))] = row

    def effect(self, reference: SqlRow) -> tuple[bool, ItemEffect]:
        identity = int(number(reference,'item_effect_base_id'))
        base = self.rows['item_effect_base'].get(identity)
        if base is None:
            raise ValueError(f'Item effect refers to missing definition {identity}')
        variables = [number(reference,f'Var{index}Data') for index in range(1,4)]
        modifiers: list[Modifier] = []
        for prefix, count in [('Var',3),('Constant',2)]:
            for index in range(1,count+1):
                operation = int(number(base,f'{prefix}{index}Type'))
                if operation:
                    value = variables[index-1] if prefix == 'Var' else number(base,f'Constant{index}Value')
                    modifiers.append({'stat': client_text(base.get(f'{prefix}{index}Stat')),
                                      'operation': operation, 'value': value})
        return number(base,'EffectType') == 1, {
            'id':identity,'name':client_text(base.get('Name')),'description':client_text(base.get('Description')),
            'tooltip':client_text(base.get('Tooltip')),'buff_name':client_text(base.get('Buff_Name')),
            'flag1':int(number(base,'flag1')),'flag2':int(number(base,'flag2')),
            'visual':int(number(base,'VisualEffect')),'values':variables,'modifiers':modifiers}

    def item(self, identity: int, base: SqlRow) -> ItemDefinition:
        kind = int(number(base,'type'))
        level = int(number(base,'level'))
        fields: dict[int, ItemField] = {}
        details: SqlRow = {}
        if kind in MAPPINGS:
            table, requirement, mapping = MAPPINGS[kind]
            details = self.rows[table].get(identity,{})
            for index, column, representation in mapping:
                value = number(details,column)
                fields[index] = field(index,representation,int(value) if representation == 'integer' else value)
            if kind == 10 and number(self.rows['item_ammo_type'].get(int(number(details,'ammo_type_id')),{}),'sub_category') == 102:
                requirement = 'Missile Weapon'
                fields[15] = field(15,'integer',int(number(details,'maneuv_100')))
                fields[19] = field(19,'integer',int(number(details,'range_100')))
            fields[11] = field(11,'text',requirement)
            fields[13] = field(13,'text',f'Level {level} {requirement}')
            if kind in (14,15,16):
                fields[2] = field(2,'integer',1)
            if kind in (10,15,16):
                ammo = self.rows['item_ammo_type'].get(int(number(details,'ammo_type_id')), {})
                fields[1] = field(1,'text',client_text(ammo.get('name')))
        for index, column in [(3,'rest_prof'),(18,'rest_race')]:
            fields[index] = field(index,'integer',int(number(details,column)))
        fields[17] = field(17,'integer',0)
        requirements = self.rows['item_other_req'].get(identity,{})
        requirement_fields: list[tuple[int,str,FieldKind]] = [(4,'combat_lvl','integer'),(12,'explore_lvl','integer'),
                (14,'overall_lvl','integer'),(32,'trade_level','integer'),(28,'over_skill_lvl','integer'),
                (10,'energy_drain','float'),(25,'shield_drain','float')]
        for index, column, representation in requirement_fields:
            value = number(requirements,column)
            if value:
                fields[index] = field(index,representation,int(value) if representation == 'integer' else value)
        if requirements.get('other_skill') not in (None,'','0','NULL'):
            fields[27] = field(27,'text',client_text(requirements['other_skill']))
        activated: list[ItemEffect] = []
        equipped: list[ItemEffect] = []
        for reference in self.effects.get(identity,[]):
            active, effect = self.effect(reference)
            (activated if active else equipped).append(effect)
        container = self.rows['item_effect_container'].get(identity,{})
        recipe = self.rows['item_manufacture'].get(identity,{})
        flags = sum(bit for column, bit in [('no_trade',1),('unique',4),('no_store',8),
                    ('no_destroy',16),('no_manu',128)] if number(base,column) == 1)
        return {'id':identity,'name':client_text(base.get('name'))[:63],
                'description':client_text(base.get('description')),'manufacturer':client_text(
                    self.rows['item_manufacturer_base'].get(int(number(base,'manufacturer')),{}).get('name')),
                'category':int(number(base,'category')),'subcategory':int(number(base,'sub_category')),
                'item_type':kind,'level':level,'icon':int(number(base,'2d_asset')),'asset':int(number(base,'3d_asset')),
                'price':max(0,int(number(base,'price'))),'max_stack':max(1,int(number(base,'max_stack'))),
                'flags':flags,'use_effect':int(number(details,'fire_effect')),
                'fields':[fields[index] for index in sorted(fields)],'activated':activated,'equipped':equipped,
                'activation':{'recharge':number(container,'RechargeTime'),'range':number(container,'_Range'),
                              'energy':number(container,'EnergyUse')},
                'components':[int(number(recipe,f'comp_{index}')) for index in range(1,7)
                              if number(recipe,f'comp_{index}') > 0],
                'refines_into':int(number(self.rows['item_refine'].get(identity,{}),'refine_id')),
                'ammo_type':int(number(details,'ammo_type_id')),
                'crafting': {'cost': max(0, int(number(base, 'man_cost'))),
                    'cost_base': max(0, int(number(base, 'man_cost_base'))),
                    'cost_modifier': max(0, int(number(base, 'man_cost_mod'))),
                    'manufacture_difficulty': int(number(base, 'man_dif')),
                    'analyze_difficulty': int(number(base, 'ana_dif')),
                    'dismantle_difficulty': int(number(base, 'dis_dif')),
                    'recipe_difficulty': int(number(recipe, 'difficulty'))}}

    def export(self, output: Path) -> int:
        items = [self.item(identity, base) for identity, base in sorted(self.rows['item_base'].items())]
        rejected = resolve_refining_links(items)
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(items,separators=(',',':'),allow_nan=False)+'\n',encoding='utf8')
        output.with_suffix('.refining.json').write_text(json.dumps({'unresolved': rejected}, indent=2)+'\n', encoding='utf8')
        return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'data/items.json')
    args = parser.parse_args()
    print(f'Exported {PublicItems(args.dump).export(args.output)} public item templates to {args.output}')


if __name__ == '__main__':
    main()
