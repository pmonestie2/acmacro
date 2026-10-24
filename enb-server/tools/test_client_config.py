"""Test temporary client configuration using a fake installation only."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from client_config import LocalClientConfiguration, local_configuration


class ClientConfigurationTests(unittest.TestCase):
    def test_auth_routes_both_services_locally(self) -> None:
        original = b'[General]\r\nAAIBase=AuthLogin\r\nAAIUrl=sunrise.net-7.org\r\nLKeyUrl=https://sunrise.net-7.org/session\r\n'
        applied = local_configuration(original, 'auth')
        self.assertNotIn(b'sunrise', applied)
        self.assertIn(b'AAIBase=AuthLogin\r\n', applied)
        self.assertIn(b'AAIUrl=local.net-7.org\r\n', applied)

    def test_network_comments_and_ports_survive(self) -> None:
        original = b';Name=old\nName =old\nPort=3805\n'
        self.assertEqual(local_configuration(original, 'network'),
                         b';Name=old\nName =local.net-7.org\nPort=3805\n')

    def test_missing_auth_key_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, 'LKeyUrl'):
            local_configuration(b'AAIUrl=old\n', 'auth')

    def test_restoration_after_failure_preserves_concurrent_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = {'Data/client/ini/auth.ini': b'AAIUrl=old\nLKeyUrl=old\n',
                        'Data/common/network.ini': b'Name=old\nPort=3805\n',
                        'Data/client/ini/rg_regdata.ini': b'regserverurl=old\n'}
            for relative, data in fixtures.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            with self.assertRaisesRegex(RuntimeError, 'test failure'):
                with LocalClientConfiguration(root / 'release/client.exe', root / 'backups'):
                    self.assertIn(b'local.net-7.org', (root / 'Data/client/ini/auth.ini').read_bytes())
                    (root / 'Data/common/network.ini').write_bytes(b'concurrent edit')
                    raise RuntimeError('test failure')
            self.assertEqual((root / 'Data/client/ini/auth.ini').read_bytes(), fixtures['Data/client/ini/auth.ini'])
            self.assertEqual((root / 'Data/client/ini/rg_regdata.ini').read_bytes(), fixtures['Data/client/ini/rg_regdata.ini'])
            self.assertEqual((root / 'Data/common/network.ini').read_bytes(), b'concurrent edit')
            self.assertEqual(len(list((root / 'backups').iterdir())), 3)


if __name__ == '__main__':
    unittest.main()
