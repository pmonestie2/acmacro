import multiprocessing
import threading
import unittest
import uuid
from urllib.error import HTTPError
from unittest.mock import Mock, patch

import ac
from comms import PeerServer, post
from focus import SharedFocus
from watchdog import StopMacro


def receiver(connection, namespace):
    focus = SharedFocus("receiver-game", "Receiver", namespace=namespace)
    received = threading.Event()
    try:
        focus.start_comms(received.set)
        connection.send("ready")
        connection.send(received.wait(10))
        connection.recv()
    finally:
        focus.close()


class CommsTests(unittest.TestCase):
    def test_http_queues_commands_once_and_requests_focus_without_game_input(self):
        logoff, focus = Mock(), Mock()
        server = PeerServer(logoff, focus, lambda: {"ready": True})
        self.addCleanup(server.close)
        payload = {"id": "same-command", "command": "logoff"}
        first = post(server.port, "/message", payload, server.token)
        second = post(server.port, "/message", payload, server.token)
        self.assertEqual(first["status"], "queued")
        self.assertTrue(second["duplicate"])
        logoff.assert_called_once()
        post(server.port, "/focus/request", {"id": "focus-command"}, server.token)
        focus.assert_called_once()
        with self.assertRaises(HTTPError) as error:
            post(server.port, "/message", payload, "wrong-token")
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            post(server.port, "/message", {"id": "bad", "command": "unknown"}, server.token)
        self.assertEqual(error.exception.code, 400)

    def test_two_processes_discover_peer_and_send_http_logoff(self):
        namespace = "Local\\acmacro-comms-test-" + uuid.uuid4().hex
        sender = SharedFocus("sender-game", "Sender", namespace=namespace)
        self.addCleanup(sender.close)
        parent, child = multiprocessing.Pipe()
        process = multiprocessing.get_context("spawn").Process(target=receiver, args=(child, namespace))
        process.start()
        child.close()
        try:
            self.assertTrue(parent.poll(10))
            self.assertEqual(parent.recv(), "ready")
            result = sender.send_logoff("receiver", "test-message")
            self.assertEqual(result["status"], "queued")
            self.assertEqual(sender.broadcast_logoff(), [("Receiver", None)])
            self.assertTrue(parent.poll(3))
            self.assertTrue(parent.recv())
            parent.send("close")
            process.join(5)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            parent.close()
            process.close()

    def test_logoff_waits_for_safe_boundary_and_stops_only_its_instance(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.instance = Mock()
        controller.focus = Mock(depth=1)
        controller._remote_logoff.set()
        controller._check_remote_logoff()
        controller.instance.stop.assert_not_called()
        controller.focus.depth = 0
        with patch.object(controller, "terminate"), patch("ac.stop_chat"):
            with self.assertRaises(StopMacro):
                controller._check_remote_logoff()
        controller.instance.stop.assert_called_once()
        controller.focus.broadcast_logoff.assert_not_called()

    def test_broadcast_continues_after_failed_peer_and_retries_same_id(self):
        focus = SharedFocus("sender", "Sender", namespace="Local\\acmacro-test-" + uuid.uuid4().hex)
        try:
            with focus.state() as state:
                for key, port in (("bad", 1), ("good", 2)):
                    state["members"][key] = {"pid": 123, "born": "fake", "toon": key,
                        "game": True, "http": {"port": port, "token": "test"}}
            with patch.object(focus, "clean"), \
                 patch("comms.post", side_effect=[OSError("offline"), OSError("offline"), {"status": "queued"}]) as send:
                result = focus.broadcast_logoff()
            self.assertEqual(result, [("bad", "offline"), ("good", None)])
            self.assertEqual([c.args[0] for c in send.call_args_list], [1, 1, 2])
            self.assertEqual(send.call_args_list[0].args[2]["id"], send.call_args_list[1].args[2]["id"])
        finally:
            focus.close()

    def test_unavailable_peer_is_reported(self):
        focus = SharedFocus("solo", "Solo", namespace="Local\\acmacro-test-" + uuid.uuid4().hex)
        try:
            with self.assertRaisesRegex(RuntimeError, "No HTTP peer"):
                focus.send_logoff("Missing")
        finally:
            focus.close()


if __name__ == "__main__":
    unittest.main()
