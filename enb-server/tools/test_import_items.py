"""Public item joins and scalar/effect preservation; no game or SQL execution."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from import_items import PublicItems
from test_import_sector import table


class ItemImportTests(unittest.TestCase):
    def test_description_refining_links_preserve_sql_quantity_and_report_missing_targets(self) -> None:
        sql = table('item_base', [
            {'id': 1, 'type': 0, 'level': 1, 'name': 'Iron Ore', 'description': 'Refines to: Iron\\nRe-refines to: Steel'},
            {'id': 2, 'type': 0, 'level': 1, 'name': 'Iron', 'description': ''},
            {'id': 3, 'type': 0, 'level': 1, 'name': 'Unknown Ore', 'description': 'Refines to: Missing'},
            {'id': 4, 'type': 0, 'level': 1, 'name': 'Fallback', 'description': ''},
            {'id': 5, 'type': 0, 'level': 1, 'name': 'Fallback Ore', 'description': 'Refines into: Fallback'},
            {'id': 6, 'type': 0, 'level': 1, 'name': 'Unlinked', 'description': 'Re-refines to: Iron'}])
        sql += table('item_manufacture', [{'item_id': 2, **{f'comp_{index}': 1 for index in range(1, 6)}}])
        with tempfile.TemporaryDirectory() as temporary:
            dump, output = Path(temporary)/'public.sql', Path(temporary)/'items.json'
            dump.write_text(sql)
            PublicItems(dump).export(output)
            items = {item['id']: item for item in json.loads(output.read_text())}
            self.assertEqual(items[1]['refines_into'], 2)
            self.assertEqual(items[2]['components'], [1]*5)
            self.assertEqual(items[5]['refines_into'], 4)
            self.assertEqual(items[4]['components'], [5, 5])
            self.assertEqual(items[6]['refines_into'], 0)
            unresolved = json.loads(output.with_suffix('.refining.json').read_text())['unresolved']
            self.assertEqual([row['item'] for row in unresolved], [3])

    def test_missile_ammunition_preserves_family_range_and_client_label(self) -> None:
        sql = table('item_base',[{'id':1,'type':10,'level':1,'name':'Test Missile','max_stack':200}])
        sql += table('item_ammo',[{'item_id':1,'ammo_type_id':7,'damage_100':50,'damage_type':3,'range_100':4500,'maneuv_100':25}])
        sql += table('item_ammo_type',[{'id':7,'name':'Test Family','sub_category':102}])
        with tempfile.TemporaryDirectory() as temporary:
            dump, output = Path(temporary)/'public.sql', Path(temporary)/'items.json'
            dump.write_text(sql)
            PublicItems(dump).export(output)
            item = json.loads(output.read_text())[0]
        fields={field['index']:field['value']['value'] for field in item['fields']}
        self.assertEqual(item['ammo_type'],7)
        self.assertEqual(fields[1],'Test Family')
        self.assertEqual(fields[19],4500)
        self.assertEqual(fields[11],'Missile Weapon')

    def test_templates_join_stats_effects_restrictions_stacks_and_recipe(self) -> None:
        sql = table('item_base',[{'id':1,'type':6,'level':3,'name':'Engine','max_stack':1,
            'category':12,'sub_category':121,'manufacturer':2,'price':100,'2d_asset':5,'3d_asset':6,
            'no_store':1,'unique':1,'man_cost':1081,'man_cost_base':831,
            'man_cost_mod':130,'man_dif':50,'ana_dif':49,'dis_dif':48}])
        sql += table('item_manufacturer_base',[{'id':2,'name':'Test Maker'}])
        sql += table('item_engine',[{'item_id':1,'rest_prof':2,'rest_race':4,'warp':2500,
            'warp_drain_100':6.5,'thrust_100':125,'signature_100':1500}])
        sql += table('item_effect_base',[{'EffectID':20,'EffectType':0,'Name':'Thrust boost',
            'Description':'%0 thrust','Tooltip':'boost','Buff_Name':'BUFF_THRUST','flag1':1,'flag2':2,
            'VisualEffect':-1,'Var1Type':2,'Var1Stat':'STAT_ENGINE_TOP_SPEED',
            'Constant1Type':1,'Constant1Stat':'STAT_SIGNATURE','Constant1Value':50}])
        sql += table('item_effects',[{'ItemID':1,'item_effect_base_id':20,'Var1Data':25,'Var2Data':0,'Var3Data':0}])
        sql += table('item_manufacture',[{'item_id':1,'comp_1':30,'comp_2':30,'comp_3':31}])
        sql += table('item_refine',[{'item_id':1,'refine_id':32}])
        sql += 'INSERT INTO `accounts` VALUES (invalid ignored private content);\n'
        with tempfile.TemporaryDirectory() as temporary:
            dump, output = Path(temporary)/'public.sql', Path(temporary)/'items.json'
            dump.write_text(sql)
            self.assertEqual(PublicItems(dump).export(output),1)
            item = json.loads(output.read_text())[0]
        fields = {field['index']:field['value'] for field in item['fields']}
        self.assertEqual(fields[31],{'kind':'integer','value':125})
        self.assertEqual(fields[34],{'kind':'float','value':6.5})
        self.assertEqual(fields[11]['value'],'Engine Tech')
        self.assertEqual((fields[3]['value'],fields[18]['value']),(2,4))
        self.assertEqual(item['manufacturer'],'Test Maker')
        self.assertEqual(item['flags'],12)
        self.assertEqual(item['crafting'], {'cost':1081,'cost_base':831,'cost_modifier':130,
            'manufacture_difficulty':50,'analyze_difficulty':49,'dismantle_difficulty':48,
            'recipe_difficulty':0})
        self.assertEqual(item['components'],[30,30,31])
        self.assertEqual(item['refines_into'],32)
        self.assertEqual(item['equipped'][0]['visual'],-1)
        self.assertEqual(item['equipped'][0]['modifiers'],[
            {'stat':'STAT_ENGINE_TOP_SPEED','operation':2,'value':25},
            {'stat':'STAT_SIGNATURE','operation':1,'value':50}])
        self.assertNotIn('slot',item)
