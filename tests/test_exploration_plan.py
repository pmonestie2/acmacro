import unittest

from exploration_plan import plan, split_legs, order_points


def point(identity, xyz, kind=1, visited=False, name=None):
    return dict(id=identity, name=name or identity, raw_xyz=xyz, kind='map_object',
                sector='Earth', navigation_present=True, nav_type=kind, visited=visited)


class ExplorationPlanTests(unittest.TestCase):
    def test_route_covers_each_point_once_and_orders_by_distance(self):
        points = [point('far', [30, 0, 0]), point('near', [10, 0, 0]), point('mid', [20, 0, 0])]
        self.assertEqual([p['id'] for p in order_points(points, [0, 0, 0])], ['near', 'mid', 'far'])

    def test_destination_only_points_end_each_leg(self):
        route = [point('nav', [0, 0, 0]), point('gate', [1, 0, 0], 2),
                 point('gate2', [2, 0, 0], 2), point('nav2', [3, 0, 0])]
        self.assertEqual([[p['id'] for p in leg] for leg in split_legs(route)],
                         [['nav', 'gate'], ['gate2'], ['nav2']])

    def test_navigation_flags_override_resource_names(self):
        objects = [point('new', [0, 0, 0]), point('old', [1, 0, 0], visited=True),
                   point('rock', [2, 0, 0], name='Asteroid 1')]
        result = plan(dict(recent=dict(sector='Earth'), catalog=objects), now=100)
        self.assertEqual({p['id'] for p in result['route']}, {'new', 'rock'})
        self.assertFalse(result['origin_is_fresh'])

    def test_observed_hostile_stop_is_blocked_but_friendly_is_allowed(self):
        objects = [point('danger', [0, 0, 0]), point('safe', [50000, 0, 0]),
                   dict(kind='ship_or_npc', name='enemy', hostility='hostile', raw_xyz=[1000, 0, 0], position_seen=100),
                   dict(kind='ship_or_npc', name='vendor', hostility='friendly', raw_xyz=[50000, 0, 0], position_seen=100)]
        result = plan(dict(recent=dict(sector='Earth'), catalog=objects), now=100)
        self.assertEqual([p['id'] for p in result['route']], ['safe'])
        self.assertEqual(result['blocked'][0]['blocked_by'], ['enemy'])

    def test_recorded_respawn_hazard_is_preserved(self):
        result = plan(dict(recent=dict(sector='Earth'), catalog=[point('nav', [0, 0, 0])]),
                      hazards=[dict(name='Vrix', raw_xyz=[1, 0, 0])], now=100)
        self.assertFalse(result['route'])
        self.assertEqual(len(result['blocked']), 1)

    def test_finishes_at_closest_gate_even_when_already_explored(self):
        objects = [point('nav', [100000, 0, 0]),
                   point('far', [0, 0, 0], 2, True, 'Sector Gate Far'),
                   point('near', [110000, 0, 0], 2, True, 'Sector Gate Near')]
        result = plan(dict(recent=dict(sector='Earth'), catalog=objects), now=100)
        self.assertEqual([p['id'] for p in result['route']], ['nav', 'near'])
        self.assertEqual(len(result['exploration_route']), 1)
        self.assertTrue(result['executable'])

    def test_no_gate_or_dangerous_closest_gate_cannot_execute(self):
        objects = [point('nav', [0, 0, 0])]
        self.assertFalse(plan(dict(recent=dict(sector='Earth'), catalog=objects))['executable'])
        objects += [point('gate', [20000, 0, 0], 2, True, 'Sector Gate')]
        result = plan(dict(recent=dict(sector='Earth'), catalog=objects),
                      hazards=[dict(name='enemy', raw_xyz=[21000, 0, 0])])
        self.assertFalse(result['executable'])

    def test_final_gate_cost_changes_optimal_order(self):
        points = [point('a', [0, 0, 0]), point('b', [100000, 0, 0])]
        gate = point('gate', [0, 0, 0], 2, True, 'Sector Gate')
        route = order_points(points, [50000, 0, 0], [gate])
        self.assertEqual([p['id'] for p in route], ['b', 'a'])

    def test_runner_requires_discovery_and_fresh_endpoint_position(self):
        from explore_sector import leg_complete
        nav = point('nav', [0, 0, 0])
        world = dict(recent=dict(player=dict(raw_xyz=[0, 0, 0], last_seen=100)))
        self.assertFalse(leg_complete(world, [nav], {'nav': nav}, now=100))
        self.assertTrue(leg_complete(world, [nav], {'nav': dict(nav, visited=True)}, now=100))
        self.assertFalse(leg_complete(world, [nav], {'nav': dict(nav, visited=True)}, now=120))

    def test_new_contact_does_not_interrupt_submitted_warp_path(self):
        from explore_sector import check_leg
        nav = point('nav', [0, 0, 0])
        enemy = dict(id='enemy', kind='ship_or_npc', hostility='hostile',
                     raw_xyz=[100, 0, 0], position_seen=100)
        world = dict(recent=dict(sector='Earth'), catalog=[nav, enemy])
        with self.assertRaises(RuntimeError):
            check_leg(world, [nav], [], now=100)
        self.assertIn('nav', check_leg(world, [nav], [], now=100, check_hazards=False))

    def test_gate_arrival_accepts_last_inflight_position_within_gate_range(self):
        from explore_sector import leg_complete
        gate = point('gate', [0, 0, 0], 2, True, 'Sector Gate')
        world = dict(recent=dict(player=dict(raw_xyz=[2900, 0, 0], last_seen=100)))
        self.assertTrue(leg_complete(world, [gate], {'gate': gate}, now=140, position_floor=95))
        self.assertFalse(leg_complete(world, [gate], {'gate': gate}, now=140, position_floor=110))

    def test_snapshot_retries_recorder_file_replacement(self):
        from unittest.mock import Mock, patch
        from explore_sector import read_snapshot
        path = Mock()
        path.read_text.side_effect = [PermissionError(), '{"state":"running"}']
        with patch('explore_sector.time.sleep') as sleep:
            self.assertEqual(read_snapshot(path), {'state': 'running'})
        sleep.assert_called_once_with(.05)

    def test_uses_closest_safe_gate_when_nearest_has_known_mobs(self):
        objects = [point('nav', [0, 0, 0]),
                   point('bad', [20000, 0, 0], 2, True, 'Sector Gate Bad'),
                   point('safe', [80000, 0, 0], 2, True, 'Sector Gate Safe')]
        result = plan(dict(recent=dict(sector='Earth'), catalog=objects),
                      hazards=[dict(name='Vrix', raw_xyz=[21000, 0, 0])])
        self.assertEqual(result['final_gate']['id'], 'safe')
        self.assertEqual(result['excluded_gates'], ['Sector Gate Bad'])
        self.assertTrue(result['executable'])
