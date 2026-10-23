from __future__ import annotations
import json,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from navigation.stations import Station,StationCatalog
from explore_gates import GateExplorer,SectorGraph
from navigate_destination import DestinationGraph,DestinationNavigator,NavigationLeg
from protocol.vendor_trading import VendorSession
import enb

class StationTests(unittest.TestCase):
    def test_only_server_station_objects_are_recorded_with_sector_scoped_ids(self)->None:
        catalog=StationCatalog()
        obj=dict(object_type=12,sector='A',name='Station A',id='0x186a1',position_seen=5.,raw_xyz=[1.,2.,3.])
        self.assertEqual(len(catalog.observe([obj],'capture')),1)
        self.assertEqual(catalog.observe([obj],'capture'),[])
        self.assertEqual(catalog.observe([dict(obj,object_type=37,name='TransCom Monitor')],'capture'),[])
        catalog.observe([dict(obj,sector='B',name='Station B')],'capture')
        self.assertEqual(len(catalog.stations),2)
        self.assertEqual(catalog.stations[('A','Station A')].raw_xyz,(1.,2.,3.))

    def test_save_merges_other_writer_and_newest_identity_wins(self)->None:
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'stations.json'
            first=StationCatalog(p);second=StationCatalog(p)
            first.record(Station('A','One',1,5.,'first'));first.save()
            second.record(Station('B','Two',1,6.,'second'));second.save()
            first.record(Station('A','One',2,7.,'latest'));first.save()
            saved=StationCatalog(p)
            self.assertEqual(len(saved.stations),2)
            self.assertEqual(saved.stations[('A','One')].item_id,2)
            self.assertFalse(saved.record(Station('A','One',9,4.,'old')))

    def test_dock_route_uses_first_local_station_and_explicit_name_overrides(self)->None:
        with tempfile.TemporaryDirectory() as directory:
            graph=SectorGraph(Path(directory)/'graph.json')
            graph.gate('A',9,'Gate to B');graph.confirm('A',9,'B',1.)
            stations=StationCatalog()
            stations.record(Station('B','Later Station',5,1.,'capture'))
            stations.record(Station('B','First Station',2,1.,'capture'))
            d=DestinationGraph(graph,station_catalog=stations)
            route=d.route('A','B',dock=True)
            self.assertEqual([leg.action for leg in route],[18,7])
            self.assertEqual(route[-1].target_id,2)
            self.assertEqual(d.route('A','Later Station')[-1].target_id,5)
            self.assertEqual(d.route('B','B',dock=True)[0].target_id,2)
            with self.assertRaisesRegex(ValueError,'No recorded station'):d.route('A','A',dock=True)

    def navigator(self)->DestinationNavigator:
        n=DestinationNavigator.__new__(DestinationNavigator)
        n.session=VendorSession(1,'client',2,'proxy',0x40000007)
        n.cargo=Mock();n.jumps=0;n.station_arrival=None;n.docking_guard=None
        return n

    def test_docking_sends_one_completion_and_requires_matching_fresh_station_arrival(self)->None:
        n=self.navigator()
        def checkpoint()->None:n.station_arrival=('B','Station',10.5)
        with patch.object(n,'select_target'),patch.object(n,'validate_selected_target'),patch.object(n,'send') as send,patch.object(n,'checkpoint',side_effect=checkpoint),patch.object(n,'active_time',return_value=0.),patch('navigate_destination.time.time',return_value=10.):
            n.dock(NavigationLeg('B','Station',22,'Station',7))
        send.assert_called_once_with(0x2c,struct.pack('<4I',n.session.game_id,7,22,0))
        self.assertEqual(n.jumps,1);self.assertIsNone(n.docking_guard)
        n.cargo.close.assert_called_once()

    def test_stale_or_wrong_station_never_succeeds_or_retries(self)->None:
        for arrival,error in [(('B','Station',9.),TimeoutError),(('B','Wrong station',11.),RuntimeError)]:
            with self.subTest(arrival=arrival):
                n=self.navigator();n.station_arrival=arrival
                with patch.object(n,'select_target'),patch.object(n,'validate_selected_target'),patch.object(n,'send') as send,patch.object(n,'checkpoint'),patch.object(n,'active_time',side_effect=[0.,31.]),patch('navigate_destination.time.time',return_value=10.):
                    with self.assertRaises(error):n.dock(NavigationLeg('B','Station',22,'Station',7))
                self.assertEqual(send.call_count,1);self.assertEqual(n.jumps,0)

    def test_cli_passes_dock_flag(self)->None:
        args=enb.parser().parse_args(['navigate','je','Antares','--dock'])
        self.assertIn('--dock',enb.command_line(args))

    def test_station_arrival_requires_incoming_matching_session_and_player(self)->None:
        n=self.navigator();n.latest_verbs=None;n.world=Mock(sector='B')
        fields=b'System\0B\0Station\0'
        for born,owner,outgoing in [('old',7,False),('proxy',8,False),('proxy',7,True),('proxy',7,False)]:
            body=struct.pack('<III',4,len(fields)+4,owner)+fields
            row=dict(time=11.,proxy=2,born=born,outgoing=outgoing,payload_hex=(struct.pack('<HH',len(body)+4,0x97)+body).hex())
            with patch.object(GateExplorer,'observe'):n.observe(row)
            if born=='proxy' and owner==7 and not outgoing:self.assertEqual(n.station_arrival,('B','Station',11.))
            else:self.assertIsNone(n.station_arrival)

if __name__=='__main__':unittest.main()
