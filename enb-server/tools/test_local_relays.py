"""Socket-level relay checks; these never launch or drive a game client."""
from __future__ import annotations

import os
import socket
import unittest

from local_relays import LoopbackRelay, proxy_listener_ports


class RelayTests(unittest.TestCase):
    def test_bidirectional_stream_survives_client_half_close(self) -> None:
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            server.settimeout(3)
            relay = LoopbackRelay('127.0.0.1', 0, target_port=server.getsockname()[1])
            try:
                relay.start()
                with socket.create_connection(('127.0.0.1', relay.port), timeout=3) as client:
                    with server.accept()[0] as upstream:
                        upstream.settimeout(3)
                        client.sendall(b'login request')
                        client.shutdown(socket.SHUT_WR)
                        self.assertEqual(upstream.recv(1024), b'login request')
                        self.assertEqual(upstream.recv(1024), b'')
                        upstream.sendall(b'login response')
                        upstream.shutdown(socket.SHUT_WR)
                        self.assertEqual(client.recv(1024), b'login response')
                        self.assertEqual(client.recv(1024), b'')
            finally:
                relay.close()
            self.assertFalse(relay.thread.is_alive())
            self.assertTrue(all(not connection.alive() for connection in relay.connections))

    def test_close_interrupts_connected_idle_streams(self) -> None:
        with socket.socket() as server:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            server.settimeout(3)
            relay = LoopbackRelay('127.0.0.1', 0, target_port=server.getsockname()[1])
            try:
                relay.start()
                with socket.create_connection(('127.0.0.1', relay.port), timeout=3) as client:
                    with server.accept()[0] as upstream:
                        upstream.settimeout(3)
                        # A round trip proves the connection is registered before close.
                        client.sendall(b'ready')
                        self.assertEqual(upstream.recv(1024), b'ready')
                        relay.close()
                        self.assertEqual(client.recv(1024), b'')
                        self.assertEqual(upstream.recv(1024), b'')
            finally:
                relay.close()
            self.assertFalse(relay.thread.is_alive())
            self.assertTrue(all(not connection.alive() for connection in relay.connections))

    @unittest.skipUnless(os.name == 'nt', 'Windows listener ownership API')
    def test_listener_snapshot_filters_interface_and_process(self) -> None:
        with socket.socket() as server:
            server.bind(('127.0.0.2', 0))
            server.listen(1)
            port = server.getsockname()[1]
            self.assertIn(port, proxy_listener_ports(os.getpid(), '127.0.0.2'))
            self.assertNotIn(port, proxy_listener_ports(os.getpid(), '127.0.0.1'))
            self.assertNotIn(port, proxy_listener_ports(0, '127.0.0.2'))
