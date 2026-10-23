"""Synthetic protocol and loopback HTTP tests; never drive a live game."""
import json
import socket
import struct
import threading
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from enb_protocol.capture import IPv4Reassembler
from enb_protocol.connection import GameConnection, RC4, receive_exact
from enb_protocol.wire import Frame, decode_batch, decode_frame, encode_udp
from enb_protocol.world import World, decode_map_object
from enb_web.server import AppState, WebServer


def map_body(name: str = 'Test gate', flags: int = 0x91) -> bytes:
    body = bytearray(60)
    struct.pack_into('<I', body, 0, 123)
    body[4] = 11
    struct.pack_into('<H', body, 5, 48)
    struct.pack_into('<fff', body, 25, 1000, -2000, 3000)
    body[57] = flags
    encoded = name.encode('ascii')
    struct.pack_into('<H', body, 58, len(encoded))
    return bytes(body) + encoded


def fragment(offset: int, payload: bytes, more: bool) -> bytes:
    header = bytearray(20)
    header[0] = 0x45
    header[9] = 17
    struct.pack_into('!HHH', header, 2, 20 + len(payload), 44, offset // 8 | (0x2000 if more else 0))
    return bytes(header) + payload


class SharedProtocolTests(unittest.TestCase):
    def test_batch_round_trip_and_every_truncation(self) -> None:
        data = encode_udp(0x2016, 42, Frame(0x2018, map_body()).encode(), sequence=8)
        batch = decode_batch(data)
        self.assertEqual((batch.player_id, batch.sequence), (42, 8))
        self.assertEqual(batch.frames[0].body, map_body())
        for end in range(len(data)):
            with self.subTest(end=end), self.assertRaises(ValueError):
                decode_batch(data[:end])

    def test_invalid_inner_lengths_rejected(self) -> None:
        for size in (0, 1, 2, 3, 65535):
            with self.subTest(size=size), self.assertRaises(ValueError):
                decode_batch(encode_udp(0x2016, 42, struct.pack('<HH', size, 5)))

    def test_frame_size_limit(self) -> None:
        with self.assertRaises(ValueError):
            Frame(1, bytes(65532)).encode()
        with self.assertRaises(ValueError):
            encode_udp(1, 1, bytes(65500))
        self.assertEqual(decode_frame(Frame(5, b'hello').encode()), Frame(5, b'hello'))

    def test_rc4_known_vector_across_calls(self) -> None:
        cipher = RC4(b'Key')
        self.assertEqual((cipher.transform(b'Plain') + cipher.transform(b'text')).hex(), 'bbf316e8d940af0ad3')

    def test_tcp_receive_spans_short_reads(self) -> None:
        peer = Mock(spec=socket.socket)
        peer.recv.side_effect = [b'a', b'bc', b'd']
        self.assertEqual(receive_exact(peer, 4), b'abcd')

    def test_tcp_eof(self) -> None:
        peer = Mock(spec=socket.socket)
        peer.recv.side_effect = [b'a', b'']
        with self.assertRaises(EOFError):
            receive_exact(peer, 4)

    def test_tcp_cipher_state_spans_header_and_body(self) -> None:
        wire = RC4(b'test').transform(Frame(0x0001, bytes(4)).encode())
        peer = Mock(spec=socket.socket)
        peer.recv.side_effect = [wire[:2], wire[2:4], wire[4:5], wire[5:]]
        self.assertEqual(GameConnection(peer, b'test').receive(), Frame(1, bytes(4)))

    def test_connect_closes_socket_after_invalid_public_key(self) -> None:
        peer = Mock(spec=socket.socket)
        peer.recv.return_value = struct.pack('!I', 257)
        with patch('enb_protocol.connection.socket.create_connection', return_value=peer):
            with self.assertRaises(ValueError):
                GameConnection.connect('unit.test')
        peer.close.assert_called_once()

    def test_map_layout_and_flags(self) -> None:
        item = decode_map_object(map_body())
        self.assertEqual((item.object_id, item.name, item.kind), (123, 'Test gate', 'gate'))
        self.assertEqual(item.position, (1000, -2000, 3000))
        self.assertTrue(item.visited)
        self.assertTrue(item.navigation_present)
        self.assertEqual(item.asset_id, 48)

    def test_map_rejects_truncated_name_and_nan(self) -> None:
        with self.assertRaises(ValueError):
            decode_map_object(map_body()[:-1])
        body = bytearray(map_body())
        struct.pack_into('<f', body, 25, float('nan'))
        with self.assertRaises(ValueError):
            decode_map_object(body)

    def test_world_excludes_non_navigation_and_resets_on_sector_change(self) -> None:
        world = World()
        world.apply_frame(Frame(0x2018, map_body(flags=0)))
        self.assertEqual(world.navigable(), [])
        world.apply_frame(Frame(0x2018, map_body()))
        self.assertEqual(len(world.navigable()), 1)
        fields = b'System\0New sector\0\0'
        body = struct.pack('<III', 4, len(fields) + 4, 42) + fields
        world.apply_frame(Frame(0x0097, body))
        self.assertEqual(world.sector, 'New sector')
        self.assertEqual(world.navigable(), [])

    def test_ipv4_fragments_reassemble_out_of_order(self) -> None:
        reassembler = IPv4Reassembler()
        self.assertIsNone(reassembler.feed(fragment(8, b'ijkl', False)))
        packet = reassembler.feed(fragment(0, b'abcdefgh', True))
        self.assertIsNotNone(packet)
        self.assertEqual(packet[20:], b'abcdefghijkl')
        self.assertEqual(reassembler.pending, {})

    def test_ipv4_overlapping_fragments_rejected(self) -> None:
        reassembler = IPv4Reassembler()
        self.assertIsNone(reassembler.feed(fragment(0, b'abcdefghijklmnop', True)))
        self.assertIsNone(reassembler.feed(fragment(8, b'conflict', False)))
        self.assertEqual(reassembler.pending, {})


class WebStateTests(unittest.TestCase):
    def test_snapshot_scales_coordinates_and_marks_source(self) -> None:
        state = AppState('unit.test', None, '127.0.0.1')
        state.world.apply_frame(Frame(0x2018, map_body()))
        snapshot = state.snapshot()
        self.assertFalse(snapshot['character_connected'])
        self.assertEqual(snapshot['source'], 'empty')
        self.assertEqual(snapshot['objects'][0]['position'], (1, -2, 3))

    def test_probe_closes_connection_and_releases_lock_on_failure(self) -> None:
        state = AppState('unit.test', None, '127.0.0.1')
        connection = Mock(spec=GameConnection)
        connection.check_version.side_effect = ValueError('bad response')
        with patch('enb_web.server.GameConnection.connect', return_value=connection):
            with self.assertRaises(ValueError):
                state.probe()
        connection.close.assert_called_once()
        self.assertFalse(state.probe_lock.locked())

    def test_overlapping_probe_does_not_connect(self) -> None:
        state = AppState('unit.test', None, '127.0.0.1')
        state.probe_lock.acquire()
        try:
            with patch('enb_web.server.GameConnection.connect') as connect:
                with self.assertRaises(RuntimeError):
                    state.probe()
                connect.assert_not_called()
        finally:
            state.probe_lock.release()

    def test_http_assets_state_and_probe_guard(self) -> None:
        state = AppState('unit.test', None, '127.0.0.1')
        with WebServer(('127.0.0.1', 0), state) as server:
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                for path in ('/', '/app.js', '/style.css', '/api/state'):
                    with urllib.request.urlopen(base + path) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
                request = urllib.request.Request(base + '/api/probe', method='POST')
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(request)
                self.assertEqual(failure.exception.code, 403)
                with patch.object(state, 'probe', return_value={'ok': True}):
                    request.add_header('X-ENB-Request', '1')
                    with urllib.request.urlopen(request) as response:
                        self.assertTrue(json.load(response)['ok'])
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(base + '/../../macboyje.conf')
                self.assertEqual(failure.exception.code, 404)
            finally:
                server.shutdown()
                thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
