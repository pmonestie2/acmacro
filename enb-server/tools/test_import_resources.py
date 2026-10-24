"""Public field expansion tests; no live game, private data or SQL execution."""
from __future__ import annotations

import math
from pathlib import Path
import tempfile
import unittest

from import_resources import PublicResources
from test_import_sector import table


class ResourceImportTests(unittest.TestCase):
    def test_fields_expand_with_rotated_positions_models_and_stable_ids(self) -> None:
        sql = table('sector_objects', [
            {'sector_object_id': 20, 'sector_id': 1077, 'type': 38, 'base_asset_id': 0,
             'position_x': 1000, 'position_y': 2000, 'position_z': 0,
             'orientation_u': 0, 'orientation_v': 0, 'orientation_w': math.sqrt(.5), 'orientation_z': math.sqrt(.5)},
            {'sector_object_id': 21, 'sector_id': 1077, 'type': 38, 'base_asset_id': 1131,
             'position_x': 0, 'position_y': 0, 'position_z': 0,
             'orientation_u': 0, 'orientation_v': 0, 'orientation_w': 0, 'orientation_z': 1}])
        sql += table('sector_objects_harvestable', [
            {'resource_id': 20, 'res_count': 4, 'max_field_radius': 100, 'level': 3, 'field': 1},
            {'resource_id': 21, 'res_count': 0, 'max_field_radius': 0, 'level': 2, 'field': 0}])
        sql += table('sector_objects_harvestable_restypes', [{'group_id': 20, 'type': 1822}])
        sql += table('sector_objects_harvestable_oretypes', [{'resource_id': 20, 'additional_ore_item_id': 50, 'frequency': 25}])
        sql += table('item_base', [{'id': 50, 'level': 3, 'sub_category': 202, 'name': 'Test ore'}])
        sql += table('asteroid_content_selection', [{'asteroid_type': 1, 'item_subcat_id': 202}])
        sql += table('assets', [{'base_id': 1822, 'main_cat': 'Asteroids', 'sub_cat': 'Rocky'},
                                {'base_id': 1131, 'main_cat': 'Hulks', 'sub_cat': 'Inorganic'}])
        with tempfile.TemporaryDirectory() as temporary:
            dump = Path(temporary) / 'public.sql'
            dump.write_text(sql)
            index = PublicResources(dump)
            nodes = index.for_sector(1077)
            self.assertEqual(nodes, index.for_sector(1077))
        self.assertEqual(len(nodes), 5)
        self.assertEqual(len({node['id'] for node in nodes}), 5)
        self.assertEqual(nodes[0]['level'], 3)
        self.assertEqual(nodes[0]['radius'], 800.)
        self.assertAlmostEqual(nodes[0]['position'][0], 1000)
        self.assertAlmostEqual(nodes[0]['position'][1], 2100)
        self.assertEqual(nodes[0]['ore_items'], [{'item_id': 50, 'frequency': 25}])
        self.assertEqual(nodes[-1]['name'], 'Hulk (L2)')
        self.assertGreater(nodes[-1]['radius'], 0.)
        self.assertTrue(all(node['object_type'] == 38 for node in nodes))

    def test_ore_candidates_preserve_legacy_zero_weights_and_filter_items(self) -> None:
        sql = table('item_base', [
            {'id': 1, 'level': 3, 'sub_category': 202, 'name': 'Ore'},
            {'id': 2, 'level': 2, 'sub_category': 202, 'name': 'Lower ore'},
            {'id': 3, 'level': 1, 'sub_category': 202, 'name': 'Too low'},
            {'id': 4, 'level': 4, 'sub_category': 202, 'name': 'Too high'},
            {'id': 5, 'level': 3, 'sub_category': 110, 'name': 'Ordinary device'},
            {'id': 6, 'level': 3, 'sub_category': 110, 'name': 'aaAncient device'},
            {'id': 7, 'level': 3, 'sub_category': 999, 'name': 'Wrong category'},
            {'id': 8, 'level': 3, 'sub_category': 202, 'name': 'Disabled'}])
        sql += table('asteroid_content_selection', [
            {'asteroid_type': kind, 'item_subcat_id': category}
            for kind in (1, 9) for category in (202, 110)])
        sql += table('base_ore_list', [
            {'sector_id': 1077, 'item_id': identity, 'frequency': -1 if identity == 8 else 0}
            for identity in range(1, 9)])
        sql += table('sector_objects_harvestable_oretypes', [
            {'resource_id': 20, 'additional_ore_item_id': 1, 'frequency': 25}])
        with tempfile.TemporaryDirectory() as temporary:
            dump = Path(temporary) / 'public.sql'
            dump.write_text(sql)
            index = PublicResources(dump)
            self.assertEqual(index.eligible_ores(1077, 20, 1, 3), [
                {'item_id': 1, 'frequency': 26}, {'item_id': 2, 'frequency': 1},
                {'item_id': 6, 'frequency': 1}])
            self.assertIn({'item_id': 5, 'frequency': 1}, index.eligible_ores(1077, 20, 9, 3))
            self.assertEqual(index.eligible_ores(9999, 21, 1, 3), [])
