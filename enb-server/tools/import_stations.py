"""Export public station room/terminal layouts, without player or account data.

StationLoader resolves a station's sector through its exterior object's gate_to,
falling back to parent*10+1. Room 'type' is its client index, not the SQL room ID.
NPC appearances are packed using AvatarData's exact legacy field layout;
dialogue and terminal services remain separate implementation work.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import struct
from pathlib import Path
from typing import TypedDict
import unicodedata

from build import ROOT
from import_sector import SqlRow, number, public_rows


class Room(TypedDict):
    index: int
    style: int
    fog_near: float
    fog_far: float
    fog_color: list[int]


class Terminal(TypedDict):
    room: int
    location: int
    kind: int


class Station(TypedDict):
    id: int
    parent: int
    exterior: int
    name: str
    kind: int
    rooms: list[Room]
    terminals: list[Terminal]
    npcs: list[Npc]


class Npc(TypedDict):
    id: int
    room: int
    location: int
    booth: int
    avatar: list[int]


def avatar_bytes(npc: SqlRow, appearance: SqlRow) -> list[int]:
    """Packed 241-byte AvatarData from PacketStructures.h/StationLoader.cpp."""
    data = bytearray()
    for key in ('first_name', 'last_name'):
        value = unicodedata.normalize('NFKD', npc.get(key) or '').encode('ascii', 'ignore')[:19]
        data.extend(value.ljust(20, b'\0'))
    data.extend(struct.pack('<iBB4i', int(number(appearance, 'avatar_type')), 0,
        int(number(appearance, 'avatar_version')) & 255,
        *(int(number(appearance, key)) for key in ('race', 'profession', 'gender', 'mood_type'))))
    data.extend(bytes(int(number(appearance, key)) & 255 for key in (
        'personality', 'nlp', 'shirt_type', 'pants_type', 'head_type', 'hair_type', 'ear_type',
        'goggle_num', 'beard_num', 'weapon_hip_num', 'weapon_unique_num', 'weapon_back_num',
        'head_texture_num', 'tatoo_texture_num')))
    data.extend(struct.pack('<3f', *(number(appearance, 'tatoo_' + axis) for axis in 'xyz')))
    for prefix in ('hair', 'beard', 'eye', 'skin', 'shirt1', 'shirt2', 'pants1', 'pants2'):
        data.extend(struct.pack('<3f', *(number(appearance, f'{prefix}_color_{axis}') for axis in range(3))))
    data.extend(struct.pack('<4i', *(int(number(appearance, key + '_metal')) for key in ('shirt1', 'shirt2', 'pants1', 'pants2'))))
    data.append(0)
    for prefix in ('body', 'head'):
        data.extend(struct.pack('<5f', *(number(appearance, f'{prefix}_weight{index}') for index in range(5))))
    if len(data) != 241: raise ValueError('AvatarData layout size mismatch')
    return list(data)


def export_stations(dump: Path, output: Path) -> int:
    stations: list[SqlRow] = []
    objects: dict[int, SqlRow] = {}
    rooms: defaultdict[int, list[SqlRow]] = defaultdict(list)
    terminals: defaultdict[int, list[SqlRow]] = defaultdict(list)
    npcs: defaultdict[int, list[SqlRow]] = defaultdict(list)
    appearances: dict[int, SqlRow] = {}
    vendors: dict[int, SqlRow] = {}
    tables = frozenset({'starbases', 'sector_objects', 'starbase_rooms', 'starbase_terminals',
                        'starbase_npcs', 'starbase_npc_avatar_templates', 'starbase_vendors'})
    for table, row in public_rows(dump, tables):
        if table == 'starbases': stations.append(row)
        elif table == 'sector_objects': objects[int(number(row, 'sector_object_id'))] = row
        elif table == 'starbase_rooms': rooms[int(number(row, 'starbase_id'))].append(row)
        elif table == 'starbase_terminals': terminals[int(number(row, 'room_id'))].append(row)
        elif table == 'starbase_npcs': npcs[int(number(row, 'room_id'))].append(row)
        elif table == 'starbase_npc_avatar_templates': appearances[int(number(row, 'avatar_template_id'))] = row
        elif table == 'starbase_vendors': vendors[int(number(row, 'vendor_id'))] = row
    result: list[Station] = []
    for source in stations:
        if not number(source, 'is_active'): continue
        parent = int(number(source, 'sector_id'))
        exterior = int(number(source, 'target_sector_object'))
        object_row = objects.get(exterior)
        if object_row is None or parent >= 10000: continue
        identity = int(number(object_row, 'gate_to')) or parent * 10 + 1
        exported_rooms: list[Room] = []
        exported_terms: list[Terminal] = []
        exported_npcs: list[Npc] = []
        for room in rooms[int(number(source, 'starbase_id'))]:
            index = int(number(room, 'type'))
            exported_rooms.append({'index': index, 'style': int(number(room, 'style')),
                'fog_near': number(room, 'fog_near'), 'fog_far': number(room, 'fog_far'),
                'fog_color': [int(number(room, 'fog_' + color)) for color in ('red', 'green', 'blue')]})
            for terminal in terminals[int(number(room, 'room_id'))]:
                exported_terms.append({'room': index, 'location': int(number(terminal, 'location')),
                                       'kind': int(number(terminal, 'type'))})
            for npc in npcs[int(number(room, 'room_id'))]:
                npc_id = int(number(npc, 'npc_Id'))
                appearance = appearances.get(npc_id)
                if appearance is None: raise ValueError(f'NPC {npc_id} has no public appearance')
                exported_npcs.append({'id': npc_id, 'room': index, 'location': int(number(npc, 'location')),
                    'booth': int(number(vendors.get(npc_id, {}), 'booth_type', -1)),
                    'avatar': avatar_bytes(npc, appearance)})
        if not exported_rooms: continue
        if len(exported_rooms) > 5 or len(exported_terms) > 15 or len(exported_npcs) > 30:
            raise ValueError(f'Station {identity} exceeds the client layout capacity')
        name = unicodedata.normalize('NFKD', source.get('name') or 'Station').encode('ascii', 'ignore').decode()
        result.append({'id': identity, 'parent': parent, 'exterior': exterior + 100000,
                       'name': name, 'kind': int(number(source, 'type')),
                       'rooms': exported_rooms, 'terminals': exported_terms, 'npcs': exported_npcs})
    if len({station['id'] for station in result}) != len(result):
        raise ValueError('Duplicate station sector identities')
    output.write_text(json.dumps(result, indent=2) + '\n')
    return len(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/stations.json')
    args = parser.parse_args()
    print(f'Exported {export_stations(args.dump, args.output)} station layouts')


if __name__ == '__main__': main()
