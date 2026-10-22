import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chatproc


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.reset("")
        self.marker = chatproc.begin_chat()
        self.echo = f"(3):19:31:30 Lightje (private): {self.marker}\n"
        self.message = "(3):19:31:31 Friend (private): hey noob\n"

    def poll(self, text):
        with patch("chatproc.read_chat_log", return_value=text):
            chatproc.read_chat_internal()
        return chatproc.next_reply()

    def reset(self, text):
        with patch("chatproc.require_admin"), patch.object(Path, "exists", return_value=True), patch("chatproc.read_chat_log", return_value=text):
            chatproc.reset_chat("Lightje")

    def test_waits_for_marker_and_ignores_old_and_outgoing_messages(self):
        self.reset(self.message)
        self.marker = chatproc.begin_chat()
        self.echo = f"(3):19:31:30 Lightje (private): {self.marker}\n"
        self.assertIsNone(self.poll(self.message + f"To lightje: {self.marker}\n"))
        self.assertFalse(chatproc.chat_ready())
        self.assertIsNone(self.poll(self.message + f"To lightje: {self.marker}\n" + self.echo + "To Friend: hello\n"))
        self.assertTrue(chatproc.chat_ready())

    def test_message_stays_pending_until_sent_and_is_not_queued_twice(self):
        text = self.echo + self.message
        reply = self.poll(text)
        self.assertEqual(reply, ("Friend", "hey noob"))
        self.assertIs(self.poll(text), reply)
        chatproc.reply_sent(reply, "Hi")
        self.assertIsNone(self.poll(text))

    def test_other_toons_internal_markers_never_become_reply_requests(self):
        markers = ''.join(f'(3):18:50:00 Lightbulb (private): hello_{value}\n'
                          for value in (1791118134694353300, 1791144575652905200))
        self.assertEqual(self.poll(self.echo + markers + self.message), ('Friend', 'hey noob'))
        chatproc.reply_sent(chatproc.next_reply(), 'Hi')
        self.assertIsNone(chatproc.next_reply())

    def test_marker_like_normal_text_and_gm_messages_are_retained(self):
        self.assertEqual(self.poll(self.echo + '(3):18:50:00 Friend (private): hello_123 how are you?\n'),
                         ('Friend', 'hello_123 how are you?'))
        chatproc.reply_sent(chatproc.next_reply(), 'Hi')
        reply = self.poll(self.echo + '(3):18:50:00 JeffTS [GM] (private): hello_123\n')
        self.assertEqual(reply, ('JeffTS', 'hello_123'))
        self.assertTrue(reply.is_gm)

    def test_installation_switch_skips_history_but_keeps_queued_real_pm(self):
        reply = self.poll(self.echo + self.message)
        target = Path('different-installation/chat.log').resolve()
        with patch.object(chatproc, 'CHAT_LOG_PATH', Path('original/chat.log').resolve()), \
                patch.object(Path, 'exists', return_value=True), \
                patch('chatproc.read_chat_log', return_value='(3):18:00:00 OldFriend (private): old history\n'):
            chatproc.configure_chat_path(target)
            self.assertIs(chatproc.next_reply(), reply)
            chatproc.reply_sent(reply, 'Hi')
            self.assertEqual(self.poll('(3):18:00:00 OldFriend (private): old history\n' + self.message),
                             ('Friend', 'hey noob'))

    def test_partial_line_waits_for_completion(self):
        self.assertIsNone(self.poll(self.echo + self.message.rstrip("\n")))
        self.assertEqual(self.poll(self.echo + self.message), ("Friend", "hey noob"))

    def test_gm_badge_is_not_the_reply_recipient(self):
        message = "Hi, do you have a moment to chat?"
        line = f"  (3):18: 9:52  JeffTS [GM] (private): {message}\n"
        reply = self.poll(self.echo + line)
        self.assertEqual(reply, ("JeffTS", message))
        self.assertTrue(reply.is_gm)
        chatproc.reply_sent(reply, "Yes")
        self.assertIsNone(self.poll(self.echo + line))

    def test_gm_tag_is_case_insensitive_and_bare_badge_is_not_a_sender(self):
        line = "(3):18:10:00 JeffTS [gm] (PRIVATE): hello\n"
        self.assertEqual(self.poll(self.echo + line), ("JeffTS", "hello"))
        chatproc.reply_sent(chatproc.next_reply(), "Hi")
        self.assertIsNone(self.poll(self.echo + line + "(3):18:10:01 [GM] (private): invalid\n"))

    def test_first_word_after_padded_time_is_sender_regardless_of_title(self):
        line = "  (3): 5: 8: 2  JeffTS [Other title] (private): hello (private): again\n"
        reply = self.poll(self.echo + line)
        self.assertEqual(reply, ("JeffTS", "hello (private): again"))
        self.assertFalse(reply.is_gm)

    def test_failed_read_retries_without_losing_messages(self):
        self.poll(self.echo)
        with patch("chatproc.read_chat_log", side_effect=OSError("busy")):
            self.assertIsNone(chatproc.read_chat_internal())
        self.assertEqual(self.poll(self.echo + self.message), ("Friend", "hey noob"))

    def test_new_session_ignores_previous_log(self):
        old = self.echo + self.message
        self.reset(old)
        chatproc.begin_chat()
        self.assertIsNone(self.poll(old))
        self.assertFalse(chatproc.chat_ready())

    def test_multiple_senders_and_identical_messages_are_all_retained(self):
        self.poll(self.echo + self.message + self.message + "(3):19:31:32 Other (private): hello\n")
        for sender, message in (("Friend", "hey noob"), ("Friend", "hey noob"), ("Other", "hello")):
            reply = chatproc.next_reply()
            self.assertEqual(reply, (sender, message))
            chatproc.reply_sent(reply, "Hi")
        self.assertIsNone(chatproc.next_reply())

    def test_messages_before_and_during_relogin_survive_new_marker(self):
        self.poll(self.echo + self.message)
        chatproc.suspend_chat()
        self.assertFalse(chatproc.chat_ready())
        text = self.echo + self.message + "(3):19:32:00 Second (private): hi\n"
        self.poll(text)
        marker = chatproc.begin_chat()
        self.poll(text + f"(3):19:32:01 Lightje (private): {marker}\n")
        self.assertTrue(chatproc.chat_ready())
        for sender in ("Friend", "Second"):
            reply = chatproc.next_reply()
            self.assertEqual(reply[0], sender)
            chatproc.reply_sent(reply, "Hi")
        self.assertIsNone(chatproc.next_reply())

    def test_log_replacement_preserves_pending_replies(self):
        self.poll(self.echo + self.message)
        self.poll("(3):19:35:00 Newfriend (private): hi\n")
        chatproc.reply_sent(chatproc.next_reply(), "Hi")
        self.assertEqual(chatproc.next_reply()[0], "Newfriend")

    def test_snapshot_cleanup_on_success_and_read_error(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                snapshot = Mock(DeviceObject=r"\\?\GLOBALROOT\Device\Snapshot")
                service = Mock()
                service.Win32_ShadowCopy.Create.return_value = (0, "our-id")
                service.Win32_ShadowCopy.return_value = [snapshot]
                with patch("chatproc.wmi.WMI", return_value=service), \
                     patch.object(Path, "read_text", side_effect=OSError("read failed") if failure else None,
                                  return_value="chat"):
                    if failure:
                        with self.assertRaises(OSError):
                            chatproc._read_snapshot(Path(r"C:\game\chat.log"))
                    else:
                        self.assertEqual(chatproc._read_snapshot(Path(r"C:\game\chat.log")), "chat")
                snapshot.Delete_.assert_called_once()


if __name__ == "__main__":
    unittest.main()
