"""Public dialogue and vendor joins, using synthetic SQL without any game I/O."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from import_npcs import export_npcs, talk_nodes
from test_import_sector import table


class NpcImportTests(unittest.TestCase):
    def test_dialogue_decodes_entities_and_preserves_trade_and_branches(self) -> None:
        nodes = talk_nodes('<Tree Node="1"><Text>A & B &amp; C</Text><Branch Node="2">Trade</Branch></Tree>'
                           '<Tree Node="2"><Text>Thanks</Text><Trade>1</Trade></Tree>')
        self.assertEqual(nodes[0]['text'], 'A & B & C')
        self.assertEqual(nodes[0]['branches'], [{'node': 2, 'text': 'Trade'}])
        self.assertEqual(nodes[1]['flag'], 1)
        for fragment in ('<Tree Node="1"><Branch Node="9">Missing</Branch></Tree>',
                         '<Tree Node="230"/>', '<Tree Node="2"/>', '<Tree Node="1"/><Tree Node="1"/>'):
            with self.subTest(fragment=fragment), self.assertRaises(ValueError): talk_nodes(fragment)

    def test_vendor_export_joins_only_public_placed_npcs_and_known_items(self) -> None:
        sql = table('starbase_npcs', [{'npc_Id': 9, 'first_name': 'Test', 'last_name': 'Vendor',
            'talk_tree_handle': '<broken>', 'faction_id': 17, 'description': 'Public'}])
        sql += table('starbase_vendors', [{'vendor_id': 9, 'groupid': 2}])
        sql += table('starbase_vender_groups', [{'GroupID': 2, 'GroupName': 'Parts',
            'SellMultiplyer': 1.35, 'BuyMultiplyer': .8, 'BuyOnlyList': 1}])
        sql += table('starbase_vender_inventory', [
            {'groupid': 2, 'itemid': 100, 'sell_price': 0, 'buy_price': -1, 'quanity': -1},
            {'groupid': 2, 'itemid': 100, 'sell_price': 999, 'buy_price': 0, 'quanity': 10},
            {'groupid': 2, 'itemid': 999, 'sell_price': 0, 'buy_price': 0, 'quanity': -1}])
        sql += 'INSERT INTO `accounts` VALUES (ignored private data);\n'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dump, stations, items, output = (root/name for name in ('world.sql', 'stations.json', 'items.json', 'npcs.json'))
            dump.write_text(sql)
            stations.write_text(json.dumps([{'id': 17051, 'npcs': [{'id': 9}]}]))
            items.write_text(json.dumps([{'id': 100}]))
            counts = export_npcs(dump, stations, items, output)
            result = json.loads(output.read_text())
        self.assertEqual(result['npcs'][0]['name'], 'Test Vendor')
        self.assertEqual(result['npcs'][0]['station'], 17051)
        self.assertEqual(result['npcs'][0]['nodes'], [])
        self.assertTrue(result['groups'][0]['listed_only'])
        self.assertEqual(result['groups'][0]['offers'], [{'item': 100, 'sell': 0, 'buy': -1, 'quantity': -1}])
        self.assertEqual(counts['missing_item_rows'], 1)
        self.assertEqual(counts['duplicate_offer_rows'], 1)
        self.assertEqual(counts['invalid_dialogues'], 1)
