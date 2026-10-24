"""Station export joins public exterior IDs and room indices, never player data."""
from __future__ import annotations

import json
import struct
from pathlib import Path
import tempfile
import unittest

from import_stations import export_stations
from test_import_sector import table


class StationTests(unittest.TestCase):
    def test_resolves_destination_and_terminal_room_index(self) -> None:
        sql = table('starbases', [{'starbase_id': 7, 'sector_id': 1076,
            'target_sector_object': 2, 'name': 'Dauntless', 'type': 0, 'is_active': 1}])
        sql += table('sector_objects', [{'sector_object_id': 2, 'gate_to': 10761}])
        sql += table('starbase_rooms', [{'room_id': 90, 'starbase_id': 7, 'type': 1, 'style': 16}])
        sql += table('starbase_terminals', [{'room_id': 90, 'location': 2, 'type': 1}])
        sql += table('starbase_npcs', [{'npc_Id': 5, 'room_id': 90, 'location': 3,
                                      'first_name': 'Julie', 'last_name': 'Bristol'}])
        sql += table('starbase_npc_avatar_templates', [{'avatar_template_id': 5,
                    'avatar_version': 1, 'race': 2, 'profession': 1, 'shirt_type': 7}])
        sql += table('starbase_vendors', [{'vendor_id': 5, 'booth_type': -1}])
        with tempfile.TemporaryDirectory() as folder:
            dump, output = Path(folder) / 'content.sql', Path(folder) / 'stations.json'
            dump.write_text(sql)
            self.assertEqual(export_stations(dump, output), 1)
            station = json.loads(output.read_text())[0]
        self.assertEqual((station['id'], station['parent'], station['exterior']), (10761, 1076, 100002))
        self.assertEqual(station['terminals'], [{'room': 1, 'location': 2, 'kind': 1}])
        self.assertEqual(station['rooms'][0]['style'], 16)
        npc = station['npcs'][0]
        self.assertEqual((npc['id'], npc['room'], npc['location'], npc['booth']), (5, 1, 3, -1))
        avatar = bytes(npc['avatar'])
        self.assertEqual(len(avatar), 241)
        self.assertEqual(avatar[:20], b'Julie'.ljust(20, b'\0'))
        self.assertEqual(avatar[20:40], b'Bristol'.ljust(20, b'\0'))
        self.assertEqual(struct.unpack_from('<II', avatar, 46), (2, 1))
        self.assertEqual(avatar[64], 7)
