from contextlib import contextmanager
from types import SimpleNamespace
import threading
import time
import unittest
from unittest.mock import Mock, patch

from background_jobs import BackgroundJobPickup
from job_policy import JobPolicy
from job_sender import JobPacketUnavailable, JobPacketUncertain
from player import Player
import pluto


class BackgroundPickupTests(unittest.TestCase):
    def worker(self, count=1, target=2, sender=None):
        controller = SimpleNamespace(instance=SimpleNamespace(pid=1, born='client', proxy_pid=2,
            proxy_born='proxy', valid=lambda: True), job_monitor=Mock(),
            polling_time=time.monotonic, _remote_logoff=threading.Event(),
            _stopping=False, _paused=False, _pause_control=None, _yield=time.sleep)
        controller.job_monitor.mission_snapshot.return_value = None
        worker = BackgroundJobPickup(controller, JobPolicy(frozenset({'uranus'})),
                                     target, count, sender=sender or Mock())
        self.addCleanup(worker.close)
        return worker

    def test_background_facade_has_no_ui_and_logs_start_and_stop(self):
        worker = self.worker()
        def pick():
            facade = picker.call_args.args[0]
            self.assertFalse(hasattr(facade, 'focus'))
            self.assertFalse(hasattr(facade, 'lclick'))
            self.assertEqual(facade.count_mission(), 1)
            picker.call_args.kwargs['on_accept'](2)
            return 2
        with patch('background_jobs.process_identity', return_value='proxy'), \
             patch('background_jobs.PacketJobPicker') as picker, \
             patch('background_jobs.log_event') as log:
            picker.return_value.pick.side_effect = pick
            worker.start()
            self.assertEqual(worker.finish(), 2)
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(any('starting at Earth station: count=1, target=2' in s for s in messages))
        self.assertTrue(any('stopped: count=2, target=2, reason=finished' in s for s in messages))

    def test_acceptance_waits_until_vendor_sale_is_finished(self):
        worker = self.worker()
        ready = threading.Event()
        def pick():
            ready.set()
            picker.call_args.kwargs['before_accept']()
            return 2
        with patch('background_jobs.process_identity', return_value='proxy'), \
             patch('background_jobs.PacketJobPicker') as picker, patch('background_jobs.log_event'):
            picker.return_value.pick.side_effect = pick
            worker.start()
            self.assertTrue(ready.wait(1))
            self.assertFalse(worker.done.wait(.03))
            worker.allow_accepts()
            self.assertEqual(worker.finish(), 2)

    def test_vendor_excludes_packet_submissions(self):
        worker = self.worker()
        started = threading.Event()
        def submit():
            started.set()
            worker.send(worker.instance, 7, 123)
        with patch('background_jobs.process_identity', return_value='proxy'):
            with worker.vendor():
                thread = threading.Thread(target=submit)
                thread.start()
                self.assertTrue(started.wait(1))
                worker.sender.assert_not_called()
            thread.join(1)
            self.assertFalse(thread.is_alive())
        worker.sender.assert_called_once()

    def test_relogin_cannot_redirect_a_worker_to_another_proxy(self):
        worker = self.worker()
        worker.controller.instance.proxy_pid = 99
        with patch('background_jobs.process_identity', return_value='proxy'):
            with self.assertRaises(JobPacketUnavailable):
                worker.send(worker.instance, 8, 123)
        worker.sender.assert_not_called()

    def test_cancellation_joins_waiting_worker_and_logs_stop(self):
        worker = self.worker()
        ready = threading.Event()
        def pick():
            ready.set()
            picker.call_args.kwargs['before_accept']()
        with patch('background_jobs.process_identity', return_value='proxy'), \
             patch('background_jobs.PacketJobPicker') as picker, patch('background_jobs.log_event') as log:
            picker.return_value.pick.side_effect = pick
            worker.start()
            self.assertTrue(ready.wait(1))
            worker.close()
            self.assertFalse(worker.thread.is_alive())
        self.assertTrue(any('reason=Background job pickup cancelled' in call.args[0]
                            for call in log.call_args_list))

    def test_uncertain_acceptance_is_propagated_before_undocking(self):
        worker = self.worker()
        with patch('background_jobs.PacketJobPicker') as picker, patch('background_jobs.log_event'):
            picker.return_value.pick.side_effect = JobPacketUncertain('missing reply')
            worker.start()
            with self.assertRaises(JobPacketUncertain):
                worker.finish()

    def test_real_picker_on_worker_confirms_reply_without_ui_or_route_mutation(self):
        worker = self.worker()
        value = dict(owner=(2, 'proxy'), remote='server:1', generation=0,
            opened_at=0, list_seen=0, closed=True, jobs={}, descriptions={}, description_errors={}, accepted={},
            removed=set(), limit_at=0, rejected_at=0, rejection='', error='')
        worker.monitor.job_snapshot.side_effect = lambda: dict(value)
        calls = []
        def send(instance, action, target=0):
            calls.append((action, target, threading.get_ident()))
            now = time.time()
            if action == 6:
                value.update(generation=1, opened_at=now, list_seen=now, closed=False,
                    jobs={123:dict(id=123, type=2, title='Launch a Satellite', level=50)})
            elif action == 7:
                value['descriptions'][123] = dict(available=True, title='Launch a Satellite',
                    description='Launch a satellite: -Puck- in Uranus using the control.', seen=now)
            elif action == 8:
                value['accepted'][123] = now
            elif action == 9:
                value['closed'] = True
        worker.sender = send
        with patch('background_jobs.process_identity', return_value='proxy'), \
             patch('background_jobs.log_event'), patch('packet_jobs.log_event'), \
             patch('packet_jobs.PLAYER') as player:
            worker.allow_accepts()
            worker.start()
            self.assertEqual(worker.finish(), 2)
            player.set_pos.assert_not_called()
        self.assertEqual([(action, target) for action, target, thread in calls],
                         [(6, 0), (7, 123), (8, 123), (9, 0)])
        self.assertTrue(all(thread == worker.thread.ident for action, target, thread in calls))

    def test_paused_worker_can_be_cancelled_without_touching_main_thread_focus(self):
        worker = self.worker()
        worker.controller._paused = True
        ready = threading.Event()
        def pick():
            ready.set()
            worker.checkpoint()
        with patch('background_jobs.process_identity', return_value='proxy'), \
             patch('background_jobs.PacketJobPicker') as picker, patch('background_jobs.log_event'):
            picker.return_value.pick.side_effect = pick
            worker.start()
            self.assertTrue(ready.wait(1))
            self.assertFalse(worker.done.wait(.03))
            worker.close()
            self.assertFalse(worker.thread.is_alive())
        worker.sender.assert_not_called()


class BackgroundRouteTests(unittest.TestCase):
    def test_packet_mode_overlaps_station_walk_and_skips_job_room(self):
        runner = pluto.PlutoRunner()
        runner.player_settings = SimpleNamespace(job_pickup='packet')
        runner.controller = Mock()
        runner.controller.count_mission.return_value = 2
        player = Player()
        player.type_, player.mission_count = 'pw', 6
        trace = []
        @contextmanager
        def vendor():
            trace.append('vendor_lock')
            yield
            trace.append('vendor_unlock')
        with patch('pluto.PLAYER', player), patch('pluto.BackgroundJobPickup') as factory:
            worker = factory.return_value
            worker.start.side_effect = lambda: trace.append('start')
            worker.vendor.side_effect = vendor
            worker.allow_accepts.side_effect = lambda: trace.append('allow_accepts')
            worker.finish.side_effect = lambda: trace.append('finish')
            worker.close.side_effect = lambda: trace.append('close')
            runner.controller.earth1_2.side_effect = lambda: trace.append('walk')
            runner.controller.earth3_vendor.side_effect = lambda: trace.append('sell')
            runner.controller.earth1_space.side_effect = lambda: trace.append('undock')
            runner.all_earth()
        self.assertEqual(factory.call_args.args[2:], (6, 2))
        self.assertEqual(trace, ['start', 'walk', 'vendor_lock', 'sell', 'vendor_unlock',
                                 'allow_accepts', 'finish', 'undock', 'close'])
        runner.controller.earth2_j.assert_not_called()
        runner.controller.earthj_2.assert_not_called()

    def test_worker_failure_blocks_undocking_and_closes_worker(self):
        runner = pluto.PlutoRunner()
        runner.player_settings = SimpleNamespace(job_pickup='packet')
        runner.controller = Mock()
        player = Player()
        player.type_, player.mission_count = 'pw', 6
        with patch('pluto.PLAYER', player), patch('pluto.BackgroundJobPickup') as factory:
            factory.return_value.vendor.return_value.__enter__.return_value = None
            factory.return_value.finish.side_effect = JobPacketUncertain('missing reply')
            with self.assertRaises(JobPacketUncertain):
                runner.all_earth()
            factory.return_value.close.assert_called_once()
        runner.controller.earth1_space.assert_not_called()
