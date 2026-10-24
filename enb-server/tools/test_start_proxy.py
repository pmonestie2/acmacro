"""Launcher address checks without game processes or network traffic."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from start_proxy import default_server_address, ensure_server, proxy_server_address


class LauncherAddressTests(unittest.TestCase):
    @patch('start_proxy.socket.gethostbyname', return_value='192.168.47.128')
    def test_default_uses_hostname_interface(self, resolve: object) -> None:
        self.assertEqual(default_server_address(), '192.168.47.128')

    def test_proxy_rejects_loopback_and_public_server(self) -> None:
        for address in ('127.0.0.1', '127.0.0.2', '8.8.8.8'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                proxy_server_address(address)

    def test_remote_private_server_is_allowed(self) -> None:
        self.assertEqual(proxy_server_address('192.168.47.100'), '192.168.47.100')

    @patch('start_proxy.registered', return_value=False)
    @patch('start_proxy.socket.gethostbyname_ex', return_value=('vm', [], ['192.168.47.128']))
    @patch('start_proxy.build')
    def test_remote_failure_does_not_start_local_server(self, build: object, resolve: object, probe: object) -> None:
        with self.assertRaisesRegex(RuntimeError, 'start the Rust server on that machine'):
            ensure_server('192.168.47.100')


if __name__ == '__main__':
    unittest.main()
