"""Export public NPC conversations and vendor groups; never read account tables.

Station placements and item templates are the already exported public catalogs.
Legacy talk trees are XML fragments, not a document. Unsupported mission flags
are retained so the runtime can reject those actions rather than award rewards.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import TypedDict
import unicodedata
import xml.etree.ElementTree as ET

from build import ROOT
from import_sector import number, public_rows


class Branch(TypedDict):
    node: int
    text: str


class Node(TypedDict):
    id: int
    text: str
    flag: int
    branches: list[Branch]


class Npc(TypedDict):
    id: int
    station: int
    name: str
    description: str
    faction: int
    group: int
    nodes: list[Node]


class Offer(TypedDict):
    item: int
    sell: int
    buy: int
    quantity: int


class VendorGroup(TypedDict):
    id: int
    name: str
    sell_multiplier: float
    buy_multiplier: float
    listed_only: bool
    offers: list[Offer]


def client_text(value: str | None) -> str:
    """The native client expects legacy single-byte text, not UTF-8."""
    return unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode().replace('\0', '')


def talk_nodes(fragment: str) -> list[Node]:
    # A bare ampersand appears in original dialogue. Preserve it as text, while
    # letting ElementTree decode genuine XML entities and reject broken markup.
    fragment = re.sub(r'&(?!#\d+;|#x[0-9a-fA-F]+;|amp;|lt;|gt;|quot;|apos;)', '&amp;', fragment)
    root = ET.fromstring('<Dialog>' + fragment + '</Dialog>')
    result: list[Node] = []
    for tree in root.findall('Tree'):
        identity = int(tree.attrib['Node'])
        if not 1 <= identity < 230:
            raise ValueError('Dialogue node exceeds native one-byte selection range')
        branches = [Branch(node=int(branch.attrib['Node']), text=client_text(branch.text))
                    for branch in tree.findall('Branch')]
        if len(branches) > 20 or any(not 0 <= branch['node'] < 230 for branch in branches):
            raise ValueError('Dialogue branch exceeds native bounds')
        result.append(Node(id=identity, text=client_text(tree.findtext('Text')),
                           flag=int(tree.findtext('Trade') or tree.findtext('Flags') or '0'),
                           branches=branches))
    identities = {node['id'] for node in result}
    if result and (len(identities) != len(result) or 1 not in identities or
            any(branch['node'] and branch['node'] not in identities for node in result for branch in node['branches'])):
        raise ValueError('Dialogue has duplicate, missing root or dangling nodes')
    return result


def export_npcs(dump: Path, stations: Path, items: Path, output: Path) -> Counter[str]:
    placements = {npc['id']: station['id'] for station in json.loads(stations.read_text()) for npc in station['npcs']}
    templates = {item['id'] for item in json.loads(items.read_text())}
    npc_rows = {}
    vendor_rows = {}
    groups: dict[int, VendorGroup] = {}
    inventory: list[tuple[int, Offer]] = []
    counts: Counter[str] = Counter()
    tables = frozenset({'starbase_npcs', 'starbase_vendors', 'starbase_vender_groups', 'starbase_vender_inventory'})
    for table, row in public_rows(dump, tables):
        if table == 'starbase_npcs': npc_rows[int(number(row, 'npc_Id'))] = row
        elif table == 'starbase_vendors': vendor_rows[int(number(row, 'vendor_id'))] = row
        elif table == 'starbase_vender_groups':
            identity = int(number(row, 'GroupID'))
            groups[identity] = VendorGroup(id=identity, name=client_text(row.get('GroupName')),
                sell_multiplier=number(row, 'SellMultiplyer'), buy_multiplier=number(row, 'BuyMultiplyer'),
                listed_only=bool(number(row, 'BuyOnlyList')), offers=[])
        else:
            item = int(number(row, 'itemid'))
            if item not in templates:
                counts['missing_item_rows'] += 1
                continue
            inventory.append((int(number(row, 'groupid')), Offer(item=item,
                sell=int(number(row, 'sell_price')), buy=int(number(row, 'buy_price')),
                quantity=int(number(row, 'quanity')))))
    for group, offer in inventory:
        if group not in groups:
            counts['missing_group_rows'] += 1
            continue
        if any(existing['item'] == offer['item'] for existing in groups[group]['offers']):
            counts['duplicate_offer_rows'] += 1
            continue
        groups[group]['offers'].append(offer)
    npcs: list[Npc] = []
    for identity, station in sorted(placements.items()):
        row = npc_rows[identity]
        group = int(number(vendor_rows.get(identity, {}), 'groupid'))
        if group not in groups: group = 0
        nodes: list[Node] = []
        fragment = row.get('talk_tree_handle') or ''
        if fragment.strip():
            try: nodes = talk_nodes(fragment)
            except (ET.ParseError, ValueError, KeyError): counts['invalid_dialogues'] += 1
        if nodes: counts['dialogues'] += 1
        npcs.append(Npc(id=identity, station=station, name=client_text(' '.join(filter(None,
            (row.get('first_name'), row.get('last_name'))))), description=client_text(row.get('description')),
            faction=int(number(row, 'faction_id')), group=group, nodes=nodes))
    output.write_text(json.dumps({'npcs': npcs, 'groups': sorted(groups.values(), key=lambda entry: entry['id']),
        'import_diagnostics': dict(counts)}, indent=2) + '\n')
    counts.update(npcs=len(npcs), groups=len(groups), offers=sum(len(group['offers']) for group in groups.values()))
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--stations', type=Path, default=ROOT/'data/stations.json')
    parser.add_argument('--items', type=Path, default=ROOT/'data/items.json')
    parser.add_argument('--output', type=Path, default=ROOT/'data/npcs.json')
    args = parser.parse_args()
    print(dict(export_npcs(args.dump, args.stations, args.items, args.output)))


if __name__ == '__main__': main()
