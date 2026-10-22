import multiprocessing
import time
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import Mock, patch

from PIL import Image
import ac
import pluto
from focus import SharedFocus


def borrower(connection, namespace, instance, toon, claim_game=True, singleton=False, slots=None):
    try:
        focus = SharedFocus(instance, toon, namespace=namespace, claim_game=claim_game, singleton=singleton, slots=slots)
    except Exception as error:
        connection.send(("error", str(error)))
        return
    connection.send(("ready", focus.key))
    try:
        while True:
            command = connection.recv()
            if command == "acquire":
                focus.acquire()
                connection.send(("owned", focus.key))
            elif command == "release":
                focus.release()
                connection.send(("released", focus.key))
            elif command == "borrow":
                focus.acquire()
                connection.send(("owned", focus.key))
                time.sleep(0.05)
                focus.release()
                connection.send(("released", focus.key))
            elif command == "close":
                break
    finally:
        focus.close()


class FocusTests(unittest.TestCase):
    def test_four_vendor_window_slots_are_claimed_across_processes(self) -> None:
        slots = tuple(f'vendor-window-{index}' for index in range(4))
        self.focus.close()
        self.focus = SharedFocus('same-client-path', 'A', namespace=self.namespace, slots=slots)
        self.addCleanup(self.focus.close)
        second, second_pipe, second_ready = self.worker('same-client-path', 'B', slots=slots)
        third, third_pipe, third_ready = self.worker('same-client-path', 'C', slots=slots)
        fourth, fourth_pipe, fourth_ready = self.worker('same-client-path', 'D', slots=slots)
        with self.focus.state() as state:
            owned = [state['members'][key]['slot'] for key in
                     (self.focus.key, second_ready[1], third_ready[1], fourth_ready[1])]
        self.assertEqual(owned, [0, 1, 2, 3])
        second_pipe.send('close')
        third_pipe.send('close')
        fourth_pipe.send('close')
        second.join(5)
        third.join(5)
        fourth.join(5)

    def setUp(self):
        self.namespace = "Local\\acmacro-test-" + uuid.uuid4().hex
        self.focus = SharedFocus("game-a", "A", namespace=self.namespace)
        self.addCleanup(self.focus.close)

    def worker(self, instance="game-b", toon="B", claim_game=True, singleton=False, slots=None):
        parent, child = multiprocessing.Pipe()
        process = multiprocessing.get_context("spawn").Process(
            target=borrower, args=(child, self.namespace, instance, toon, claim_game, singleton, slots))
        process.start()
        child.close()

        def cleanup():
            if process.is_alive():
                process.terminate()
            process.join(5)
            parent.close()
            process.close()

        self.addCleanup(cleanup)
        self.assertTrue(parent.poll(10), "worker failed to start")
        return process, parent, parent.recv()

    def wait_queue(self, length):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.focus.state() as state:
                if len(state["queue"]) == length:
                    return
            time.sleep(0.01)
        self.fail("borrowers did not enter the focus queue")

    def test_two_auto_slots_are_atomic_and_restart_rechecks_availability(self):
        slots = ("client-a", "client-b")
        self.focus.close()
        self.focus = SharedFocus("pending", "A", namespace=self.namespace, slots=slots, preferred_slot=1)
        self.addCleanup(self.focus.close)
        self.assertEqual(self.focus.slot, 1)
        process, pipe, ready = self.worker(slots=slots)
        with self.focus.state() as state:
            self.assertEqual(state["members"][ready[1]]["slot"], 0)
        unused, third, result = self.worker("pending", "C", slots=slots)
        self.assertEqual(result[0], "error")
        self.assertIn("Both", result[1])
        self.assertEqual(self.focus.claim_available_slot(slots), 1)
        pipe.send("close")
        process.join(5)
        self.assertEqual(self.focus.claim_available_slot(slots), 0)

    def test_keeps_ownership_without_waiters_and_yields_at_boundary(self):
        self.focus.acquire()
        self.focus.boundary()
        self.assertTrue(self.focus.owned)
        unused, pipe, ready = self.worker()
        self.assertEqual(ready[0], "ready")
        pipe.send("acquire")
        self.wait_queue(1)
        self.assertFalse(pipe.poll(0.05))
        self.focus.depth = 1  # An unfinished held input cannot yield.
        self.focus.boundary()
        self.assertTrue(self.focus.owned)
        self.focus.depth = 0
        self.focus.boundary()
        self.assertFalse(self.focus.owned)
        self.assertTrue(pipe.poll(5))
        self.assertEqual(pipe.recv()[0], "owned")

    def test_twenty_second_expiry_releases_without_waiters(self):
        self.focus.acquire()
        self.focus.acquired_at = time.monotonic() - 20.1
        self.focus.boundary()
        self.assertFalse(self.focus.owned)

    def test_handoff_leaves_short_pause_but_same_owner_reacquires_immediately(self):
        with self.focus.state() as state:
            state["last_owner"] = "previous-process"
            state["released_at"] = time.monotonic()
        started = time.monotonic()
        self.focus.acquire()
        self.assertGreaterEqual(time.monotonic() - started, self.focus.HANDOFF_PAUSE - 0.01)
        self.focus.release()
        with patch("focus.time.sleep") as sleep:
            self.focus.acquire()
        sleep.assert_not_called()

    def test_http_request_yields_at_safe_boundary(self):
        self.focus.acquire()
        self.focus.requested.set()
        self.focus.depth = 1
        self.focus.boundary()
        self.assertTrue(self.focus.owned)
        self.focus.depth = 0
        self.focus.boundary()
        self.assertFalse(self.focus.owned)

    def test_borrower_notifies_owner_over_http_before_handoff(self):
        self.focus.start_comms(lambda: None)
        self.focus.acquire()
        unused, pipe, ready = self.worker()
        pipe.send("acquire")
        self.assertTrue(self.focus.requested.wait(3))
        self.assertTrue(self.focus.owned)
        self.focus.boundary()
        self.assertTrue(pipe.poll(5))
        self.assertEqual(pipe.recv()[0], "owned")

    def test_expired_protected_pause_releases_then_restores_focus(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        controller._logging_in = True
        self.focus.acquire()
        self.focus.depth = 1
        self.focus.acquired_at = time.monotonic() - 20.1
        with patch.object(self.focus, "release", wraps=self.focus.release) as release, \
             patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True):
            controller._yield(0.01)
        release.assert_called_once()
        self.assertTrue(self.focus.owned)
        self.assertEqual(self.focus.depth, 1)
        self.assertLess(time.monotonic() - self.focus.acquired_at, 1)
        self.focus.depth = 0

    def test_singleton_retains_focus_and_rejects_another_game(self):
        self.focus.close()
        self.focus = SharedFocus("game-a", "A", namespace=self.namespace, singleton=True)
        self.addCleanup(self.focus.close)
        self.focus.acquire()
        self.focus.acquired_at = time.monotonic() - 30
        self.focus.requested.set()
        self.focus.boundary()
        self.assertTrue(self.focus.owned)
        unused, pipe, result = self.worker()
        self.assertEqual(result[0], "error")
        self.assertIn("singleton", result[1])

    def test_exclusive_section_blocks_peer_and_ignores_normal_twenty_second_limit(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        controller._logging_in = True
        unused, pipe, ready = self.worker()
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True), \
             patch("ac.win32gui.GetForegroundWindow", return_value=123):
            with controller.exclusive_focus("test deployment"):
                pipe.send("acquire")
                self.wait_queue(1)
                self.focus.acquired_at = time.monotonic() - 25
                controller._yield(0.01)
                self.assertTrue(self.focus.owned)
                self.assertFalse(pipe.poll(0.05))
                self.assertGreater(controller.exclusive_remaining(reserve=15), 29)
        self.assertTrue(pipe.poll(5))
        self.assertEqual(pipe.recv()[0], "owned")
        self.assertFalse(self.focus.owned)
        self.assertIsNone(controller._exclusive_deadline)

    def test_exclusive_timeout_prevents_next_input_and_releases_mutex(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True), \
             patch("ac.win32gui.GetForegroundWindow", return_value=123), patch("ac.key_press") as key:
            with self.assertRaisesRegex(ac.GameUnresponsive, "45-second"):
                with controller.exclusive_focus("test deployment"):
                    controller._exclusive_deadline = time.monotonic() - 1
                    controller.key_press("q")
        key.assert_not_called()
        self.assertFalse(self.focus.owned)
        self.assertEqual(self.focus.depth, 0)
        self.assertIsNone(controller._exclusive_deadline)

    def test_singleton_bypasses_exclusive_lock_and_budget(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test", singleton=True)
        with patch.object(controller, "input_action") as acquire:
            with controller.exclusive_focus("test deployment"):
                self.assertIsNone(controller._exclusive_deadline)
                self.assertEqual(controller.exclusive_remaining(reserve=15), 45)
        acquire.assert_not_called()

    def test_singleton_cannot_start_alongside_another_macro(self):
        unused, pipe, result = self.worker(singleton=True)
        self.assertEqual(result[0], "error")
        self.assertIn("singleton", result[1])


    def test_three_processes_take_fifo_turns(self):
        self.focus.acquire()
        unused, second, ready = self.worker()
        unused, third, ready = self.worker("game-c", "C")
        second.send("acquire")
        self.wait_queue(1)
        third.send("acquire")
        self.wait_queue(2)
        self.focus.boundary()
        self.assertTrue(second.poll(5))
        self.assertEqual(second.recv()[0], "owned")
        self.assertFalse(third.poll(0.05))
        second.send("release")
        self.assertTrue(second.poll(5))
        self.assertEqual(second.recv()[0], "released")
        self.assertTrue(third.poll(5))
        self.assertEqual(third.recv()[0], "owned")

    def test_crashed_owner_and_crashed_waiter_do_not_block(self):
        process, pipe, ready = self.worker()
        pipe.send("acquire")
        self.assertTrue(pipe.poll(5))
        self.assertEqual(pipe.recv()[0], "owned")
        process.terminate()
        process.join(5)
        self.focus.acquire()  # Reclaims the abandoned Windows mutex.
        process2, pipe2, ready = self.worker("game-c", "C")
        pipe2.send("acquire")
        self.wait_queue(1)
        process2.terminate()
        process2.join(5)
        self.focus.boundary()
        self.assertTrue(self.focus.owned)

    def test_duplicate_directory_and_fifth_instance_are_rejected(self):
        unused, pipe, result = self.worker("game-a", "B")
        self.assertEqual(result[0], "error")
        self.assertIn("already owns", result[1])
        self.worker("game-b", "B")
        self.worker("game-c", "C")
        unused, pipe, fourth = self.worker("game-d", "D")
        self.assertEqual(fourth[0], 'ready')
        unused, pipe, result = self.worker("game-e", "E")
        self.assertEqual(result[0], "error")
        self.assertIn("four", result[1])

    def test_screenshots_are_taken_under_verified_focus(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        controller.client_size = controller.screen_size = (1280, 768)
        controller.offset_x = controller.offset_y = 0

        def screenshot(**kwargs):
            self.assertTrue(self.focus.owned)
            self.assertGreater(self.focus.depth, 0)
            return Image.new("RGB", (1280, 768))

        with patch.object(controller, "require_window"), patch.object(controller, "refresh_offset"), \
             patch("ac.focus_window", return_value=True) as focus_window, \
             patch("ac.pag.screenshot", side_effect=screenshot) as capture:
            controller.screenshot()
        focus_window.assert_called_once_with(123)
        capture.assert_called_once()
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=False), \
             patch("ac.pag.screenshot") as capture:
            with self.assertRaisesRegex(RuntimeError, "Cannot focus"):
                controller.screenshot()
        capture.assert_not_called()

    def test_layout_helper_waits_without_consuming_a_game_slot(self):
        self.focus.acquire()
        self.worker("game-b", "B")
        self.worker("game-c", "C")
        unused, helper, ready = self.worker("layout", "Layout", claim_game=False)
        self.assertEqual(ready[0], "ready")
        helper.send("acquire")
        self.wait_queue(1)
        self.assertFalse(helper.poll(0.05))
        self.focus.boundary()
        self.assertTrue(helper.poll(5))
        self.assertEqual(helper.recv()[0], "owned")

    def test_held_input_blocks_interrupts_and_releases_after_exception(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True), \
             patch("ac.win32gui.GetForegroundWindow", return_value=123), \
             patch("ac.pag.mouseDown"), patch("ac.pag.mouseUp") as mouse_up, \
             patch("ac.run_interrupts") as interrupts, patch("ac.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "drag failed"):
                with controller.input_action():
                    controller.mouseDown()
                    controller._yield(0.1)
                    interrupts.assert_not_called()
                    raise RuntimeError("drag failed")
            mouse_up.assert_called_once()
            self.assertEqual(self.focus.depth, 0)
            self.assertFalse(controller._mouse_held)

    def test_job_row_keeps_selection_loading_and_acceptance_together(self):
        runner = pluto.PlutoRunner()
        controller = Mock()
        runner.controller = controller
        in_action = []
        decisions = []

        @contextmanager
        def action(name):
            in_action.append(True)
            try:
                yield
            finally:
                in_action.pop()

        def click(x, y):
            if x in (290, 1089):
                self.assertTrue(in_action)

        def pause(*args, **kwargs):
            self.assertTrue(in_action, "Short row/page waits must remain in their turn")

        def recognize():
            self.assertTrue(in_action)
            decisions.append(True)
            return True

        controller.with_focus.side_effect = action
        controller.lclick.side_effect = click
        controller._yield.side_effect = pause
        runner.pickBatchJob(recognize)
        self.assertEqual(len(decisions), 20)
        self.assertEqual(sum(call.args == ("job rows",) for call in controller.with_focus.call_args_list), 6)
        self.assertEqual(controller.with_focus.call_count, 8)

    def test_grouped_short_actions_block_peer_until_section_ends(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        controller._logging_in = True
        unused, pipe, ready = self.worker()
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True), \
             patch("ac.win32gui.GetForegroundWindow", return_value=123), patch("ac.pag.press"):
            with controller.with_focus("test short sequence"):
                pipe.send("acquire")
                self.wait_queue(1)
                controller.press("m")
                controller._yield(0.01)
                controller.press("i")
                self.assertTrue(self.focus.owned)
                self.assertFalse(pipe.poll(0.05))
            self.assertTrue(pipe.poll(5))
            self.assertEqual(pipe.recv()[0], "owned")
        self.assertFalse(self.focus.owned)
        self.assertIsNone(self.focus.section)

    def test_grouped_section_still_lends_focus_at_twenty_second_budget(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        controller._logging_in = True
        unused, pipe, ready = self.worker()
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True), \
             patch("ac.win32gui.GetForegroundWindow", return_value=123):
            with controller.with_focus("test capped sequence"):
                pipe.send("borrow")
                self.wait_queue(1)
                self.focus.acquired_at = time.monotonic() - 20.1
                controller._yield(0.01)
                self.assertTrue(pipe.poll(5))
                self.assertEqual(pipe.recv()[0], "owned")
                self.assertTrue(pipe.poll(5))
                self.assertEqual(pipe.recv()[0], "released")
                self.assertTrue(self.focus.owned)
                self.assertEqual(self.focus.depth, 1)
                self.assertLess(time.monotonic() - self.focus.acquired_at, 1)

    def test_long_key_lends_before_it_would_overrun_group_budget(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        unused, pipe, ready = self.worker()
        def held_key(*args):
            self.assertTrue(self.focus.owned)
            self.assertGreater(self.focus.depth, 0)
            self.assertLess(time.monotonic() - self.focus.acquired_at, 1)
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True), \
             patch("ac.win32gui.GetForegroundWindow", return_value=123), \
             patch("ac.key_press", side_effect=held_key) as key, \
             patch.object(self.focus, "release", wraps=self.focus.release) as release:
            with controller.with_focus("walking"):
                pipe.send("borrow")
                self.wait_queue(1)
                self.focus.acquired_at = time.monotonic() - 18
                controller.key_press("a", 4)
                self.assertTrue(pipe.poll(5))
                self.assertEqual(pipe.recv()[0], "owned")
                self.assertTrue(pipe.poll(5))
                self.assertEqual(pipe.recv()[0], "released")
            key.assert_called_once_with("a", 4)
            self.assertEqual(release.call_args_list[0].kwargs["reason"], "reserve time for held key")

    def test_grouped_section_restores_label_and_releases_inputs_on_failure(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        controller.window = 123
        with patch.object(controller, "require_window"), patch("ac.focus_window", return_value=True), \
             patch("ac.win32gui.GetForegroundWindow", return_value=123), \
             patch.object(controller, "release_inputs") as release:
            with self.assertRaisesRegex(RuntimeError, "failed"):
                with controller.with_focus("outer"):
                    with controller.with_focus("inner"):
                        self.assertEqual(self.focus.section, "inner")
                        raise RuntimeError("failed")
        release.assert_called_once()
        self.assertEqual(self.focus.depth, 0)
        self.assertIsNone(self.focus.section)

    def test_waiting_time_does_not_trigger_progress_recovery(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.focus = self.focus
        self.focus.waiting = True
        with patch("ac.request_interrupt") as restart:
            controller.check_game_progress()
        restart.assert_not_called()
        self.focus.waiting = False


if __name__ == "__main__":
    unittest.main()
