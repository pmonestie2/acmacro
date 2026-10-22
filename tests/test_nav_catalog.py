import json
from pathlib import Path
import tempfile
import unittest

from nav_catalog import nearby_contacts, refresh
from navigation_route import map_point


class NavigationCatalogTests(unittest.TestCase):
    def test_coordinates_keep_sector_identity_and_exclude_asteroids_and_mobs(self):
        def point(sector,name,kind='map_object'):
            return {'id':'0x186a0','name':name,'kind':kind,'sector':sector,
                    'raw_xyz':[1000,2000,0],'last_seen':100,'position_seen':100}
        world={'epoch':2,'catalog':[point('Earth','Earth Station'),point('Earth','Asteroid 1'),
                                  point('Earth','Chavez','ship_or_npc')],
               'previous_epochs':[{'epoch':1,'objects':[point('Saturn','Gate To Beta Hydri System')]}]}
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'capture.json'; store=Path(folder)/'navpoints.json'
            source.write_text(json.dumps({'proxy_session':world}))
            result=refresh(source,store)
            self.assertEqual(set(result['sectors']),{'Earth','Saturn'})
            self.assertEqual(len(result['sectors']['Earth']),1)
            self.assertEqual(next(iter(result['sectors']['Earth'].values()))['map_xyz'],[1,2,0])
            # Older observations cannot overwrite newer coordinates.
            world['catalog'][0].update(raw_xyz=[0,0,0],position_seen=90)
            source.write_text(json.dumps({'proxy_session':world}))
            result=refresh(source,store)
            self.assertEqual(next(iter(result['sectors']['Earth'].values()))['raw_xyz'],[1000,2000,0])
            world['catalog'][1].update(navigation_present=True, nav_type=2)
            source.write_text(json.dumps({'proxy_session':world}))
            result=refresh(source,store)
            self.assertEqual(len(result['sectors']['Earth']),2)

    def test_unknown_npc_near_endpoint_is_rejected_but_stale_or_far_contacts_are_not(self):
        npc={'id':'1','kind':'ship_or_npc','raw_xyz':[1000,0,0],'position_seen':90}
        self.assertEqual(nearby_contacts([0,0,0],[npc],now=100),[npc])
        self.assertEqual(nearby_contacts([0,0,0],[npc],now=150),[])
        self.assertEqual(nearby_contacts([50000,0,0],[npc],now=100),[])

    def test_saturn_gate_coordinates_use_sector_anchors(self):
        anchors=[([366300,-226730,0],(429,568)),([199110,-321590,0],(379,588))]
        self.assertEqual(map_point([89610,-467280,0],anchors),(346,619))
        with self.assertRaises(ValueError):
            map_point([10000000,0,0],anchors)

    def test_vrix_last_position_remains_dangerous_after_visibility_loss(self):
        npc={'id':'1','name':"V'rix Scout",'kind':'ship_or_npc',
             'raw_xyz':[1000,0,0],'position_seen':90,'removal_observed':True}
        self.assertEqual(nearby_contacts([0,0,0],[npc],now=150),[npc])
        self.assertEqual(nearby_contacts([0,0,0],[npc],now=10000),[npc])

    def test_respawn_hazard_survives_empty_new_capture(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'capture.json'; store=Path(folder)/'navpoints.json'
            npc={'id':'1','name':"V'rix Scout",'kind':'ship_or_npc','sector':'Glenn',
                 'raw_xyz':[1000,2000,0],'position_seen':90,'last_seen':90}
            source.write_text(json.dumps({'old':{'epoch':1,'catalog':[npc]}}))
            refresh(source,store)
            source.write_text(json.dumps({'new':{'epoch':1,'catalog':[]}}))
            saved=refresh(source,store)
            self.assertEqual(len(saved['hazards']['Glenn']),1)
            self.assertEqual(next(iter(saved['hazards']['Glenn'].values()))['raw_xyz'],[1000,2000,0])
