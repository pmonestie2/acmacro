import contextlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import ac
import macro_control as control
from player import Player


class SignalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_all_and_individual_overrides_and_resume_all(self):
        receiver = control.PauseControl('macboyje', self.root)
        self.addCleanup(receiver.close)
        self.assertFalse(receiver.requested())
        control.set_request(True, root=self.root)
        self.assertTrue(receiver.requested())
        control.set_request(False, ['MACBOYJE'], self.root)
        self.assertFalse(receiver.requested())
        control.set_request(True, ['macboypw'], self.root)
        self.assertFalse(receiver.requested())
        control.set_request(False, root=self.root)
        self.assertEqual(control.read_request(self.root)['toons'], {})

    def test_signal_persists_for_a_restarted_macro_and_status_is_acknowledged(self):
        control.set_request(True, root=self.root)
        receiver = control.PauseControl('macboyje', self.root)
        self.addCleanup(receiver.close)
        self.assertTrue(receiver.requested())
        receiver.acknowledge(True)
        self.assertEqual([(item['toon'], item['paused']) for item in control.active_statuses(self.root)], [('macboyje', True)])
        receiver.close()
        self.assertEqual(control.active_statuses(self.root), [])

    def test_corrupt_signal_does_not_resume_a_paused_receiver(self):
        receiver = control.PauseControl('macboyje', self.root)
        self.addCleanup(receiver.close)
        receiver.acknowledge(True)
        (self.root / 'request.json').write_text('partial JSON')
        self.assertTrue(receiver.requested())

    def test_cli_pauses_and_resumes_two_separate_macro_receivers(self):
        code = '''
import sys,time
from pathlib import Path
from macro_control import PauseControl
root=Path(sys.argv[1]); receiver=PauseControl(sys.argv[2],root)
try:
 deadline=time.monotonic()+15
 while time.monotonic()<deadline and not (root/'stop').exists():
  requested=receiver.requested()
  if receiver.paused != requested: receiver.acknowledge(requested)
  time.sleep(.02)
finally: receiver.close()
'''
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.root), toon],
                     cwd=Path(__file__).resolve().parents[1], creationflags=subprocess.CREATE_NO_WINDOW)
                     for toon in ('macboyje', 'macboypw')]
        try:
            deadline = time.monotonic() + 5
            while len(control.active_statuses(self.root)) < 2 and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(len(control.active_statuses(self.root)), 2)
            output = io.StringIO()
            with patch.object(control, 'ROOT', self.root), contextlib.redirect_stdout(output):
                self.assertEqual(control.main(['pause', '--all', '--wait', '3']), 0)
                self.assertTrue(all(item['paused'] for item in control.active_statuses(self.root)))
                self.assertEqual(control.main(['resume', '--all', '--wait', '3']), 0)
                self.assertFalse(any(item['paused'] for item in control.active_statuses(self.root)))
            self.assertIn('macboyje: PAUSED', output.getvalue())
            self.assertIn('macboypw: PAUSED', output.getvalue())
        finally:
            (self.root / 'stop').touch()
            for process in processes:
                process.wait(timeout=5)


class PauseBoundaryTests(unittest.TestCase):
    def controller(self):
        with patch.object(ac.AcController, 'init_window'):
            controller = ac.AcController('test')
        controller.player = Player()
        controller.player.toon_name = 'test'
        controller._pause_control = Mock()
        controller._pause_control.requested.side_effect = [True, True, False]
        return controller

    def test_pause_releases_focus_logs_ack_and_excludes_time_from_watchdog(self):
        controller = self.controller()
        controller.focus = Mock(depth=0, waiting=False)
        controller._check_remote_logoff = Mock()
        before = controller.player.set_time
        with patch('ac.time.monotonic', side_effect=[100, 160]), patch('ac.time.sleep'), patch('ac.log_event') as log:
            controller._check_pause()
        controller.focus.cancel_wait.assert_called_once()
        self.assertEqual([call.args for call in controller._pause_control.acknowledge.call_args_list], [(True,), (False,)])
        self.assertEqual(controller._pause_total, 60)
        self.assertEqual(controller.player.set_time, before + 60)
        self.assertFalse(controller._paused)
        self.assertIn('PAUSED', log.call_args_list[0].args[0])
        self.assertIn('RESUMED', log.call_args_list[-1].args[0])

    def test_explicit_pause_during_focus_wait_still_freezes_progress_deadlines(self):
        controller = self.controller()
        controller.focus = Mock(depth=0, waiting=True)
        before = controller.player.set_time
        with patch('ac.time.monotonic', side_effect=[100, 160]), patch('ac.time.sleep'), patch('ac.log_event'):
            controller._check_pause()
        self.assertEqual(controller._pause_total, 60)
        self.assertEqual(controller.player.set_time, before + 60)

    def test_unfinished_inputs_defer_pause(self):
        controller = self.controller()
        controller.focus = Mock(depth=1)
        controller._check_pause()
        controller._pause_control.requested.assert_not_called()
        controller._pause_control.acknowledge.assert_not_called()

    def test_watchdog_does_not_schedule_relogin_while_paused(self):
        controller = self.controller()
        controller._paused = True
        controller.instance = Mock()
        with patch('ac.request_interrupt') as interrupt:
            controller.check_game_progress()
        interrupt.assert_not_called()
        controller.instance.valid.assert_not_called()

    def test_file_signal_can_pause_and_resume_without_game_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            controller = self.controller()
            receiver = controller._pause_control = control.PauseControl('test', root)
            control.set_request(True, root=root)
            finished = threading.Event()
            def resume():
                try:
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        if json.loads(receiver.path.read_text())['paused']:
                            control.set_request(False, root=root)
                            return
                        time.sleep(.01)
                finally:
                    finished.set()
            worker = threading.Thread(target=resume)
            worker.start()
            try:
                with patch('ac.log_event'), patch.object(controller, 'key_press') as key:
                    controller._check_pause()
                self.assertTrue(finished.wait(1))
                key.assert_not_called()
                self.assertFalse(receiver.paused)
            finally:
                control.set_request(False, root=root)
                worker.join(3)
                receiver.close()
