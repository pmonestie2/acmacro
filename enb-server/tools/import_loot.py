"""Compile public MOB drop definitions; never execute the SQL dump.

Keep source percentages and quantities intact. Missing templates/items and invalid
rows are recorded in diagnostics rather than replaced with invented drops.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
from typing import TypedDict

from build import ROOT
from import_sector import number, public_rows


class Drop(TypedDict):
    source_id: int
    item: int
    chance_percent: float
    quantity: int
    kind: int
    usage_chance: int


class MobLoot(TypedDict):
    id: int
    kind: int | None
    drops: list[Drop]


class RejectedDrop(TypedDict):
    source_id: int
    mob: int
    item: int
    reason: str


class LootFile(TypedDict):
    mobs: list[MobLoot]
    rejected: list[RejectedDrop]


def compile_loot(dump: Path, items: Path) -> LootFile:
    item_ids = {int(item['id']) for item in json.loads(items.read_text(encoding='utf8'))}
    mobs: dict[int, int | None] = {}
    candidates: defaultdict[int, list[Drop]] = defaultdict(list)
    rejected: list[RejectedDrop] = []
    identities: set[int] = set()
    for table, row in public_rows(dump, frozenset({'mob_base', 'mob_items'})):
        if table == 'mob_base':
            identity, kind = int(number(row, 'mob_id')), int(number(row, 'type'))
            if identity <= 0 or identity in mobs or not -1 <= kind <= 6:
                raise ValueError(f'Invalid or duplicate MOB definition {identity}')
            mobs[identity] = None if kind == -1 else kind
            continue
        identity, mob, item = (int(number(row, key)) for key in ('id', 'mob_id', 'item_base_id'))
        chance, usage = number(row, 'drop_chance'), number(row, 'usage_chance')
        quantity, kind = int(number(row, 'qty')), int(number(row, 'type'))
        if identity in identities:
            raise ValueError(f'Duplicate MOB item row {identity}')
        identities.add(identity)
        reason = ''
        if item not in item_ids: reason = 'Item absent from compiled catalog'
        elif not math.isfinite(chance) or not 0 <= chance <= 100: reason = 'Invalid drop percentage'
        elif not math.isfinite(usage) or not 0 <= usage <= 0xffffffff: reason = 'Invalid usage metadata'
        elif not 1 <= quantity <= 255: reason = 'Unsupported drop quantity'
        elif kind not in (0, 1): reason = 'Unsupported MOB item type'
        if reason:
            rejected.append(RejectedDrop(source_id=identity, mob=mob, item=item, reason=reason))
        else:
            candidates[mob].append(Drop(source_id=identity, item=item, chance_percent=chance,
                quantity=quantity, kind=kind, usage_chance=int(usage)))
    for mob in sorted(candidates.keys() - mobs.keys()):
        for drop in candidates[mob]:
            rejected.append(RejectedDrop(source_id=drop['source_id'], mob=mob, item=drop['item'],
                                         reason='MOB definition absent'))
    return LootFile(mobs=[MobLoot(id=identity, kind=kind,
        drops=sorted(candidates[identity], key=lambda drop: drop['source_id']))
        for identity, kind in sorted(mobs.items())],
        rejected=sorted(rejected, key=lambda drop: drop['source_id']))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--items', type=Path, default=ROOT/'data/items.json')
    parser.add_argument('--output', type=Path, default=ROOT/'data/loot.json')
    args = parser.parse_args()
    catalog = compile_loot(args.dump, args.items)
    args.output.write_text(json.dumps(catalog, separators=(',', ':'), allow_nan=False) + '\n', encoding='utf8')
    print(f'Exported {len(catalog["mobs"])} MOBs, {sum(len(mob["drops"]) for mob in catalog["mobs"])} drop rows; '
          f'{len(catalog["rejected"])} rows retained as rejected diagnostics')


if __name__ == '__main__': main()
