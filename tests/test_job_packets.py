import json
from pathlib import Path
import struct
import unittest

from protocol.job_packets import build_request, decode_list, decode_description, decode_request, low_job_matches


FIXTURES = json.loads((Path(__file__).parent / 'fixtures/job_packets.json').read_text())


class JobPacketTests(unittest.TestCase):
    def test_live_list_contains_structured_job_ids_types_levels_and_text(self):
        jobs = decode_list(bytes.fromhex(FIXTURES['list']))
        self.assertEqual(len(jobs), 5)
        self.assertEqual(jobs[0], dict(id=94769, type=2, unknown=0, level=50,
            title='Launch a Satellite', sponsor='Hyperia Explorers', reward='2000 XP, 2000 cr.'))

    def test_live_description_identifies_puck_and_rejects_other_planets(self):
        puck = decode_description(bytes.fromhex(FIXTURES['puck']))
        self.assertEqual(puck['id'], 94769)
        self.assertTrue(low_job_matches(puck))
        self.assertFalse(low_job_matches(decode_description(bytes.fromhex(FIXTURES['thebe']))))
        puck['available'] = False
        self.assertFalse(low_job_matches(puck))

    def test_ariel_requires_the_correct_title_and_uranus(self):
        description = dict(available=True, title='Dispose Galactic Garbage',
                           description='Dispose Garbage: -Ariel- in Uranus using the control.')
        self.assertTrue(low_job_matches(description))
        description['title'] = 'Transfer a Passenger'
        self.assertFalse(low_job_matches(description))

    def test_constructed_accept_frame_matches_actual_client_request(self):
        udp = bytes.fromhex(FIXTURES['accept_udp'])
        request = decode_request(udp[12:])
        self.assertEqual(request, dict(player=246276, target=94769, action=8))
        expected = struct.pack('<HH', len(udp) - 8, 0x004e) + udp[12:]
        self.assertEqual(build_request(request['player'], request['target'], 8), expected)

    def test_rejects_truncated_trailing_and_malformed_packets(self):
        body = bytes.fromhex(FIXTURES['list'])
        for value in (body[:-1], body + b'\0', b'\xff' * 4):
            with self.assertRaises(ValueError):
                decode_list(value)
        with self.assertRaises(ValueError):
            decode_description(bytes.fromhex(FIXTURES['puck'])[:-1])
        for player, target, action in ((0, 1, 8), (123, 0, 8), (123, 1, 1)):
            with self.assertRaises(ValueError):
                build_request(player, target, action)
