import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_reply
import chatproc
import ac


def ok(text):
    return chat_reply.ChatDecision("OK", text)


class ReplyTests(unittest.TestCase):
    def test_gm_badge_reaches_generator_and_survives_in_history(self):
        generate = Mock(return_value=ok('Hello'))
        responder = chat_reply.ReplyGenerator(generate)
        request = chat_reply.PrivateMessage('JeffTS', 'Hi', is_gm=True)
        self.ready(responder, request)
        self.assertEqual(generate.call_args.kwargs, {'is_gm': True})
        responder.sent(request, 'Hello')
        self.assertTrue(responder._history['jeffts'][0]['sender_is_gm'])

    def ready(self, responder, request):
        self.assertIsNone(responder.next_decision(request))
        responder._worker.join(2)
        self.assertFalse(responder._worker.is_alive())
        return responder.next_decision(request)

    def test_history_is_per_sender_case_insensitive_and_only_committed_after_send(self):
        generate = Mock(return_value=ok("Hello"))
        responder = chat_reply.ReplyGenerator(generate)
        alice = ("Alice", "remember my name")
        self.assertEqual(self.ready(responder, alice), ok("Hello"))
        self.assertEqual(responder.next_decision(alice), ok("Hello"))
        generate.assert_called_once()
        self.assertEqual(responder._history, {})
        responder.sent(alice, "Hello")
        bob = ("Bob", "who am I?")
        self.ready(responder, bob)
        self.assertEqual(generate.call_args.args[2], [])
        responder.sent(bob, "Hello")
        self.ready(responder, ("ALICE", "what did I say?"))
        self.assertEqual(generate.call_args.args[2], [
            {"role": "user", "content": "remember my name"},
            {"role": "assistant", "content": "Hello"},
        ])

    def test_history_keeps_last_ten_exchanges(self):
        generate = Mock(return_value=ok("ok"))
        responder = chat_reply.ReplyGenerator(generate)
        for index in range(12):
            request = ("Alice", str(index))
            responder.sent(request, f"reply {index}")
        self.ready(responder, ("Alice", "next"))
        history = generate.call_args.args[2]
        self.assertEqual(len(history), 20)
        self.assertEqual(history[0]["content"], "2")
        self.assertEqual(history[-1]["content"], "reply 11")

    def test_background_work_never_blocks_polling_and_close_discards_result(self):
        entered = threading.Event()
        def generate(*args):
            entered.set()
            args[-1].wait(2)
            return ok("Hello")
        responder = chat_reply.ReplyGenerator(generate)
        request = ("Alice", "hi")
        try:
            self.assertIsNone(responder.next_decision(request))
            self.assertTrue(entered.wait(1))
            self.assertIsNone(responder.next_decision(request))
            responder.close()
        finally:
            responder.close()
            responder._worker.join(2)
        self.assertIsNone(responder.next_decision(request))
        self.assertIsNone(responder._decision)

    def test_any_generation_failure_returns_stop_without_retry(self):
        for error in (TimeoutError('timeout'), FileNotFoundError('codex missing'),
                      RuntimeError('Codex exited with code 1'), OSError('offline'),
                      ValueError('invalid reply')):
            with self.subTest(error=type(error).__name__):
                generate = Mock(side_effect=error)
                responder = chat_reply.ReplyGenerator(generate)
                request = ('Alice', 'hi')
                with patch('chat_reply.log_event') as log:
                    self.assertEqual(self.ready(responder, request), chat_reply.ChatDecision('STOP', ''))
                    self.assertEqual(responder.next_decision(request), chat_reply.ChatDecision('STOP', ''))
                generate.assert_called_once()
                self.assertEqual(responder._history, {})
                self.assertIn('action=STOP', log.call_args.args[0])

    def test_malformed_generated_result_returns_stop(self):
        responder = chat_reply.ReplyGenerator(Mock(return_value='not json'))
        with patch('chat_reply.log_event'):
            self.assertEqual(self.ready(responder, ('Alice', 'hi')), chat_reply.ChatDecision('STOP', ''))

    def test_failure_to_start_generation_thread_returns_stop(self):
        responder = chat_reply.ReplyGenerator(Mock())
        with patch('chat_reply.threading.Thread.start', side_effect=RuntimeError('cannot start thread')), \
             patch('chat_reply.log_event'):
            self.assertEqual(responder.next_decision(('Alice', 'hi')), chat_reply.ChatDecision('STOP', ''))
        responder.close()

    def test_identical_messages_get_independent_replies(self):
        generate = Mock(side_effect=[ok("one"), ok("two")])
        responder = chat_reply.ReplyGenerator(generate)
        first, second = tuple(["Alice", "hi"]), tuple(["Alice", "hi"])
        self.assertEqual(self.ready(responder, first), ok("one"))
        responder.sent(first, "one")
        self.assertEqual(self.ready(responder, second), ok("two"))

    def test_sanitizes_control_characters_and_bounds_game_command(self):
        self.assertEqual(chat_reply.clean_reply("Hi\nthere\tfriend\x00!", "Alice"), "Hi there friend!")
        self.assertEqual(chat_reply.clean_reply("I’m here", "Alice"), "I'm here")
        self.assertLessEqual(len(chat_reply.clean_reply("x" * 400, "Alice")), 180)
        for text in ("", "\t\n", "/quit", "\x00/quit"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                chat_reply.clean_reply(text, "Alice")

    def test_reloads_prompt_and_passes_chat_as_stdin_without_shell(self):
        prompts, commands = [], []
        def start(command, **kwargs):
            commands.append((command, kwargs))
            process = Mock(returncode=0)
            process.__enter__ = Mock(return_value=process)
            process.__exit__ = Mock(return_value=False)
            process.poll.return_value = 0
            def communicate(input=None, timeout=None):
                if input is not None:
                    prompts.append(input)
                    Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps({"action": "OK", "response": "Hello"}), encoding="utf-8")
                return None, None
            process.communicate.side_effect = communicate
            return process
        with tempfile.TemporaryDirectory() as directory:
            prompt_file = Path(directory) / "prompt.txt"
            with patch.object(chat_reply, "CHAT_PROMPT_PATH", prompt_file), \
                 patch("chat_reply.codex_executable", return_value="codex.exe"), \
                 patch("chat_reply.subprocess.Popen", side_effect=start):
                for instructions in ("Use pirate speech.", "Use plain English."):
                    prompt_file.write_text(instructions, encoding="utf-8")
                    self.assertEqual(chat_reply.generate_reply("Alice", 'hi $(echo test) "', [], threading.Event()), ok("Hello"))
        self.assertIn("Use pirate speech.", prompts[0])
        self.assertIn("Use plain English.", prompts[1])
        self.assertNotIn("Use pirate speech.", prompts[1])
        self.assertEqual(json.loads(prompts[0].split("Conversation JSON:\n")[1])["current_message"], 'hi $(echo test) "')
        for command, kwargs in commands:
            self.assertNotIn("shell", kwargs)
            self.assertEqual(command[-1], "-")
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertIn("--ignore-user-config", command)
            for feature in ("shell_tool", "apps", "plugins", "multi_agent"):
                self.assertIn(feature, command)

    def test_cancelled_process_is_killed(self):
        cancelled = threading.Event()
        process = Mock()
        process.__enter__ = Mock(return_value=process)
        process.__exit__ = Mock(return_value=False)
        process.poll.return_value = None
        def communicate(input=None, timeout=None):
            if timeout is not None:
                cancelled.set()
                raise subprocess.TimeoutExpired("codex", timeout)
        process.communicate.side_effect = communicate
        with patch("chat_reply.codex_executable", return_value="codex.exe"), \
             patch("chat_reply.subprocess.Popen", return_value=process):
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                chat_reply.generate_reply("Alice", "hi", [], cancelled)
        process.kill.assert_called_once()

    def test_queue_and_history_survive_relogin_and_reset_on_new_run(self):
        with patch("chatproc.require_admin"), patch.object(Path, "exists", return_value=False):
            chatproc.reset_chat("Lightje")
        responder = chat_reply.ReplyGenerator(Mock(return_value=ok("Hello")))
        with patch.object(chatproc, "_responder", responder):
            request = ("Alice", "hello")
            chatproc._pending.append(request)
            self.assertIsNone(chatproc.reply_decision(request))
            responder._worker.join(2)
            chatproc.suspend_chat()
            chatproc.begin_chat()
            self.assertIs(chatproc.next_reply(), request)
            self.assertEqual(chatproc.reply_decision(request), ok("Hello"))
            chatproc.reply_sent(request, "Hello")
            self.assertIn("alice", responder._history)
            with patch("chatproc.require_admin"), patch.object(Path, "exists", return_value=False):
                chatproc.reset_chat("Lightje")
            self.assertTrue(responder._cancelled.is_set())
            self.assertEqual(chatproc._responder._history, {})
            self.assertIsNone(chatproc.next_reply())

    def test_controller_waits_for_generation_without_focusing_game(self):
        with patch.object(ac.AcController, "init_window"):
            controller = ac.AcController("test")
        controller.bring_to_front = Mock()
        with patch("ac.read_chat_internal"), patch("ac.chat_ready", return_value=True), \
             patch("ac.next_reply", return_value=("Alice", "hi")), \
             patch("ac.reply_decision", return_value=None), patch("ac.request_interrupt") as enqueue:
            controller.check_chat_response()
        enqueue.assert_not_called()
        controller.bring_to_front.assert_not_called()
        self.assertFalse(controller._reply_pending)


if __name__ == "__main__":
    unittest.main()
