import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from explore_frontier import gate_pass
from gate_navigation import NavigationUncertain
from route_replanning import change_clearance, replace_warp_route


def point(name, xyz, nav_type=1):
    return dict(id='0x123', name=name, raw_xyz=xyz, kind='map_object', sector='Earth',
                navigation_present=True, nav_type=nav_type, visited=False)


def world(objects=()):
    return dict(recent=dict(sector='Earth', updated=100,
                player=dict(raw_xyz=[0, 0, 0], last_seen=100)), catalog=list(objects))


class ReplanningTests(unittest.TestCase):
    def test_near_unknown_or_hostile_defers_but_friendly_and_removed_do_not(self):
        for hostility, allowed in [('unknown', False), ('hostile', False), ('friendly', True)]:
            contact = dict(kind='ship_or_npc', name='Ship', hostility=hostility,
                           raw_xyz=[1000, 0, 0], position_seen=100)
            self.assertEqual(change_clearance(world([contact]), now=100)['allowed'], allowed)
            contact['removal_observed'] = True
            self.assertTrue(change_clearance(world([contact]), now=100)['allowed'])

    def test_stale_position_or_capture_and_recorded_respawn_defer(self):
        w = world()
        self.assertFalse(change_clearance(w, now=104)['allowed'])
        w['recent']['player']['last_seen'] = 104
        self.assertFalse(change_clearance(w, now=104)['allowed'])
        self.assertFalse(change_clearance(world(), [dict(name='Vrix', raw_xyz=[0, 0, 0])], now=100)['allowed'])

    def test_no_second_toggle_without_confirmed_stop(self):
        route = [point('Sector Gate', [50000, 0, 0], 2)]
        owner = SimpleNamespace(proxy_pid=1, valid=lambda: True)
        with patch('route_replanning.live_world', return_value=world(route)), \
             patch('route_replanning.time.time', return_value=100), \
             patch('route_replanning.submit', return_value={'state':'handler_returned'}) as send:
            with self.assertRaises(NavigationUncertain):
                replace_warp_route(owner, '1_birth', 'Earth', route,
                                   wait_for_stop=lambda since: False, warp_confirmed=True)
            self.assertEqual(send.call_count, 1)

    def test_verified_stop_restarts_exactly_once_and_hostile_defers_without_input(self):
        route = [point('Sector Gate', [50000, 0, 0], 2)]
        owner = SimpleNamespace(proxy_pid=1, valid=lambda: True)
        w = world(route)
        with patch('route_replanning.live_world', return_value=w), \
             patch('route_replanning.time.time', return_value=100), \
             patch('route_replanning.submit', return_value={'state':'handler_returned'}) as send:
            result = replace_warp_route(owner, '1_birth', 'Earth', route,
                                       wait_for_stop=lambda since: True, warp_confirmed=True)
            self.assertEqual(result['state'], 'restart_submitted')
            self.assertEqual(send.call_count, 2)
            send.reset_mock()
            w['catalog'].append(dict(kind='ship_or_npc', hostility='hostile', raw_xyz=[0,0,0], position_seen=100))
            self.assertEqual(replace_warp_route(owner, '1_birth', 'Earth', route,
                             wait_for_stop=Mock(), warp_confirmed=True)['state'], 'deferred')
            send.assert_not_called()

    def test_gate_pass_defers_non_gate_destinations_and_departure_gate(self):
        w = world([point('Nav', [100000,0,0]), point('Station', [80000,0,0],2),
                   point('Sector Gate Start', [1000,0,0],2), point('Sector Gate Finish', [120000,0,0],2)])
        result = gate_pass(w)
        self.assertTrue(result['executable'])
        self.assertNotIn('Station', [p['name'] for p in result['route']])
        self.assertNotIn('Sector Gate Start', [p['name'] for p in result['route']])
        self.assertTrue(all('gate' in leg['names'][-1].lower() for leg in result['legs']))

    def test_gate_pass_without_usable_gate_refuses_departure(self):
        result = gate_pass(world([point('Nav', [100000,0,0]), point('Sector Gate Start',[1000,0,0],2)]))
        self.assertFalse(result['executable'])


if __name__ == '__main__':
    unittest.main()
