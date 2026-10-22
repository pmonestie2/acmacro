import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ac
import chat_audit
from chat_reply import ChatDecision, parse_decision
import pluto
from player import PlayerSettings
import watchdog


class ChatActionTests(unittest.TestCase):
    def test_stop_reply_transmits_before_sender_peer_and_local_game_close(self):
        controller = self.controller()
        controller.focus = Mock(depth=0, waiting=False)
        controller.focus.broadcast_logoff.return_value = [("Macboyje", None)]
        controller._chat_stop = (("Macboytt", "so u doing it?"),
                                 ChatDecision("STOP", "I'm logging off now."))
        events = Mock()
        events.attach_mock(controller.say, "say")
        events.attach_mock(controller.focus.broadcast_logoff, "broadcast")
        events.attach_mock(controller.killenb, "close")
        with patch("ac.stop_chat"), patch("ac.chat_ready", return_value=True), \
             patch("ac.reply_sent"), patch("ac.log_interaction"), \
             patch("ac.find_window_exact", return_value=None), patch("ac.time.sleep") as settle:
            events.attach_mock(settle, "settle")
            with self.assertRaises(watchdog.StopMacro):
                controller._stop_for_chat()
        self.assertEqual([c[0] for c in events.mock_calls],
                         ["say", "settle", "broadcast", "close"])
        settle.assert_called_once_with(1.0)

    def test_stop_reply_failure_still_broadcasts_and_closes_without_waiting(self):
        controller = self.controller()
        controller.focus = Mock(depth=0, waiting=False)
        controller.focus.broadcast_logoff.return_value = [("Peer", None)]
        controller.say.side_effect = RuntimeError("send failed")
        controller._chat_stop = (("Macboytt", "stop"), ChatDecision("STOP", "ok"))
        with patch("ac.stop_chat"), patch("ac.chat_ready", return_value=True), \
             patch("ac.log_interaction") as audit, patch("ac.find_window_exact", return_value=None), \
             patch("ac.time.sleep") as settle:
            with self.assertRaises(watchdog.StopMacro):
                controller._stop_for_chat()
        settle.assert_not_called()
        controller.focus.broadcast_logoff.assert_called_once()
        controller.killenb.assert_called_once()
        self.assertFalse(audit.call_args.kwargs["response_sent"])

    def test_gm_stop_broadcasts_before_local_close_even_if_delivery_fails(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                controller = self.controller()
                controller.focus = Mock(depth=0, waiting=False)
                controller.focus.broadcast_logoff.return_value = [("Peer", None)]
                if fail:
                    controller.focus.broadcast_logoff.side_effect = OSError("offline")
                controller._logging_in = True
                controller._chat_stop = (("GMTest", "stop"), ChatDecision("STOP", ""))
                events = Mock()
                events.attach_mock(controller.focus.broadcast_logoff, "broadcast")
                events.attach_mock(controller.killenb, "close")
                with patch("ac.stop_chat"), patch("ac.log_interaction"), patch("ac.find_window_exact", return_value=None):
                    with self.assertRaises(watchdog.StopMacro):
                        controller._stop_for_chat()
                self.assertEqual([c[0] for c in events.mock_calls], ["broadcast", "close"])

    def controller(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.player = ac.Player()
        controller.player.toon_name = "TestPlayer"
        controller.schedule_event = threading.Event()
        controller.bring_to_front = Mock(return_value=True)
        controller.say = Mock()
        controller.killenb = Mock()
        return controller

    def test_parse_actions_and_never_treat_response_text_as_action(self):
        self.assertEqual(parse_decision('{"action":"STOP","response":"Understood."}', "GMTest"),
                         ChatDecision("STOP", "Understood."))
        self.assertEqual(parse_decision('{"action":"OK","response":"STOP"}', "Alice").action, "OK")
        for invalid in ('STOP', '["STOP", "ok"]', '{"action":"RESTART","response":"ok"}',
                        '{"action":"STOP"}', '{"action":true,"response":"ok"}'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_decision(invalid, "Alice")
        for response in ("", "/quit", "\x00"):
            self.assertEqual(parse_decision({"action": "STOP", "response": response}, "Alice"),
                             ChatDecision("STOP", ""))

    def test_stop_sends_only_response_then_closes_game_and_records_action(self):
        controller = self.controller()
        request, decision = ("GMTest", "Please stop now"), ChatDecision("STOP", "Understood.")
        events = Mock()
        events.attach_mock(controller.say, "say")
        events.attach_mock(controller.killenb, "close")
        with patch("ac.read_chat_internal"), patch("ac.next_reply", return_value=request), \
             patch("ac.reply_decision", return_value=decision), patch("ac.chat_ready", return_value=True), \
             patch("ac.request_interrupt") as enqueue, patch("ac.stop_chat") as stop_chat, \
             patch("ac.reply_sent") as acknowledge, patch("ac.log_interaction") as audit, \
             patch("ac.find_window_exact", return_value=None):
            controller.check_chat_response()
            controller.check_chat_response()
            enqueue.assert_called_once()
            with self.assertRaises(watchdog.StopMacro):
                enqueue.call_args.args[0]()
        self.assertEqual([call[0] for call in events.mock_calls], ["say", "close"])
        controller.say.assert_called_once_with("/t GMTest Understood.")
        self.assertTrue(controller._terminated)
        self.assertTrue(controller.schedule_event.is_set())
        stop_chat.assert_called_once()
        acknowledge.assert_called_once_with(request, "Understood.")
        audit.assert_called_once_with("TestPlayer", request, decision, response_sent=True, game_closed=True, error=None)

    def test_stop_still_closes_on_focus_send_and_log_failures(self):
        for failure in ("focus", "send", "log"):
            with self.subTest(failure=failure):
                controller = self.controller()
                controller._chat_stop = (("GMTest", "stop"), ChatDecision("STOP", "ok"))
                if failure == "focus":
                    controller.bring_to_front.return_value = False
                if failure == "send":
                    controller.say.side_effect = RuntimeError("send failed")
                with patch("ac.stop_chat"), patch("ac.chat_ready", return_value=True), \
                     patch("ac.reply_sent"), patch("ac.find_window_exact", return_value=None), \
                     patch("ac.log_interaction", side_effect=OSError("disk full") if failure == "log" else None) as audit:
                    with self.assertRaises(watchdog.StopMacro):
                        controller._stop_for_chat()
                controller.killenb.assert_called_once()
                self.assertEqual(audit.call_args.kwargs["response_sent"], failure == "log")
                self.assertTrue(controller._terminated)

    def test_stop_during_running_login_interrupt_is_not_delayed_until_login_finishes(self):
        controller = self.controller()
        controller._logging_in = True
        controller._terminated = True
        decision = ChatDecision("STOP", "ok")
        with patch("ac.read_chat_internal"), patch("ac.next_reply", return_value=("GMTest", "stop")), \
             patch("ac.reply_decision", return_value=decision), patch("ac.chat_ready", return_value=False), \
             patch("ac.request_interrupt"), patch("ac.stop_chat"), patch("ac.reply_sent"), \
             patch("ac.log_interaction") as audit, patch("ac.find_window_exact", return_value=None), \
             patch("ac.time.sleep"), patch.object(watchdog, "_processing", True):
            controller.check_chat_response()
            with self.assertRaises(watchdog.StopMacro):
                controller._yield(0.1)
        controller.say.assert_not_called()
        controller.killenb.assert_called_once()
        self.assertFalse(audit.call_args.kwargs["response_sent"])

    def test_audit_appends_readable_interactions_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "interactions.log"
            with patch.object(chat_audit, "CHAT_INTERACTION_LOG", path):
                chat_audit.log_interaction("TestPlayer", ("Alice", "hello\nagain"),
                                          ChatDecision("OK", "hi"), response_sent=True)
                chat_audit.log_interaction("TestPlayer", ("GMTest", "stop"),
                                          ChatDecision("STOP", "ok"), response_sent=False, game_closed=True)
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        first, second = map(json.loads, lines)
        self.assertEqual(first["message"], "hello\nagain")
        self.assertEqual(first["response"], "hi")
        self.assertEqual(first["action"], "OK")
        self.assertEqual(second["action"], "STOP")
        self.assertTrue(second["game_closed"])
        self.assertIn("timestamp", first)

    def test_stop_escapes_route_loop_and_cleans_up_all_timers(self):
        settings = PlayerSettings("TestPlayer", "je", 0, "test", 6, False, False, "TestAccount")
        runner = pluto.PlutoRunner(settings)
        controller, timer = Mock(window=123), Mock()
        with patch("pluto.require_admin"), patch("pluto.reset_chat"), patch("pluto.stop_chat") as stop_chat, \
             patch("pluto.AcController", return_value=controller) as factory, \
             patch("pluto.run_every", return_value=timer), patch.object(runner, "find_dir"), \
             patch.object(pluto.PLAYER, "set_pos"), patch.object(runner, "route", side_effect=watchdog.StopMacro("STOP")):
            with self.assertRaises(watchdog.StopMacro):
                runner.run()
        factory.assert_called_once()
        controller.terminate.assert_called_once()
        timer.set.assert_called_once()
        stop_chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
