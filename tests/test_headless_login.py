"""Headless login, navigation, and cancellation using synthetic packets only."""
import hashlib
import socket
import struct
import threading
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error

from enb_protocol.connection import GameConnection
from enb_protocol.login import Character, CharacterTicket, Credentials, GlobalLogin, authenticate, decode_characters, global_error, ticket_frame, register_comms
from enb_protocol.session import SectorStream, SpaceSession, login_body
from enb_protocol.wire import Frame, UDP_HEADER, encode_udp
from enb_web.sessions import SessionController, SessionView
from tests.test_headless_protocol import map_body


def make_session() -> SpaceSession:
    join = bytearray(64)
    struct.pack_into('!II', join, 16, 0x40000007, 1076)
    ticket = CharacterTicket(0x40000007, 1076, bytes(join))
    session = SpaceSession('unit.test', ticket, Character(0, 'Test', 1076, 'Test sector'),
                           Mock(spec=socket.socket), Mock(spec=socket.socket), 3524)
    session.world.sector = 'Test sector'
    session.world.apply_frame(Frame(0x2018, map_body()))
    session.started = True
    return session


class LoginTests(unittest.TestCase):
    def test_ticket_string_length_includes_terminating_nul_and_is_little_endian(self) -> None:
        self.assertEqual(ticket_frame('account-123').body, struct.pack('<I', 12) + b'account-123\0')
        with self.assertRaises(ValueError):
            ticket_frame('bad\0ticket')

    def test_global_rejection_exposes_server_message_and_code(self) -> None:
        message = b'Error: You have not completed registration.'
        body = struct.pack('<I', len(message)) + struct.pack('!I', 16) + message
        self.assertEqual(global_error(body), message.decode() + ' (server code 16)')
        self.assertIn('truncated', global_error(bytes(3)))

    def test_udp_registration_precedes_auth_and_socket_ownership_transfers(self) -> None:
        comms = Mock(spec=socket.socket)
        connection = Mock(spec=GameConnection)
        connection.check_version.return_value = 0
        connection.receive.return_value = Frame(0x70, bytes(1874))
        events: list[str] = []

        def register(host: str) -> socket.socket:
            events.append('register')
            return comms

        def auth(host: str, credentials: Credentials, pin: str | None) -> str:
            events.append('authenticate')
            return 'fresh-ticket'

        with patch('enb_protocol.login.register_comms', side_effect=register), \
             patch('enb_protocol.login.authenticate', side_effect=auth), \
             patch('enb_protocol.login.GameConnection.connect', return_value=connection):
            login, _ = GlobalLogin.open('unit.test', Credentials('account', 'secret'))
        self.assertEqual(events, ['register', 'authenticate'])
        self.assertIs(login.take_comms(), comms)
        login.close()
        comms.close.assert_not_called()
        connection.close.assert_called_once()

    def test_failed_auth_closes_registered_socket_without_tcp_connection(self) -> None:
        comms = Mock(spec=socket.socket)
        with patch('enb_protocol.login.register_comms', return_value=comms), \
             patch('enb_protocol.login.authenticate', side_effect=ValueError('rejected')), \
             patch('enb_protocol.login.GameConnection.connect') as connect:
            with self.assertRaises(ValueError):
                GlobalLogin.open('unit.test', Credentials('account', 'secret'))
        comms.close.assert_called_once()
        connect.assert_not_called()

    def test_registration_checks_reply_before_accepting_socket(self) -> None:
        peer = Mock(spec=socket.socket)
        peer.recv.return_value = encode_udp(0x1009, 0)
        with patch('enb_protocol.login.socket.socket', return_value=peer), \
             patch('enb_protocol.login.socket.gethostbyname', return_value='127.0.0.1'):
            with self.assertRaises(ValueError):
                register_comms('unit.test')
        peer.close.assert_called_once()

    def test_explicit_certificate_pin_checked_before_credentials_sent(self) -> None:
        connection = Mock()
        connection.sock.getpeercert.return_value = b'wrong certificate'
        with patch('enb_protocol.login.http.client.HTTPSConnection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'configured pin'):
                authenticate('unit.test', Credentials('account', 'secret'), '00' * 32)
        connection.request.assert_not_called()
        connection.close.assert_called_once()

    def test_matching_pin_allows_fresh_authentication(self) -> None:
        connection = Mock()
        connection.sock.getpeercert.return_value = b'certificate'
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.return_value = b'Valid=TRUE\r\nTicket=fresh-ticket\r\n'
        pin = hashlib.sha256(b'certificate').hexdigest()
        with patch('enb_protocol.login.http.client.HTTPSConnection', return_value=connection):
            self.assertEqual(authenticate('unit.test', Credentials('account', 'secret'), pin), 'fresh-ticket')
        connection.close.assert_called_once()

    def test_authentication_errors_do_not_disclose_credentials(self) -> None:
        with patch('enb_protocol.login.urllib.request.urlopen', side_effect=urllib.error.URLError('https://unit.test/?password=secret')):
            with self.assertRaises(ConnectionError) as failure:
                authenticate('unit.test', Credentials('account', 'secret'))
        self.assertNotIn('secret', str(failure.exception))
        self.assertNotIn('secret', repr(Credentials('account', 'secret')))

    def test_character_list_layout_and_slot_validation(self) -> None:
        body = bytearray(1874)
        struct.pack_into('!II', body, 0, 0, 1076)
        body[133:137] = b'Test'
        body[52:58] = b'Sector'
        self.assertEqual(decode_characters(bytes(body)), [Character(0, 'Test', 1076, 'Sector')])
        struct.pack_into('!I', body, 0, 5)
        with self.assertRaises(ValueError):
            decode_characters(bytes(body))
        with self.assertRaises(ValueError):
            decode_characters(bytes(1873))

    def test_character_selection_correlates_fresh_ticket(self) -> None:
        connection = Mock(spec=GameConnection)
        body = bytearray(68)
        struct.pack_into('!II', body, 20, 0x40000007, 1076)
        connection.receive.return_value = Frame(0x006f, bytes(body))
        ticket = GlobalLogin(connection).select(2)
        self.assertEqual((ticket.player_id, ticket.sector_id), (0x40000007, 1076))
        connection.send.assert_called_once_with(Frame(0x006e, struct.pack('!I', 2)))
        self.assertEqual(ticket.join_data, bytes(body[4:]))
        with self.assertRaises(ValueError):
            GlobalLogin(connection).select(5)

    def test_login_reserved_fields_and_timestamp_layout(self) -> None:
        session = make_session()
        body = login_body(session.ticket, 12345)
        self.assertEqual(len(body), 137)
        self.assertEqual(body[:64], session.ticket.join_data)
        self.assertEqual(struct.unpack_from('<I', body, 64)[0], 12345)
        self.assertEqual(body[68:108], bytes(40))
        self.assertEqual(body[125], 0)


class SectorStreamTests(unittest.TestCase):
    def test_large_aux_and_following_frame_across_out_of_order_datagrams(self) -> None:
        stream = SectorStream(42)
        frames = [Frame(0x1b, bytes(4000)), Frame(0x2018, map_body())]
        payload = b''.join(frame.encode() for frame in frames)
        chunks = [payload[offset:offset + 1200] for offset in range(0, len(payload), 1200)]
        packets = [encode_udp(0x2016 if index == 0 else 0x201a, 42, chunk, index) for index, chunk in enumerate(chunks)]
        received: list[Frame] = []
        for index in (1, 0, 3, 2):
            received.extend(stream.feed(packets[index]))
        self.assertEqual(received, frames)
        self.assertEqual(stream.feed(packets[2]), [])
        self.assertEqual(stream.waiting, {})
        self.assertEqual(stream.pending, b'')

    def test_wrong_player_excessive_gap_and_conflicting_duplicate(self) -> None:
        for packet in (encode_udp(0x2016, 43, b'', 0), encode_udp(0x2016, 42, b'', 256)):
            with self.assertRaises(ValueError):
                SectorStream(42).feed(packet)
        stream = SectorStream(42)
        stream.feed(encode_udp(0x2016, 42, b'abcd', 1))
        with self.assertRaises(ValueError):
            stream.feed(encode_udp(0x2016, 42, b'efgh', 1))


class NavigationTests(unittest.TestCase):
    def test_warp_uses_sector_object_id_and_current_player(self) -> None:
        session = make_session()
        session.navigate(123, 'warp')
        packet = session.sector.send.call_args.args[0]
        self.assertEqual(UDP_HEADER.unpack_from(packet)[1:3], (0x9b, 0x40000007))
        self.assertEqual(struct.unpack_from('<IHI', packet, 12), (7, 1, 123))
        with self.assertRaises(ValueError):
            session.navigate(999, 'warp')

    def test_gate_requires_matching_server_acknowledgment_and_activates_once(self) -> None:
        session = make_session()
        session.navigate(123, 'gate')
        self.assertEqual(session.sector.send.call_count, 2)
        session.apply_frame(Frame(0x19, struct.pack('<II', 999, 0xffffffff)))
        self.assertEqual(session.sector.send.call_count, 2)
        session.apply_frame(Frame(0x19, struct.pack('<II', 123, 0xffffffff)))
        self.assertEqual(struct.unpack_from('<4I', session.sector.send.call_args.args[0], 12), (0x40000007, 18, 123, 0))
        session.apply_frame(Frame(0x19, struct.pack('<II', 123, 0xffffffff)))
        self.assertEqual(session.sector.send.call_count, 3)

    def test_gate_does_not_finish_without_camera_confirmation(self) -> None:
        session = make_session()
        session.navigate(123, 'gate')
        session.apply_frame(Frame(0x19, struct.pack('<II', 123, 0xffffffff)))
        session.gate_submitted_at -= 20
        session.sector.recv.side_effect = socket.timeout
        session.poll()
        self.assertFalse(session.gate_finished)
        session.apply_frame(Frame(0x92, struct.pack('>II', 4, 123)))
        session.poll()
        self.assertTrue(session.gate_finished)
        self.assertEqual(struct.unpack_from('<4I', session.sector.send.call_args.args[0], 12)[1], 19)

    def test_handoff_preserves_player_and_rejects_station_or_other_player(self) -> None:
        session = make_session()
        body = bytearray(64)
        struct.pack_into('!II', body, 16, 0x40000007, 1070)
        session.apply_frame(Frame(0x3a, bytes(body)))
        self.assertEqual(session.pending_handoff.sector_id, 1070)
        struct.pack_into('!II', body, 16, 0x40000008, 1070)
        with self.assertRaises(ValueError):
            session.apply_frame(Frame(0x3a, bytes(body)))


class ControllerTests(unittest.TestCase):
    def test_receive_failure_closes_sockets_without_logging_off_a_replacement_session(self) -> None:
        controller = SessionController('unit.test')
        session = make_session()
        session.poll = Mock(side_effect=ConnectionError('lost session'))
        session.close = Mock()
        controller.session = session
        controller.status = 'connected'
        controller.run()
        session.close.assert_called_once_with(logout=False)
        self.assertEqual(controller.snapshot().status, 'error')

    def test_manual_authentication_lists_characters_and_logout_closes_pending_connection(self) -> None:
        controller = SessionController('unit.test')
        connection = Mock(spec=GlobalLogin)
        characters = [Character(0, 'Test', 1076, 'Sector')]
        with patch('enb_web.sessions.GlobalLogin.open', return_value=(connection, characters)):
            controller.authenticate(Credentials('account', 'secret'))
        self.assertEqual(controller.snapshot().status, 'choose_character')
        self.assertEqual(controller.character_list(), characters)
        controller.logout()
        connection.close.assert_called_once()
        self.assertEqual(controller.character_list(), [])

    def test_overlapping_authentication_sends_nothing(self) -> None:
        controller = SessionController('unit.test')
        controller.operation.acquire()
        try:
            with patch('enb_web.sessions.GlobalLogin.open') as open_login:
                with self.assertRaises(RuntimeError):
                    controller.authenticate(Credentials('account', 'secret'))
                open_login.assert_not_called()
        finally:
            controller.operation.release()

    def test_selection_expiration_requires_fresh_credentials(self) -> None:
        controller = SessionController('unit.test')
        connection = Mock(spec=GlobalLogin)
        controller.global_login = connection
        controller.characters = [Character(0, 'Test', 1076, 'Sector')]
        controller.authenticated_at = time.monotonic() - 121
        with self.assertRaisesRegex(ValueError, 'expired'):
            controller.login(0)
        connection.close.assert_called_once()

    def test_sector_correlation_and_pending_queue_limit(self) -> None:
        controller = SessionController('unit.test')
        session = make_session()
        controller.session = session
        controller.status = 'connected'
        controller.publish(session)
        with self.assertRaises(ValueError):
            controller.navigate('Wrong sector', 123, 'warp')
        controller.navigate('Test sector', 123, 'warp')
        with self.assertRaises(RuntimeError):
            controller.navigate('Test sector', 123, 'warp')
        controller.logout()
        self.assertTrue(controller.commands.empty())
        self.assertTrue(session.cancel.is_set())

    def test_stop_joins_receive_worker_before_returning(self) -> None:
        controller = SessionController('unit.test')
        session = make_session()
        entered = threading.Event()

        def receive() -> None:
            entered.set()
            session.cancel.wait(2)

        session.poll = receive
        session.close = Mock()
        controller.session = session
        controller.status = 'connected'
        controller.thread = threading.Thread(target=controller.run)
        controller.thread.start()
        self.assertTrue(entered.wait(1))
        controller.logout()
        session.close.assert_called_once()
        self.assertIsNone(controller.thread)
        self.assertEqual(controller.snapshot().status, 'disconnected')


if __name__ == '__main__':
    unittest.main()
