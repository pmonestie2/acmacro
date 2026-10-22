from unittest import TestCase
from unittest.mock import Mock, patch

import ac
import gate_navigation as nav


class GateNavigationTests(TestCase):
    def transition_controller(self, positions, clock):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.player = ac.Player()
        controller.player.current_position = "saturn"
        controller.get_current_position = Mock(side_effect=positions)
        controller.polling_time = Mock(side_effect=clock)
        controller._yield = Mock()
        controller.prepare_departure = Mock()
        controller.wait_objects = Mock(side_effect=AssertionError("UI icon is not arrival evidence"))
        return controller

    def test_transition_accepts_destination_without_loading_or_inventory_icons(self):
        controller = self.transition_controller(["aba"], [0])
        self.assertTrue(controller.transition_space(expected="aba"))
        controller.wait_objects.assert_not_called()

    def test_undocking_activates_equipment_before_early_packet_departure(self):
        for previous in ('earthS', 'earthJOB1', 'earthJOB2'):
            with self.subTest(previous=previous):
                controller = self.transition_controller(['earth'], [0])
                controller.player.current_position = previous
                calls = Mock()
                controller.setup_kranken = calls.setup
                controller.prepare_departure = calls.depart
                self.assertTrue(controller.transition_space(expected='earth'))
                self.assertEqual([call[0] for call in calls.mock_calls], ['setup', 'depart'])
                self.assertTrue(controller._skip_next_travel_setup)

    def test_sector_transition_does_not_repeat_station_equipment_setup(self):
        controller = self.transition_controller(['aba'], [0])
        controller.setup_kranken = Mock()
        self.assertTrue(controller.transition_space(expected='aba'))
        controller.setup_kranken.assert_not_called()

    def test_duplicate_setup_after_early_departure_sends_no_equipment_input(self):
        controller = self.transition_controller([], [])
        controller._skip_next_travel_setup = True
        controller.key_press = Mock()
        controller.setup_kranken()
        controller.key_press.assert_not_called()
        self.assertFalse(controller._skip_next_travel_setup)

    def test_transition_waits_through_old_sector_and_unrecognized_loading_screen(self):
        controller = self.transition_controller(["saturn", None, "aba"], [0, 1, 10])
        self.assertTrue(controller.transition_space(expected="aba"))
        self.assertEqual(controller._yield.call_count, 2)

    def test_transition_wrong_sector_times_out_instead_of_claiming_success(self):
        controller = self.transition_controller(["earth"], [0, 61])
        self.assertFalse(controller.transition_space(expected="aba"))

    def test_hung_loading_screen_is_bounded_even_when_focus_wait_clock_barely_advances(self):
        controller = self.transition_controller([None], [0, 1])
        with patch('ac.time.monotonic', side_effect=[0, 121]):
            self.assertFalse(controller.transition_space(expected='aba'))

    def controller(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.player = ac.Player()
        controller.player.current_position = "saturn"
        controller.instance = Mock()
        controller.gate_navigation = "packet"
        for name in ("press", "get_map", "lclick", "_yield", "screenshot", "transition_space"):
            setattr(controller, name, Mock())
        controller.test_obj = Mock(side_effect=[True, False])
        return controller

    def test_already_active_gate_selects_and_enters_without_injection(self):
        controller = self.controller()
        with patch("ac.submit_gate_navigation", return_value={"state": "handler_returned"}) as send:
            self.assertTrue(controller.gate_internal(429, 568))
        send.assert_not_called()
        controller.get_map.assert_called_once()
        self.assertEqual([c.args for c in controller.lclick.call_args_list], [(429, 568), (1236, 515)])
        controller.transition_space.assert_called_once()

    def test_pre_submission_failure_uses_clicks(self):
        controller = self.controller()
        with patch("ac.submit_gate_navigation", return_value={"state": "fallback", "error": "inactive"}), patch("ac.time.sleep"):
            controller.gate_internal(429, 568)
        controller.get_map.assert_called_once()
        self.assertEqual(controller.lclick.call_args_list[0].args, (429, 568))

    def test_injected_approach_does_not_toggle_navigation_with_q(self):
        for state in ("handler_returned", "fallback"):
            with self.subTest(state=state):
                controller = self.controller()
                controller.test_obj.side_effect = None
                controller.test_obj.return_value = False
                def found(objects, **kwargs):
                    kwargs["on_found"]()
                    return True
                controller.wait_gate_objects = Mock(side_effect=found)
                with patch("ac.submit_gate_navigation", return_value={"state": state}), patch("ac.time.sleep"):
                    self.assertTrue(controller.gate_internal(429, 568))
                q_presses = [c for c in controller.press.call_args_list if c.args == ("q",)]
                self.assertEqual(len(q_presses), 0 if state == "handler_returned" else 1)

    def test_click_mode_never_injects(self):
        controller = self.controller()
        controller.gate_navigation = "click"
        with patch("ac.submit_gate_navigation") as send, patch("ac.time.sleep"):
            controller.gate_internal(429, 568)
        send.assert_not_called()
        controller.get_map.assert_called_once()

    def test_unknown_sector_gate_does_not_reuse_another_sector_id(self):
        controller = self.controller()
        controller.player.current_position = "uranus"
        with patch("ac.submit_gate_navigation") as send, patch("ac.time.sleep"):
            controller.gate_internal(386, 527)
        send.assert_not_called()
        controller.get_map.assert_called_once()

    def test_uncertain_submission_never_falls_back(self):
        controller = self.controller()
        controller.test_obj.side_effect = [False, False]
        with patch("ac.submit_gate_navigation", side_effect=nav.NavigationUncertain("timeout")):
            with self.assertRaises(nav.NavigationUncertain):
                controller.gate_internal(429, 568)
        controller.get_map.assert_called_once()
        controller.lclick.assert_called_once_with(429, 568)

    def test_failed_transition_is_not_reported_as_success(self):
        controller = self.controller()
        controller.transition_space.return_value = False
        with patch("ac.submit_gate_navigation", return_value={"state": "handler_returned"}):
            with self.assertRaisesRegex(ac.GameUnresponsive, "sector transition"):
                controller.gate_internal(429, 568)

    def test_travel_verifies_arrival_instead_of_assuming_destination(self):
        controller = self.controller()
        controller.gate = Mock(return_value=True)
        controller.get_current_position = Mock(return_value="aba")
        with self.assertRaisesRegex(ac.GameUnresponsive, "arrived in aba"):
            controller.travel_one("saturn", "akeron")
        self.assertEqual(controller.player.current_position, "saturn")

    def test_reused_proxy_pid_is_rejected_before_opening_process(self):
        instance = Mock(proxy_pid=12, proxy_born=100)
        with patch("gate_navigation.process_identity", return_value=200), patch.object(nav.proxy_commands, "Process") as process:
            self.assertEqual(nav.submit(instance, 0x1870f)["state"], "fallback")
        process.assert_not_called()

    def test_native_failure_after_submission_is_uncertain(self):
        instance = Mock(proxy_pid=12, proxy_born=100)
        def fail(args, report, frame):
            report.update(thread_id=42, state="failed")
            raise RuntimeError("native failure")
        with patch("gate_navigation.process_identity", return_value=100), patch.object(nav.proxy_commands, "Process"), patch.object(nav.proxy_commands, "submit", side_effect=fail):
            with self.assertRaises(nav.NavigationUncertain):
                nav.submit(instance, 0x1870f)

    def test_proven_pre_handler_rejection_allows_click_fallback(self):
        instance = Mock(proxy_pid=12, proxy_born=100)
        def rejected(args, report, frame):
            report.update(thread_id=42, state="not_sent", native_handler_attempted=False, error="guard rejection")
        with patch("gate_navigation.process_identity", return_value=100), patch.object(nav.proxy_commands, "Process"), patch.object(nav.proxy_commands, "submit", side_effect=rejected):
            self.assertEqual(nav.submit(instance, 0x1870f)["state"], "fallback")

    def test_prepared_departure_does_not_reselect_or_resubmit_route(self):
        controller = self.controller()
        controller._prepared_gate = dict(key=('saturn', 'akeron', controller.map_key()[:2]),
            active=False, injected=True, selected=True, entered=False, map_selected=True)
        def found(objects, **kwargs):
            kwargs['on_found']()
            return True
        controller.wait_gate_objects = Mock(side_effect=found)
        with patch('ac.submit_gate_navigation') as send:
            self.assertTrue(controller.gate_internal(429, 568))
        controller.get_map.assert_not_called()
        controller.press.assert_not_called()
        send.assert_not_called()
        controller.lclick.assert_called_once_with(1236, 515)

    def test_travel_reuses_confirmed_arrival_without_another_map(self):
        controller = self.controller()
        controller.get_current_position = Mock()
        controller.gate = Mock(side_effect=lambda *args: setattr(controller, '_confirmed_arrival', 'akeron'))
        controller.travel_one('saturn', 'akeron')
        controller.get_current_position.assert_not_called()

    def test_arrival_prepares_departure_before_focus_is_released(self):
        from contextlib import contextmanager
        controller = self.transition_controller(['aba'], [0])
        held = []
        @contextmanager
        def focus():
            held.append(True)
            try:
                yield
            finally:
                held.pop()
        controller.gate_departure_focus = focus
        controller.prepare_departure.side_effect = lambda sector: self.assertTrue(held)
        self.assertTrue(controller.transition_space(expected='aba'))
        controller.prepare_departure.assert_called_once_with('aba')

    def test_uranus_unload_stop_prevents_automatic_departure(self):
        controller = self.controller()
        controller.departure_stops = {"uranus"}
        controller.shortest_path = Mock(return_value=["uranus", "pluto"])
        controller.prepare_gate = Mock()
        controller.prepare_departure("uranus")
        controller.prepare_gate.assert_not_called()

    def test_job_delivery_timeout_keeps_cargo_and_does_not_click_control(self):
        controller = self.controller()
        controller.test_obj = Mock(return_value=False)
        controller.key_press = Mock()
        controller.wait_gate_objects = Mock(side_effect=[True, False])
        with self.assertRaisesRegex(ac.GameUnresponsive, "cargo retained"):
            controller.gate_sat(331, 620, approach_timeout=120, strict=True)
        controller.lclick.assert_called_once_with(331, 620)
        self.assertEqual(controller.wait_gate_objects.call_args.kwargs['timeout'], 120)

    def test_completed_low_job_without_control_returns_without_warp_or_relogin(self):
        controller = self.controller()
        controller.test_obj = Mock(return_value=False)
        controller.key_press = Mock()
        controller.wait_gate_objects = Mock(return_value=False)
        controller.gate_sat(331, 620, approach_timeout=120, strict=True, skip_completed=True)
        controller.lclick.assert_called_once_with(331, 620)
        controller.key_press.assert_not_called()
        controller.wait_gate_objects.assert_called_once_with([ac.SAT_OBJ, ac.ISAT_OBJ], timeout=12, fast=False)

    def test_delivery_control_becoming_active_during_selection_does_not_warp_again(self):
        controller = self.controller()
        controller.test_obj = Mock(side_effect=[False, True])
        controller.key_press = Mock()
        controller.wait_gate_objects = Mock(return_value=True)
        controller.gate_sat(331, 620, approach_timeout=120, strict=True, skip_completed=True)
        self.assertNotIn(('q',), [c.args for c in controller.key_press.call_args_list])
        self.assertEqual(controller.lclick.call_count, 8)

    def test_job_delivery_uses_active_control_without_inventory_discard(self):
        controller = self.controller()
        controller.test_obj = Mock(return_value=True)
        controller.key_press = Mock()
        controller.wait_gate_objects = Mock(return_value=True)
        controller.empty_inventory = Mock()
        controller.gate_sat(329, 490, approach_timeout=120, strict=True)
        self.assertEqual(controller.lclick.call_count, 8)
        controller.lclick.assert_any_call(1238, 516)
        controller.empty_inventory.assert_not_called()

    def test_active_station_satisfies_selection_wait_after_focus_timeout(self):
        controller = self.controller()
        controller.test_obj = Mock(side_effect=[False, True])
        state = dict(active=False, injected=False, selected=False, entered=False)
        enter = Mock()
        def found(objects, **kwargs):
            self.assertIn(ac.STATION_OBJ, objects)
            kwargs['on_found']()
            return True
        controller.wait_gate_objects = Mock(side_effect=found)
        self.assertTrue(controller.wait_selected_gate(state, 10, 20, on_enter=enter))
        self.assertTrue(state['entered'])
        enter.assert_called_once()
        controller.press.assert_not_called()

    def test_lost_target_is_reselected_without_resending_navigation(self):
        controller = self.controller()
        controller.polling_time = Mock(side_effect=[0, 17])
        controller.test_obj = Mock(return_value=False)
        repair = controller.selection_repair(10, 20)
        with patch('ac.submit_gate_navigation') as send:
            repair()
        controller.get_map.assert_called_once()
        controller.lclick.assert_called_once_with(10, 20)
        controller.press.assert_not_called()
        send.assert_not_called()

    def test_target_repair_preserves_existing_panel(self):
        controller = self.controller()
        controller.polling_time = Mock(side_effect=[0, 17])
        controller.test_obj = Mock(return_value=True)
        controller.selection_repair(10, 20)()
        controller.get_map.assert_not_called()
        controller.lclick.assert_not_called()

    def test_transient_focus_refusal_retries_before_sending_input(self):
        controller = self.controller()
        with patch('ac.focus_window', side_effect=[False, True]) as foreground, patch('ac.time.sleep'):
            self.assertTrue(controller.focus_owned_window())
        self.assertEqual(foreground.call_count, 2)
