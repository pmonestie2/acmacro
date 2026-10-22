from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest

from extract_npc_names import NameObservation, NpcNameIndex, names_in_memory
from protocol.npc_names import NpcFrames, avatar_name, station_names


def name_fields(first: str, last: str) -> bytes:
    return first.encode('cp1252').ljust(20, b'\0') + last.encode('cp1252').ljust(20, b'\0')


def roster() -> bytes:
    avatar = name_fields('Dionysius', 'Kerr') + bytes(201)
    npc = struct.pack('<6i', 1, 3, 50, -1, 0, 0) + avatar
    return struct.pack('<II', 0, 1) + bytes(28) + struct.pack('<II', 0, 1) + npc


def datagram(sequence: int, body: bytes, *, opcode: int = 0x2016, owner: int = 1) -> bytes:
    return struct.pack('<HHII', len(body) + 12, opcode, owner, sequence) + body


class NpcNameTests(unittest.TestCase):
    def test_station_roster_pairs_id_with_both_name_fields(self) -> None:
        item, = station_names(roster())
        self.assertEqual((item.npc_id, item.name, item.room, item.location), (50, 'Dionysius Kerr', 1, 3))

    def test_truncated_or_unsupported_station_layout_is_not_guessed(self) -> None:
        for value in (roster()[:-1], roster() + b'x', bytes(5), struct.pack('<II', 0, 99) + bytes(500)):
            with self.subTest(size=len(value)), self.assertRaises(ValueError):
                station_names(value)

    def test_avatar_name_supports_empty_surname_and_rejects_controls(self) -> None:
        self.assertEqual(avatar_name(name_fields("Dr. B'nzen", "H'neydoo")), "Dr. B'nzen H'neydoo")
        self.assertEqual(avatar_name(name_fields('Warship Genesis', '')), 'Warship Genesis')
        with self.assertRaises(ValueError):
            avatar_name(name_fields('Invalid\nname', 'Surname'))

    def test_fragmented_roster_handles_preceding_and_following_packets(self) -> None:
        body = roster()
        application = struct.pack('<HH', len(body) + 4, 0x52) + body
        prefix = struct.pack('<HHi', 8, 0x56, 3)
        suffix = struct.pack('<HHi', 8, 0x56, 6)
        assembler = NpcFrames()
        self.assertEqual(assembler.feed(datagram(10, prefix + application[:100]), 1), [])
        result = assembler.feed(datagram(11, application[100:] + suffix, opcode=0x201a), 2)
        self.assertEqual([(frame.opcode, frame.body) for frame in result], [(0x52, body)])

    def test_other_owner_and_expired_continuation_cannot_complete_roster(self) -> None:
        body = roster()
        application = struct.pack('<HH', len(body) + 4, 0x52) + body
        assembler = NpcFrames()
        assembler.feed(datagram(10, application[:100]), 1)
        self.assertEqual(assembler.feed(datagram(11, application[100:], opcode=0x201a, owner=2), 2), [])
        self.assertEqual(assembler.feed(datagram(11, application[100:], opcode=0x201a), 20), [])

    def test_recorded_space_npc_has_person_name_and_separate_object_id(self) -> None:
        index = NpcNameIndex()
        index.read_capture(Path(__file__).parent / 'fixtures/npc_space_name.jsonl')
        rows = [row for row in index.observations if row.kind == 'space_npc']
        self.assertTrue(rows)
        self.assertEqual({row.name for row in rows}, {'Anjuren Kahn'})
        self.assertEqual({row.object_id for row in rows}, {100010})
        self.assertTrue(all(row.npc_id is None for row in rows))

    def test_players_are_not_added_as_npcs(self) -> None:
        avatar = struct.pack('<I', 0x40012345) + name_fields('Player', 'Surname') + bytes(216)
        application = struct.pack('<HH', len(avatar) + 4, 0x61) + avatar
        row = dict(time=1.0, proxy=123, born='birth', outgoing=False, payload_hex=datagram(1, application).hex())
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'capture.jsonl'
            path.write_text(json.dumps(row) + '\n')
            index = NpcNameIndex()
            index.read_capture(path)
        self.assertEqual(index.observations, set())

    def test_historical_source_ids_are_kept_separate_from_live_ids(self) -> None:
        index = NpcNameIndex()
        index.add_roster(roster(), 'capture', historical=False)
        index.add_roster(roster(), 'old database', historical=True)
        self.assertEqual({row.kind for row in index.observations}, {'station_npc', 'historical_station_npc'})

    def test_client_name_and_mission_reference_do_not_invent_npc_ids(self) -> None:
        avatar = name_fields('Dionysius', 'Kerr') + bytes(5) + b'\x02' + struct.pack('<III', 1, 2, 0)
        data = avatar + b'Report to Proconsul Dionysius Kerr on Arx Magister (Mars).\0'
        rows = names_in_memory(data, 'client cache')
        self.assertEqual({row.name for row in rows}, {'Dionysius Kerr', 'Proconsul Dionysius Kerr'})
        self.assertTrue(all(row.npc_id is None and row.object_id is None for row in rows))
        reference = next(row for row in rows if row.kind == 'mission_reference')
        self.assertEqual(reference.location, 'Arx Magister (Mars)')

    def test_memory_text_and_unterminated_fields_are_not_avatar_names(self) -> None:
        suffix = bytes(5) + b'\x02' + struct.pack('<III', 1, 2, 0)
        for first, last in ('.0f% seconds when ac', 'tivated.'), ('I cross-adapter flip', 'proxy (iGPU created'):
            self.assertEqual(names_in_memory(name_fields(first, last) + suffix, 'client cache'), set())

    def test_refresh_preserves_prior_client_evidence(self) -> None:
        original = NameObservation('mission_reference', 'Proconsul Dionysius Kerr', 'client cache',
                                   location='Arx Magister (Mars)')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'names.json'
            first = NpcNameIndex()
            first.observations.add(original)
            first.write(path)
            refreshed = NpcNameIndex()
            refreshed.load(path)
            refreshed.add_roster(roster(), 'new capture', historical=False)
            refreshed.write(path)
            saved = json.loads(path.read_text())
        self.assertTrue(any(row['kind'] == 'mission_reference' and row['location'] == 'Arx Magister (Mars)'
                            for row in saved['observations']))
        self.assertTrue(any(row['kind'] == 'station_npc' and row['npc_id'] == 50 for row in saved['observations']))


if __name__ == '__main__':
    unittest.main()
