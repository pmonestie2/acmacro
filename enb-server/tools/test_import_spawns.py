"""Behavioral tests for resolving the public spawn/template relationships."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from import_spawns import PublicSpawns, export_spawns
from import_asset_radii import load_radii
from build import ROOT
from test_import_sector import table


class SpawnTests(unittest.TestCase):
    def test_resolves_template_and_skips_disabled_spawn(self) -> None:
        sql = table('sector_objects', [
            {'sector_object_id': 20, 'sector_id': 1076, 'type': 0, 'position_x': 500, 'position_y': 0, 'position_z': 0},
            {'sector_object_id': 21, 'sector_id': 1076, 'type': 0, 'position_x': 0, 'position_y': 0, 'position_z': 0}])
        sql += table('sector_objects_mob', [
            {'mob_id': 20, 'mob_count': 2, 'mob_spawn_radius': 100, 'respawn_time': 25},
            {'mob_id': 21, 'mob_count': 0, 'mob_spawn_radius': 0, 'respawn_time': 0}])
        sql += table('mob_spawn_group', [{'spawn_group_id': 20, 'mob_id': 7}, {'spawn_group_id': 21, 'mob_id': 7}])
        sql += table('mob_base', [{'mob_id': 7, 'name': 'Drone', 'level': 3, 'base_asset_id': 384, 'aggressiveness': 3, 'faction_id': 17, 'type': 5, 'shield_modifier': 10, 'damage_modifier': 2, 'range_modifier': 3}])
        with tempfile.TemporaryDirectory() as folder:
            dump, content = Path(folder) / 'public.sql', Path(folder) / 'sector.json'
            dump.write_text(sql)
            content.write_text(json.dumps({'id': 1076, 'navpoints': []}))
            self.assertEqual(export_spawns(dump, content), 2)
            mobs = json.loads(content.read_text())['mobs']
        self.assertEqual([mob['source_id'] for mob in mobs], [2000, 2001])
        self.assertTrue(all(mob['template_id'] == 7 and mob['aggressive'] for mob in mobs))
        self.assertTrue(all(400 <= mob['spawn'][0] <= 600 for mob in mobs))
        self.assertEqual(mobs[0]['respawn_seconds'], 25)
        self.assertEqual((mobs[0]['mob_type'],mobs[0]['shield_modifier'],mobs[0]['damage_modifier'],mobs[0]['range_modifier']),(5,10,2,3))
        self.assertEqual((mobs[0]['faction'], mobs[0]['aggression']), (17, 3))
        self.assertEqual(mobs[0]['spawn_region'], {'center': [500., 0., 0.], 'radius': 100.})
        self.assertEqual(mobs[1]['spawn_region'], mobs[0]['spawn_region'])
        expected_radius = load_radii(ROOT / 'data/asset_radii.json')[384]
        self.assertGreater(expected_radius, 0)
        self.assertTrue(all(mob['radius'] == expected_radius for mob in mobs))

    def test_missing_native_radius_is_reported_instead_of_invented(self) -> None:
        sql = table('sector_objects', [{'sector_object_id': 20, 'sector_id': 1, 'type': 0}])
        sql += table('sector_objects_mob', [{'mob_id': 20, 'mob_count': 1}])
        sql += table('mob_spawn_group', [{'spawn_group_id': 20, 'mob_id': 7}])
        sql += table('mob_base', [{'mob_id': 7, 'name': 'Unknown geometry', 'base_asset_id': 65000}])
        with tempfile.TemporaryDirectory() as folder:
            dump = Path(folder) / 'public.sql'
            dump.write_text(sql)
            with self.assertRaisesRegex(ValueError, '65000.*no native radius'):
                PublicSpawns(dump).for_sector(1)

    def test_first_public_weapon_keeps_real_stats_and_null_boss_modifiers_default_to_one(self) -> None:
        sql=table('sector_objects',[{'sector_object_id':20,'sector_id':1,'type':0}])
        sql+=table('sector_objects_mob',[{'mob_id':20,'mob_count':1}])
        sql+=table('mob_spawn_group',[{'spawn_group_id':20,'mob_id':7}])
        sql+=table('mob_base',[{'mob_id':7,'name':'Boss','base_asset_id':384,'type':1,'shield_modifier':10,'damage_modifier':'null','range_modifier':'null'}])
        sql+=table('mob_items',[{'id':2,'mob_id':7,'item_base_id':80},{'id':1,'mob_id':7,'item_base_id':81}])
        sql+=table('item_base',[{'id':80,'type':14},{'id':81,'type':14}])
        sql+=table('item_beam',[{'item_id':80,'damage_100':10,'reload_100':1,'fire_effect':35,'damage_type':3},
                              {'item_id':81,'damage_100':90,'reload_100':2,'fire_effect':33,'damage_type':7}])
        with tempfile.TemporaryDirectory() as folder:
            dump=Path(folder)/'public.sql';dump.write_text(sql)
            mob=PublicSpawns(dump).for_sector(1)[0]
        self.assertEqual(mob['damage_modifier'],1.)
        self.assertEqual(mob['range_modifier'],1.)
        self.assertEqual(mob['weapon'],{'item':81,'item_type':14,'damage':90.,'reload':2.,'effect':33,'damage_type':7})

    def test_multi_sector_index_excludes_missing_models_and_preserves_signed_scale(self) -> None:
        sql = table('sector_objects', [
            {'sector_object_id': 20, 'sector_id': 1, 'type': 0},
            {'sector_object_id': 21, 'sector_id': 2, 'type': 0}])
        sql += table('sector_objects_mob', [{'mob_id': 20, 'mob_count': 80}, {'mob_id': 21, 'mob_count': 1}])
        sql += table('mob_spawn_group', [{'spawn_group_id': 20, 'mob_id': 7}, {'spawn_group_id': 21, 'mob_id': 8}])
        sql += table('mob_base', [{'mob_id': 7, 'name': 'Mirrored', 'base_asset_id': 384, 'scale': -1},
                                  {'mob_id': 8, 'name': 'Missing', 'base_asset_id': 0, 'scale': 1}])
        with tempfile.TemporaryDirectory() as folder:
            dump = Path(folder) / 'public.sql'
            dump.write_text(sql)
            catalog = PublicSpawns(dump)
            self.assertEqual(len(catalog.for_sector(1)), 80)
            self.assertEqual(catalog.for_sector(1)[0]['scale'], -1)
            self.assertEqual(catalog.for_sector(2), [])
            self.assertEqual(catalog.missing_models, {8: 'Missing'})

    def test_zero_radius_preserves_static_content_and_numeric_nine_aggression(self) -> None:
        sql = table('sector_objects', [{'sector_object_id': 20, 'sector_id': 1, 'type': 0, 'position_z': 500}])
        sql += table('sector_objects_mob', [{'mob_id': 20, 'mob_count': 1, 'mob_spawn_radius': 0}])
        sql += table('mob_spawn_group', [{'spawn_group_id': 20, 'mob_id': 7}])
        sql += table('mob_base', [{'mob_id': 7, 'name': 'Static', 'base_asset_id': 384, 'aggressiveness': 9}])
        with tempfile.TemporaryDirectory() as folder:
            dump = Path(folder) / 'public.sql'
            dump.write_text(sql)
            mob = PublicSpawns(dump).for_sector(1)[0]
        self.assertEqual(mob['spawn_region'], {'center': [0., 0., 500.], 'radius': 0.})
        self.assertEqual(mob['spawn'], [0., 0., 500.])
        self.assertTrue(mob['aggressive'])
        self.assertEqual(mob['aggression'], 9)

    def test_invalid_roaming_regions_and_native_byte_metadata_are_rejected(self) -> None:
        for radius, faction, aggression in [(-1, 0, 3), ('NaN', 0, 3), (100, -1, 3), (100, 0, 256)]:
            with self.subTest(radius=radius, faction=faction, aggression=aggression):
                sql = table('sector_objects', [{'sector_object_id': 20, 'sector_id': 1, 'type': 0}])
                sql += table('sector_objects_mob', [{'mob_id': 20, 'mob_count': 1, 'mob_spawn_radius': radius}])
                sql += table('mob_spawn_group', [{'spawn_group_id': 20, 'mob_id': 7}])
                sql += table('mob_base', [{'mob_id': 7, 'name': 'Invalid', 'base_asset_id': 384,
                                           'faction_id': faction, 'aggressiveness': aggression}])
                with tempfile.TemporaryDirectory() as folder:
                    dump = Path(folder) / 'public.sql'
                    dump.write_text(sql)
                    with self.assertRaises(ValueError):
                        PublicSpawns(dump).for_sector(1)
