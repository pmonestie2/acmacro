import copy
import json
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from job_policy import JobPolicy
from job_sender import JobPacketUnavailable, JobPacketUncertain
from packet_jobs import PacketJobPicker
from protocol.job_state import JobState
from protocol.job_packets import build_request
from tests.test_protocol_tracker import wrap
from pluto import PlutoRunner
from watchdog import StopMacro


def policy(destinations='auto', low=True, avoid=False, minimum=0, maximum=0):
    return JobPolicy.from_settings(SimpleNamespace(job_destinations=destinations,
        job_min_level=minimum, job_max_level=maximum), low, avoid)


class PickerSelectionTests(unittest.TestCase):
    def runner(self):
        runner = PlutoRunner()
        runner.player_settings = SimpleNamespace(job_pickup='packet', job_destinations='auto')
        runner.controller = Mock()
        runner._pick_job_ui = Mock(return_value=6)
        return runner

    def test_unavailable_packet_path_stops_without_ui_pickup(self):
        runner = self.runner()
        with patch('pluto.PacketJobPicker') as picker:
            picker.return_value.pick.side_effect = JobPacketUnavailable('no capture')
            with self.assertRaises(StopMacro):
                runner.pick_job()
        runner._pick_job_ui.assert_not_called()

    def test_uncertain_accept_never_falls_back(self):
        runner = self.runner()
        with patch('pluto.PacketJobPicker') as picker:
            picker.return_value.pick.side_effect = JobPacketUncertain('missing acceptance')
            with self.assertRaises(JobPacketUncertain):
                runner.pick_job()
        runner._pick_job_ui.assert_not_called()

    def test_explicit_ui_mode_does_not_create_packet_picker(self):
        runner = self.runner()
        runner.player_settings.job_pickup = 'ui'
        with patch('pluto.PacketJobPicker') as picker:
            self.assertEqual(runner.pick_job(), 6)
        picker.assert_not_called()


def description(target, sector='Pluto and Charon', title='Launch a Satellite'):
    return dict(available=True, title=title,
                description=f'Launch a satellite: -{target}- in {sector} using the control.')


class PolicyTests(unittest.TestCase):
    def test_default_modes_and_pit_exclusion(self):
        self.assertEqual(policy().destinations, {'uranus'})
        self.assertEqual(policy(low=False).destinations, {'charon', 'monster_pit'})
        self.assertEqual(policy(low=False, avoid=True).destinations, {'charon'})
        self.assertTrue(policy('monster_pit').matches(description('Charon Monster Pit')))
        self.assertFalse(policy('charon', avoid=True).matches(description('Charon Monster Pit')))
        self.assertTrue(policy('charon').matches(description('Charon')))
        self.assertTrue(policy('uranus').matches(description('Puck', 'Uranus')))
        self.assertFalse(policy('charon').matches(description('Puck', 'Uranus')))
        with self.assertRaises(ValueError):
            policy('monster_pit', avoid=True)

    def test_level_and_type_filter_does_not_depend_on_ui_order(self):
        selected = policy('uranus', minimum=50, maximum=75)
        job = dict(type=2, title='Launch a Satellite', level=50)
        self.assertTrue(selected.candidate(job))
        self.assertFalse(selected.candidate(dict(job, level=100)))
        self.assertFalse(selected.candidate(dict(job, type=1)))


class JobStateTests(unittest.TestCase):
    def test_bad_description_is_local_to_job_and_next_good_reply_is_usable(self):
        state = JobState()
        state.observe(wrap(0x94, struct.pack('<IB', 123, 2) + b'bad\0body\0'), False, 100)
        value = state.snapshot()
        self.assertEqual(value['error'], '')
        self.assertEqual(value['description_errors'][123]['availability'], 2)
        self.assertEqual(value['description_errors'][123]['reason'], 'Invalid job availability')
        self.assertNotIn(123, value['descriptions'])
        valid = struct.pack('<IB', 456, 1) + b'Launch a Satellite\0Launch a satellite: -Puck- in Uranus using the control.\0'
        state.observe(wrap(0x94, valid), False, 101)
        self.assertTrue(state.snapshot()['descriptions'][456]['available'])
        state.observe(wrap(0x94, valid.replace(struct.pack('<I', 456), struct.pack('<I', 123), 1)), False, 102)
        self.assertNotIn(123, state.snapshot()['description_errors'])

    def test_bad_acceptance_reply_remains_fatal(self):
        state = JobState()
        state.observe(wrap(0x96, b'bad'), False, 100)
        self.assertIn('Unexpected job reply size', state.snapshot()['error'])

    def test_real_list_description_and_accept_reply_are_correlated_by_id(self):
        fixture = json.loads((Path(__file__).parent / 'fixtures/job_packets.json').read_text())
        state = JobState()
        frame = build_request(246276, 0, 6)
        udp = struct.pack('<HHII', len(frame) + 8, 0x4e, 0x4003c204, 1) + frame[4:]
        state.observe(udp, True, 100)
        state.observe(wrap(0x93, bytes.fromhex(fixture['list'])), False, 101)
        state.observe(wrap(0x94, bytes.fromhex(fixture['puck'])), False, 102)
        state.observe(wrap(0x96, struct.pack('<I', 94769)), False, 103)
        state.observe(wrap(0x95, struct.pack('<I', 94769)), False, 103)
        value = state.snapshot()
        self.assertEqual(value['opened_at'], 100)
        self.assertEqual(value['descriptions'][94769]['seen'], 102)
        self.assertEqual(value['accepted'], {94769: 103})
        self.assertNotIn(94769, value['jobs'])
        state.observe(wrap(0x93, bytes.fromhex(fixture['list'])), False, 104)
        self.assertNotIn(94769, state.snapshot()['jobs'])


class PickerTests(unittest.TestCase):
    def setup_picker(self, *, no_reply=False, server_limit=False, lose_session=False, selected=None):
        self.clock = 1000.0
        self.calls = []
        self.count = 1
        self.value = dict(owner=(123, 'born'), remote='server:1', generation=0,
            opened_at=0, list_seen=0, closed=True, jobs={}, descriptions={}, description_errors={}, accepted={},
            removed=set(), limit_at=0, rejected_at=0, rejection='', error='')
        controller = Mock()
        controller.polling_time.side_effect = lambda: self.clock
        controller._yield.side_effect = lambda seconds: setattr(self, 'clock', self.clock + seconds)
        controller.count_mission.side_effect = lambda: self.count
        controller.job_monitor.job_snapshot.side_effect = lambda: copy.deepcopy(self.value)
        def send(instance, action, target=0):
            self.calls.append((action, target))
            if action == 6:
                self.value.update(generation=self.value['generation']+1, opened_at=self.clock,
                    list_seen=self.clock, closed=False, descriptions={}, description_errors={}, accepted={})
                self.value['jobs'] = {1:dict(id=1, type=2, title='Launch a Satellite', level=50),
                                      2:dict(id=2, type=2, title='Launch a Satellite', level=75)}
            elif action == 9:
                self.value['closed'] = True
            elif action == 7:
                target_name, sector = ('Thebe', 'Jupiter') if target == 1 else ('Puck', 'Uranus')
                self.value['descriptions'][target] = dict(description(target_name, sector), id=target, seen=self.clock)
            elif action == 8:
                if lose_session:
                    self.value['remote'] = 'other-server'
                elif server_limit:
                    self.value['limit_at'] = self.clock
                elif not no_reply:
                    self.count += 1
                    self.value['accepted'][target] = self.clock
        return PacketJobPicker(controller, selected or policy(), 2, sender=send)

    def test_filters_wrong_destination_and_counts_only_matching_server_reply(self):
        picker = self.setup_picker()
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.PLAYER'), patch('packet_jobs.log_event'):
            self.assertEqual(picker.pick(), 2)
        self.assertEqual(self.calls, [(6, 0), (7, 1), (7, 2), (8, 2), (9, 0)])

    def test_bad_description_is_logged_and_skipped_then_next_job_is_accepted(self):
        picker = self.setup_picker()
        send = picker.sender
        def malformed_description(instance, action, target=0):
            send(instance, action, target)
            if action == 7 and target == 1:
                self.value['descriptions'].pop(1)
                self.value['description_errors'][1] = dict(seen=self.clock,
                    reason='Invalid job availability', availability=2, body_prefix='0100000002')
        picker.sender = malformed_description
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.PLAYER'), patch('packet_jobs.log_event') as log:
            self.assertEqual(picker.pick(), 2)
        self.assertEqual(self.calls, [(6, 0), (7, 1), (7, 2), (8, 2), (9, 0)])
        self.assertTrue(any('invalid description skipped' in call.args[0]
                            and 'availability=2' in call.args[0] for call in log.call_args_list))

    def test_missing_accept_reply_stops_without_retry_or_ui_fallback(self):
        picker = self.setup_picker(no_reply=True)
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.log_event'):
            with self.assertRaises(JobPacketUncertain):
                picker.pick()
        self.assertEqual([call for call in self.calls if call[0] == 8], [(8, 2)])
        self.assertNotEqual(self.calls[-1], (9, 0))

    def test_fresh_open_generation_survives_receiver_timestamp_skew(self):
        picker = self.setup_picker()
        send = picker.sender
        def skewed_send(instance, action, target=0):
            send(instance, action, target)
            if action == 6:
                # Old receiver stamped at loop start, before socket readiness.
                self.value['opened_at'] -= .1
        picker.sender = skewed_send
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.PLAYER'), patch('packet_jobs.log_event'):
            self.assertEqual(picker.pick(), 2)
        self.assertIn((8, 2), self.calls)

    def test_read_request_retries_only_confirmed_no_send_failure(self):
        picker = self.setup_picker()
        picker.sender = Mock(side_effect=[JobPacketUnavailable('connection changed'), None])
        with patch('packet_jobs.log_event'):
            picker.command(7, 2)
        self.assertEqual(picker.sender.call_count, 2)

    def test_matching_job_streamed_after_twenty_seconds_is_still_picked(self):
        picker = self.setup_picker()
        send = picker.sender
        def streamed_send(instance, action, target=0):
            send(instance, action, target)
            if action == 6:
                self.value['jobs'].pop(2)
        picker.sender = streamed_send
        def advance(seconds):
            self.clock += seconds
            self.value['list_seen'] = self.clock
            if self.clock >= 1021:
                self.value['jobs'][2] = dict(id=2, type=2, title='Launch a Satellite', level=75)
        picker.controller._yield.side_effect = advance
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.PLAYER'), patch('packet_jobs.log_event'):
            self.assertEqual(picker.pick(), 2)
        self.assertEqual([call for call in self.calls if call[0] == 6], [(6, 0)])

    def test_acceptance_no_send_failure_is_not_retried(self):
        picker = self.setup_picker()
        picker.sender = Mock(side_effect=JobPacketUnavailable('connection changed'))
        with self.assertRaises(JobPacketUnavailable):
            picker.command(8, 2)
        self.assertEqual(picker.sender.call_count, 1)

    def test_old_catalog_without_observed_new_open_cannot_be_reused(self):
        picker = self.setup_picker()
        send = picker.sender
        send(None, 6)
        self.calls.clear()
        def missed_open(instance, action, target=0):
            if action == 6:
                self.calls.append((action, target))
            else:
                send(instance, action, target)
        picker.sender = missed_open
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.log_event'):
            self.assertEqual(picker.pick(), 1)
        self.assertTrue(all(action in (6, 9) for action, target in self.calls))
        self.assertEqual(self.calls[-1], (9, 0))
        self.assertGreater(len(self.calls), 2)
        self.assertLessEqual(self.clock, 1450.1)

    def test_overall_budget_expiring_during_open_returns_count_without_false_catalog_error(self):
        picker = self.setup_picker()
        send = picker.sender
        def open_at_deadline(instance, action, target=0):
            send(instance, action, target)
            if action == 6:
                # The native send/last remaining slice consumes the overall
                # budget, but the receiver has already collected five rows.
                self.clock = 1450
        picker.sender = open_at_deadline
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.log_event') as log:
            self.assertEqual(picker.pick(), 1)
        self.assertEqual(self.calls, [(6, 0), (9, 0)])
        messages = [call.args[0] for call in log.call_args_list]
        self.assertTrue(any('reason=pickup time budget exhausted' in message for message in messages))
        self.assertTrue(any('reason=time_budget_exhausted' in message for message in messages))

    def test_catalog_timeout_retries_and_next_catalog_can_complete_pickup(self):
        picker = self.setup_picker()
        send = picker.sender
        opened = 0
        def first_catalog_missing(instance, action, target=0):
            nonlocal opened
            send(instance, action, target)
            if action == 6:
                opened += 1
                if opened == 1:
                    self.value['list_seen'] = 0
                    self.value['jobs'] = {}
        picker.sender = first_catalog_missing
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.PLAYER'), patch('packet_jobs.log_event'):
            self.assertEqual(picker.pick(), 2)
        self.assertEqual(opened, 2)
        self.assertEqual([call for call in self.calls if call[0] == 8], [(8, 2)])

    def test_session_loss_after_accept_is_uncertain_not_safe_fallback(self):
        picker = self.setup_picker(lose_session=True)
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.log_event'):
            with self.assertRaises(JobPacketUncertain):
                picker.pick()

    def test_server_limit_stops_without_inventing_an_accept(self):
        picker = self.setup_picker(server_limit=True)
        with patch('packet_jobs.time.time', side_effect=lambda: self.clock), patch('packet_jobs.log_event'):
            self.assertEqual(picker.pick(), 1)
        self.assertEqual(self.calls[-1], (9, 0))
