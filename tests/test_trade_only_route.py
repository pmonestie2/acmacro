from contextlib import nullcontext
import unittest
from unittest.mock import Mock, call, patch

import pluto
from player import Player


class EndRoute(Exception):
    pass


class TradeOnlyRouteTests(unittest.TestCase):
    def setUp(self):
        self.player = Player()
        self.player.type_ = "new"
        patched = patch.object(pluto, "PLAYER", self.player)
        patched.start()
        self.addCleanup(patched.stop)
        self.runner = pluto.PlutoRunner()
        self.runner.controller = Mock()

    def test_earth_sells_and_exits_without_visiting_jobs(self):
        for name in ("walk_job", "pick_job", "close_job"):
            setattr(self.runner, name, Mock())
        self.runner.all_earth()
        controller = self.runner.controller
        controller.earth3_vendor.assert_called_once()
        self.assertEqual([c[0] for c in controller.method_calls if c[0].startswith("earth")],
                         ["earth1_2", "earth2_3", "earth3_vendor", "earth3_2", "earth2_1", "earth1_space"])
        for name in ("walk_job", "pick_job", "close_job"):
            getattr(self.runner, name).assert_not_called()
        self.assertEqual(self.player.current_dest, "charonS")

    def test_charon_buys_without_jobs_or_discarding_inventory(self):
        self.runner.all_charon()
        controller = self.runner.controller
        controller.charon3_vendor.assert_called_once()
        controller.count_mission.assert_not_called()
        controller.clear_jobs.assert_not_called()
        controller.empty_inventory.assert_not_called()
        self.assertEqual([c[0] for c in controller.method_calls if c[0].startswith("charon")],
                         ["charon1_2", "charon2_3", "charon3_vendor", "charon3_2", "charon2_1", "charon_space"])

    def test_route_returns_to_charon_without_satellite_or_monster_detour(self):
        controller = self.runner.controller
        self.player.current_position = "charonS"
        self.player.current_dest = "charonS"
        self.runner.all_charon = Mock(side_effect=lambda: self.player.set_pos("pluto"))
        self.runner.all_earth = Mock(side_effect=lambda: self.player.set_pos("earth"))
        self.runner.pluto_dump = Mock()
        controller.shortest_path.side_effect = lambda start, end: [start, end]
        controller.travel_one.side_effect = lambda start, end: self.player.set_pos(end)
        controller._yield.side_effect = [None, None, None, EndRoute()]
        with self.assertRaises(EndRoute):
            self.runner.route()
        self.runner.all_charon.assert_called_once()
        self.runner.all_earth.assert_called_once()
        self.runner.pluto_dump.assert_not_called()
        self.assertEqual(controller.shortest_path.call_args_list,
                         [call("pluto", "earthS"), call("earth", "charonS")])
        self.assertEqual(self.player.current_position, "charonS")

    def test_empty_cargo_heads_to_charon_and_loaded_cargo_heads_to_earth(self):
        controller = self.runner.controller
        controller.with_focus.side_effect = lambda name: nullcontext()
        for loaded, target in ((False, "charonS"), (True, "earthS")):
            with self.subTest(loaded=loaded):
                controller.test_obj.return_value = loaded
                self.runner.find_dir()
                self.assertEqual(self.player.current_dest, target)

    def test_existing_player_still_visits_job_rooms(self):
        self.player.type_ = "je"
        for name in ("walk_job", "pick_job", "close_job"):
            setattr(self.runner, name, Mock())
        self.runner.all_earth()
        self.runner.controller.earth2_j.assert_called_once()
        self.runner.controller.earthj_2.assert_called_once()
        self.runner.pick_job.assert_called_once()
        self.assertEqual(self.player.current_dest, "pluto")

    def test_low_jobs_dump_before_leaving_uranus_on_resume_or_in_transit(self):
        for start in ("uranus", "saturn"):
            with self.subTest(start=start):
                controller = self.runner.controller = Mock()
                self.runner.low_job = True
                self.runner._low_jobs_delivered = False
                self.player.type_ = "je"
                self.player.current_position = start
                self.player.current_dest = "pluto"
                controller.shortest_path.side_effect = lambda current, target: pluto.AcController.shortest_path(controller, current, target)
                def travel(current, target):
                    if current == "uranus":
                        self.assertEqual(controller.gate_sat.call_args_list, [
                            call(331, 620, approach_timeout=120, strict=True, skip_completed=True),
                            call(329, 490, approach_timeout=120, strict=True, skip_completed=True)])
                    self.player.set_pos(target)
                controller.travel_one.side_effect = travel
                controller._yield.side_effect = [None, EndRoute()]
                with self.assertRaises(EndRoute):
                    self.runner.route()
                controller.empty_inventory.assert_not_called()
                self.assertEqual(controller.gate_sat.call_count, 2)
                self.assertEqual(self.player.current_position, "charonS")

    def test_low_job_route_from_earth_requires_uranus_before_charon(self):
        self.runner.low_job = True
        self.player.type_ = "je"
        self.player.current_position = "earth"
        self.player.current_dest = "pluto"
        controller = self.runner.controller
        controller.shortest_path.side_effect = lambda current, target: pluto.AcController.shortest_path(controller, current, target)
        controller.travel_one.side_effect = lambda current, target: self.player.set_pos(target)
        controller._yield.side_effect = [None, EndRoute()]
        self.runner.pluto_dump = Mock()
        with self.assertRaises(EndRoute):
            self.runner.route()
        self.assertEqual(controller.travel_one.call_args_list, [
            call("earth", "aba"), call("aba", "saturn"), call("saturn", "uranus"),
            call("uranus", "pluto"), call("pluto", "charonS")])
        self.runner.pluto_dump.assert_not_called()
        controller.empty_inventory.assert_not_called()
        self.assertEqual(controller.gate_sat.call_count, 2)

    def test_low_job_undocking_sets_uranus_as_destination(self):
        self.runner.low_job = True
        self.player.type_ = "je"
        self.runner._low_jobs_delivered = True
        self.runner.walk_job = Mock()
        self.runner.pick_job = Mock()
        self.runner.close_job = Mock()
        self.runner.controller.earth1_space.side_effect = lambda: self.assertEqual(self.player.current_dest, "uranus")
        self.runner.all_earth()
        self.assertFalse(self.runner._low_jobs_delivered)

    def test_regular_route_keeps_cargo_in_uranus(self):
        self.player.current_position = "uranus"
        self.player.current_dest = "pluto"
        controller = self.runner.controller
        controller.shortest_path.return_value = ["uranus", "pluto"]
        controller.travel_one.side_effect = lambda current, target: self.player.set_pos(target)
        controller._yield.side_effect = EndRoute()
        with self.assertRaises(EndRoute):
            self.runner.route()
        controller.empty_inventory.assert_not_called()
        controller.gate_sat.assert_not_called()

    def test_failed_uranus_delivery_does_not_continue_to_pluto(self):
        self.runner.low_job = True
        self.player.current_position = "uranus"
        self.player.current_dest = "pluto"
        controller = self.runner.controller
        controller.gate_sat.side_effect = pluto.GameUnresponsive("delivery not ready")
        with self.assertRaises(pluto.GameUnresponsive):
            self.runner.route()
        controller.empty_inventory.assert_not_called()
        controller.travel_one.assert_not_called()

    def test_low_job_recounts_preserve_sort_order_across_batches(self):
        self.runner.low_job = True
        self.player.mission_count = 4
        controller = self.runner.controller
        controller.with_focus.side_effect = lambda name: nullcontext()
        controller.polling_time.return_value = 0
        controller.count_mission.side_effect = [1, 1, 2, 4]
        self.runner.open_job = Mock()
        level_clicks = []
        def click(x, y):
            if (x, y) == (607, 295):
                level_clicks.append((x, y))
        controller.lclick.side_effect = click
        def batch(check):
            # A second level click would flip ascending to descending.
            self.assertEqual(len(level_clicks), 1)
            self.assertEqual(check, self.runner.is_good_low_job)
        self.runner.pickBatchJob = Mock(side_effect=batch)
        self.runner.pick_job()
        self.assertEqual(self.runner.pickBatchJob.call_count, 3)
        self.assertEqual(controller.count_mission.call_count, 4)
        self.assertEqual(len(level_clicks), 1)
