"""Certificate lifecycle tests; never touch the Windows certificate store."""
from __future__ import annotations

from pathlib import Path
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from proxy_certificate import LocalProxyCertificate


def generate_fixture(certificate: Path, key: Path) -> None:
    certificate.write_text(ssl.DER_cert_to_PEM_cert(b'fixture certificate'))
    key.write_bytes(b'fixture key')


class ProxyCertificateTests(unittest.TestCase):
    def test_replaces_files_and_keeps_trust_between_launches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            certificate, key = root / 'local.net-7.org.cer', root / 'local.net-7.org.pem'
            certificate.write_bytes(b'original certificate')
            key.write_bytes(b'original key')
            with patch('proxy_certificate.ROOT', root), \
                 patch('proxy_certificate.openssl_path', return_value=Path('openssl')), \
                 patch.object(LocalProxyCertificate, 'generate', side_effect=generate_fixture), \
                 patch('proxy_certificate.subprocess.run', return_value=subprocess.CompletedProcess([], 1)), \
                 patch.object(LocalProxyCertificate, 'certutil') as trust:
                context = LocalProxyCertificate(root / 'net7proxy.exe')
                with context:
                    thumbprint = context.thumbprint
                    self.assertNotEqual(certificate.read_bytes(), b'original certificate')
                    self.assertEqual(key.read_bytes(), b'fixture key')
                self.assertEqual(certificate.read_bytes(), b'original certificate')
                self.assertEqual(key.read_bytes(), b'original key')
                self.assertEqual(trust.call_args_list[0].args[:2], ('-addstore', 'Root'))
                self.assertEqual(trust.call_count, 1)
                self.assertIsNotNone(thumbprint)
                # A subsequent launch finds the same certificate and trust.
                with patch.object(LocalProxyCertificate, 'reusable', return_value=True), \
                     patch('proxy_certificate.subprocess.run', return_value=subprocess.CompletedProcess([], 0)):
                    with LocalProxyCertificate(root / 'net7proxy.exe') as again:
                        self.assertEqual(again.thumbprint, thumbprint)
                self.assertEqual(trust.call_count, 1)

    def test_failed_trust_install_restores_original_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for suffix in ('cer', 'pem'):
                (root / f'local.net-7.org.{suffix}').write_bytes(suffix.encode())
            with patch('proxy_certificate.ROOT', root), \
                 patch('proxy_certificate.openssl_path', return_value=Path('openssl')), \
                 patch.object(LocalProxyCertificate, 'generate', side_effect=generate_fixture), \
                 patch('proxy_certificate.subprocess.run', return_value=subprocess.CompletedProcess([], 1)), \
                 patch.object(LocalProxyCertificate, 'certutil', side_effect=RuntimeError('trust failed')):
                with self.assertRaisesRegex(RuntimeError, 'trust failed'):
                    with LocalProxyCertificate(root / 'net7proxy.exe'):
                        self.fail('Failed trust must prevent launch')
            for suffix in ('cer', 'pem'):
                self.assertEqual((root / f'local.net-7.org.{suffix}').read_bytes(), suffix.encode())


if __name__ == '__main__':
    unittest.main()
