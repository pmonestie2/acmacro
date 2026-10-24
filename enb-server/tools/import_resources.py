"""Resolve public resource fields into stable, renderable asteroid/cloud/hulk nodes.

The field marker is a spawn rule, never a giant asteroid at the field center.
Shapes and legacy model sets follow FieldClass.cpp; deterministic sampling keeps
node identities/positions reproducible. Ore metadata is retained for mining work.
"""
from __future__ import annotations

from collections import defaultdict
import math
from pathlib import Path
from typing import TypedDict

from build import ROOT
from import_asset_radii import load_resource_radii
from import_sector import SqlRow, number, public_rows


class Ore(TypedDict):
    item_id: int
    frequency: float


class ResourceNode(TypedDict):
    id: int
    field_id: int
    level: int
    resource_type: int
    name: str
    object_type: int
    asset: int
    radius: float
    position: list[float]
    scale: float
    hsv: list[float]
    orientation: list[float]
    signature: float
    navigation_present: bool
    navigation_type: int
    huge: bool
    gate_destination: None
    ore_items: list[Ore]


class PublicResources:
    def __init__(self, dump: Path, *, asset_radii: Path = ROOT / 'data/asset_radii.json') -> None:
        self.radii = load_resource_radii(asset_radii)
        self.objects: defaultdict[int, list[SqlRow]] = defaultdict(list)
        self.rules: dict[int, SqlRow] = {}
        self.models: defaultdict[int, list[int]] = defaultdict(list)
        self.ores: defaultdict[int, list[Ore]] = defaultdict(list)
        self.base_ores: defaultdict[int, list[Ore]] = defaultdict(list)
        self.subcategories: defaultdict[int, set[int]] = defaultdict(set)
        self.items: dict[int, SqlRow] = {}
        self.assets: dict[int, SqlRow] = {}
        tables = frozenset({'sector_objects', 'sector_objects_harvestable',
            'sector_objects_harvestable_restypes', 'sector_objects_harvestable_oretypes', 'assets',
            'base_ore_list', 'asteroid_content_selection', 'item_base'})
        for table, row in public_rows(dump, tables):
            if table == 'sector_objects' and number(row, 'type') == 38:
                self.objects[int(number(row, 'sector_id'))].append(row)
            elif table == 'sector_objects_harvestable': self.rules[int(number(row, 'resource_id'))] = row
            elif table == 'sector_objects_harvestable_restypes': self.models[int(number(row, 'group_id'))].append(int(number(row, 'type')))
            elif table == 'sector_objects_harvestable_oretypes':
                self.ores[int(number(row, 'resource_id'))].append({'item_id': int(number(row, 'additional_ore_item_id')),
                                                               'frequency': number(row, 'frequency')})
            elif table == 'assets': self.assets[int(number(row, 'base_id'))] = row
            elif table == 'item_base': self.items[int(number(row, 'id'))] = row
            elif table == 'asteroid_content_selection':
                self.subcategories[int(number(row, 'asteroid_type'))].add(int(number(row, 'item_subcat_id')))
            elif table == 'base_ore_list':
                self.base_ores[int(number(row, 'sector_id'))].append(Ore(item_id=int(number(row, 'item_id')),
                                                                     frequency=number(row, 'frequency')))

    def for_sector(self, sector_id: int) -> list[ResourceNode]:
        result: list[ResourceNode] = []
        for obj in sorted(self.objects[sector_id], key=lambda row: number(row, 'sector_object_id')):
            identity = int(number(obj, 'sector_object_id'))
            rule = self.rules.get(identity)
            if rule is None: continue
            count = int(number(rule, 'res_count')) if number(rule, 'max_field_radius') > 0 else 1
            if count == 0: continue
            if not 0 < count <= 4096: raise ValueError(f'Resource field {identity} has unsupported count {count}')
            shape = int(number(rule, 'field')) or identity % 5 + 1
            models = self.models[identity] or self.legacy_models(shape)
            if count == 1 and number(obj, 'base_asset_id') > 0: models = [int(number(obj, 'base_asset_id'))]
            models = [asset for asset in models if self.assets.get(asset, {}).get('main_cat') in ('Asteroids', 'Hulks')]
            if not models: continue
            center = [number(obj, 'position_' + axis) for axis in 'xyz']
            radius = max(0., number(rule, 'max_field_radius'))
            level = max(1, min(9, int(number(rule, 'level'))))
            for index in range(count):
                asset = models[index % len(models)]
                if asset not in self.radii:
                    raise ValueError(f'Resource asset {asset} has no native radius')
                name = self.resource_name(asset)
                resource_type = self.resource_type(asset)
                offset = self.offset(shape, index, count, radius)
                rotated = self.rotate(offset, obj)
                result.append({'id': 0x20000000 + identity * 4096 + index, 'field_id': identity,
                    'level': level, 'resource_type': resource_type,
                    'name': f'{name} (L{level})', 'object_type': 38, 'asset': asset,
                    'radius': self.radii[asset],
                    'position': [center[axis] + rotated[axis] for axis in range(3)],
                    'scale': 3. if name == 'Hulk' else 1.,
                    'hsv': [[180., 120., 30., 20., 10., 0., -60., -70., -100.][level-1], 0., 0.],
                    'orientation': [0., 0., 0., 1.], 'signature': 5000., 'navigation_present': False,
                    'navigation_type': 0, 'huge': False, 'gate_destination': None,
                    'ore_items': self.eligible_ores(sector_id, identity, resource_type, level)})
        if len(result) > 8192: raise ValueError(f'Sector {sector_id} exceeds 8192 resource nodes')
        return result

    def resource_type(self, asset: int) -> int:
        record = self.assets[asset]
        subtype = record.get('sub_cat') or ''
        if record.get('main_cat') == 'Hulks':
            return 9 if subtype.startswith('Organic') else 10
        if subtype.startswith('Ga'): return 7
        types = {'R': 1, 'M': 2, 'H': 3, 'G': 4, 'C': 5, 'D': 6, 'N': 8}
        return types.get(subtype[:1], 2)

    def eligible_ores(self, sector: int, field: int, resource_type: int, level: int) -> list[Ore]:
        """The SQL sector base list plus field additions, filtered as GetOreTemplate.

        Preserve positive frequency weights. Zero is the legacy default weight
        (all base-ore rows in this dump use zero), not a disabled ore entry.
        Ordinary resource contents are field level or one below. Subcategory 110
        devices in asteroids must have the original ancient-device 'aa' prefix.
        """
        result: dict[int, float] = {}
        for ore in self.base_ores[sector] + self.ores[field]:
            item = self.items.get(ore['item_id'])
            if item is None or ore['frequency'] < 0: continue
            subcategory = int(number(item, 'sub_category'))
            if subcategory not in self.subcategories[resource_type]: continue
            if not max(1, level - 1) <= number(item, 'level') <= level: continue
            if resource_type < 9 and subcategory == 110 and not (item.get('name') or '').startswith('aa'): continue
            result[ore['item_id']] = result.get(ore['item_id'], 0.) + (ore['frequency'] or 1.)
        return [Ore(item_id=identity, frequency=frequency) for identity, frequency in sorted(result.items())]

    def resource_name(self, asset: int) -> str:
        record = self.assets[asset]
        if record.get('main_cat') != 'Asteroids': return 'Hulk'
        subtype = record.get('sub_cat') or ''
        if subtype.startswith(('R', 'M', 'N')): return 'Asteroid'
        if subtype.startswith(('H', 'C', 'D')): return 'Hydrocarbon Deposit'
        if subtype.startswith('G') and not subtype.startswith('Ga'): return 'Crystalline Asteroid'
        return 'Gas Cloud'

    @staticmethod
    def legacy_models(shape: int) -> list[int]:
        offsets = {1: [6,7,8,10], 2: [2,4,5,11,12], 3: [3,6,7,9,12,11],
                   4: [1,3,4,6,12,10], 5: [12]}
        return [0x71e + value for value in offsets.get(shape, [1,0,6,9])]

    @staticmethod
    def offset(shape: int, index: int, count: int, radius: float) -> list[float]:
        angle = index * math.pi * (3. - math.sqrt(5.))
        fraction = (index + .5) / count
        if shape in (4,5):
            z = 1. - 2. * fraction
            ring = math.sqrt(max(0., 1. - z*z))
            distance = radius * ((index * .61803398875 + .5) % 1.) ** (1/3)
            if shape == 5: distance *= .7
            return [distance * ring * math.cos(angle), distance * ring * math.sin(angle), distance*z]
        radial = radius if shape == 1 else radius * (.8 + .2 * fraction)
        z = (fraction - .5) * (radius if shape == 3 else min(radius * .2, 1000.))
        return [radial * math.cos(angle), radial * math.sin(angle), z]

    @staticmethod
    def rotate(vector: list[float], obj: SqlRow) -> list[float]:
        q = [number(obj, 'orientation_' + axis) for axis in 'uvwz']
        length = math.sqrt(sum(value*value for value in q))
        if length < .00001: return vector
        x,y,z,w = [value/length for value in q]
        a,b,c = vector
        return [(1-2*(y*y+z*z))*a + 2*(x*y-z*w)*b + 2*(x*z+y*w)*c,
                2*(x*y+z*w)*a + (1-2*(x*x+z*z))*b + 2*(y*z-x*w)*c,
                2*(x*z-y*w)*a + 2*(y*z+x*w)*b + (1-2*(x*x+y*y))*c]
