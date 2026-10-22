import unittest

from gravity_route import avoid_wells, crosses_well


class GravityRouteTests(unittest.TestCase):
    def test_checks_entire_segment_with_margin(self):
        well = dict(raw_xyz=[0, 0, 0], radius=1, margin=1)
        self.assertTrue(crosses_well([-10, 0, 0], [10, 0, 0], well))
        self.assertFalse(crosses_well([-10, 3, 0], [10, 3, 0], well))

    def test_detours_through_real_warp_path_ids(self):
        well = dict(raw_xyz=[0, 0, 0], radius=2, margin=0)
        target = dict(id='gate', name='Gate', nav_type=2, raw_xyz=[10, 0, 0])
        detour = dict(id='nav', name='Nav', nav_type=1, raw_xyz=[0, 10, 0])
        result = avoid_wells([target], [-10, 0, 0], [detour], [well])
        self.assertEqual([p['id'] for p in result], ['nav', 'gate'])
        for a, b in zip([[-10, 0, 0]]+[p['raw_xyz'] for p in result], [p['raw_xyz'] for p in result]):
            self.assertFalse(crosses_well(a, b, well))

    def test_cannot_use_destination_only_point_as_detour(self):
        well = dict(raw_xyz=[0, 0, 0], radius=2, margin=0)
        target = dict(id='gate', name='Gate', nav_type=2, raw_xyz=[10, 0, 0])
        detour = dict(id='othergate', nav_type=2, raw_xyz=[0, 10, 0])
        with self.assertRaises(RuntimeError):
            avoid_wells([target], [-10, 0, 0], [detour], [well])
