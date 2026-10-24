"""Private installation and alias ownership checks; no processes or live clients."""
from __future__ import annotations

from contextlib import nullcontext
import os
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_profiles import ClientProfile, ProfileDns, copy_client, prepare_profile, isolate_client_mutex


class ProfileTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows primary-client hosts manager')
    def test_primary_dns_cleanup_preserves_current_profiles_and_unknown_edits(self) -> None:
        from local_windows import MARKER, local_hosts, restore_local_hosts
        original = b'127.0.0.1 localhost\r\n192.168.1.1 local.net-7.org other\r\n'
        first = b'127.0.0.2 third.local.net-7.org # acmacro-local-instance third\r\n'
        second = b'127.0.0.3 buddy.local.net-7.org # acmacro-local-instance buddy\r\n'
        before = original + first
        applied = local_hosts(before, MARKER + b' fixture')
        current = applied.replace(first,b'') + second
        restored = restore_local_hosts(before,applied,current)
        self.assertIsNotNone(restored)
        assert restored is not None
        self.assertNotIn(first,restored)
        self.assertIn(second,restored)
        self.assertIn(b'192.168.1.1 local.net-7.org other',restored)
        self.assertNotIn(MARKER,restored)
        self.assertIsNone(restore_local_hosts(before,applied,current + b'1.2.3.4 user-edit\r\n'))

    def test_singleton_identity_is_private_repeatable_and_preserves_unknown_edits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / 'installed.exe'
            contents = b'fixture executable\0enb_mutex_lock\0unchanged instructions'
            original.write_bytes(contents)
            first, second = root / 'first.exe', root / 'second.exe'
            first.write_bytes(contents)
            second.write_bytes(contents)
            with patch('local_profiles.CLIENT_SHA256', hashlib.sha256(contents).hexdigest()):
                isolate_client_mutex(first, original, 'third')
                expected = first.read_bytes()
                isolate_client_mutex(first, original, 'third')
                self.assertEqual(first.read_bytes(), expected)
                isolate_client_mutex(second, original, 'buddy')
                self.assertNotEqual(first.read_bytes(), second.read_bytes())
                self.assertEqual(len(expected), len(contents))
                with self.assertRaisesRegex(RuntimeError, 'independent copy'):
                    isolate_client_mutex(original, original, 'third')
                first.write_bytes(b'changed externally')
                with self.assertRaisesRegex(RuntimeError, 'refusing to overwrite'):
                    isolate_client_mutex(first, original, 'third')
                self.assertEqual(first.read_bytes(), b'changed externally')
            self.assertEqual(original.read_bytes(), contents)

    def test_copies_settings_and_links_only_immutable_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, target = root / 'source', root / 'target'
            for relative, content in [('release/client.exe',b'client'),('Data/client/ini/auth.ini',b'original'),
                                      ('Data/client/art/model.w3d',b'model'),('Data/client/output/cache.pld',b'cache')]:
                path = source / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            copy_client(source, target)
            (target / 'Data/client/ini/auth.ini').write_bytes(b'private')
            self.assertEqual((source / 'Data/client/ini/auth.ini').read_bytes(),b'original')
            self.assertTrue(os.path.samefile(source / 'Data/client/art/model.w3d',target / 'Data/client/art/model.w3d'))
            self.assertFalse(os.path.samefile(source / 'Data/client/output/cache.pld',target / 'Data/client/output/cache.pld'))

    def test_profiles_reserve_distinct_interfaces_and_keep_original_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            client, proxy = root / 'source/release/client.exe', root / 'proxy/net7proxy.exe'
            client.parent.mkdir(parents=True)
            proxy.parent.mkdir(parents=True)
            client.write_bytes(b'client')
            proxy.write_bytes(b'proxy')
            original = {
                'Data/client/ini/auth.ini': b'AAIUrl=original\nLKeyUrl=https://original/login\n',
                'Data/common/network.ini': b'Name=original\n',
                'Data/client/ini/rg_regdata.ini': b'regserverurl=https://original/registration\n'}
            for relative, contents in original.items():
                path = client.parent.parent / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(contents)
            with patch('local_profiles.ROOT',root / 'server'), patch('local_profiles.named_mutex',return_value=nullcontext()):
                first = prepare_profile('third',client,proxy)
                second = prepare_profile('buddy',client,proxy)
                self.assertNotEqual(first.address,second.address)
                self.assertIn(first.hostname.encode(),(first.directory / 'game/Data/client/ini/auth.ini').read_bytes())
                self.assertEqual(prepare_profile('third',client,proxy).address,first.address)
            for relative,contents in original.items():
                self.assertEqual((client.parent.parent / relative).read_bytes(),contents)

    def test_dns_cleanup_matches_full_profile_name(self) -> None:
        dns = ProfileDns(ClientProfile('third','127.0.0.2',Path('unused')))
        self.assertTrue(dns.owns_line(b'127.0.0.2 third.local.net-7.org # acmacro-local-instance third\r\n'))
        self.assertFalse(dns.owns_line(b'127.0.0.3 third2.local.net-7.org # acmacro-local-instance third2\r\n'))
        self.assertFalse(dns.owns_line(b'127.0.0.2 third.local.net-7.org # user entry\r\n'))
