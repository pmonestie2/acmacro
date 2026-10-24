"""A persistent development certificate for Net7Proxy's local HTTPS listener.

Only the optional Windows launcher needs this. Rust networking is unchanged.
The certificate is trusted once for this Windows user and reused across launches.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import ssl
import subprocess

from build import ROOT
from client_config import ConfigurationEdit


def openssl_path() -> Path:
    executable = shutil.which('openssl')
    path = Path(executable) if executable else Path('C:/Program Files/Git/usr/bin/openssl.exe')
    if not path.is_file():
        raise FileNotFoundError('OpenSSL is needed to generate the local proxy certificate (Git for Windows includes it)')
    return path


class LocalProxyCertificate:
    def __init__(self, proxy: Path, *, instances: bool = False) -> None:
        self.directory = proxy.parent
        self.generated = ROOT / 'state' / ('proxy-instance-certificate' if instances else 'proxy-certificate')
        self.instances = instances
        self.edits: list[ConfigurationEdit] = []
        self.thumbprint: str | None = None

    def certutil(self, *arguments: str) -> None:
        executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/certutil.exe'
        subprocess.run([str(executable), '-user', *arguments], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)

    def __enter__(self) -> 'LocalProxyCertificate':
        if self.instances:
            from local_profiles import named_mutex
            with named_mutex('Global\\AcMacroRustInstanceCertificate'):
                return self.prepare()
        return self.prepare()

    def prepare(self) -> 'LocalProxyCertificate':
        self.generated.mkdir(parents=True, exist_ok=True)
        certificate, key = self.generated / 'server.cer', self.generated / 'server.pem'
        if not self.reusable(certificate, key):
            self.generate(certificate, key)
        der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding='ascii'))
        thumbprint = hashlib.sha1(der).hexdigest()
        executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/certutil.exe'
        trusted = subprocess.run([str(executable), '-user', '-store', 'Root', thumbprint],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NO_WINDOW).returncode == 0
        if not trusted:
            print('Trust the local development certificate once; subsequent launches reuse it.', flush=True)
            self.certutil(*(['-f'] if self.instances else []), '-addstore', 'Root', str(certificate))
        self.thumbprint = thumbprint
        try:
            for filename, source in [('local.net-7.org.cer', certificate), ('local.net-7.org.pem', key)]:
                path = self.directory / filename
                original, applied = path.read_bytes(), source.read_bytes()
                # Keep the first original even when reusing the certificate.
                backup = self.generated / f'original-{filename}'
                if not backup.exists():
                    backup.write_bytes(original)
                self.edits.append(ConfigurationEdit(path, original, applied, backup))
                path.write_bytes(applied)
        except BaseException:
            self.close()
            raise
        return self

    def reusable(self, certificate: Path, key: Path) -> bool:
        if not certificate.is_file() or not key.is_file():
            return False
        # OpenSSL checks expiry without depending on Python's private SSL API.
        result = subprocess.run([str(openssl_path()), 'x509', '-in', str(certificate),
                                 '-checkend', str(30 * 86400), '-noout'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return result.returncode == 0

    def generate(self, certificate: Path, key: Path) -> None:
        subprocess.run([str(openssl_path()), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-sha256', '-days', '365', '-subj', '/CN=local.net-7.org',
                        '-addext', 'subjectAltName=DNS:local.net-7.org' + (',DNS:*.local.net-7.org' if self.instances else ''),
                        '-addext', 'basicConstraints=critical,CA:FALSE',
                        '-addext', 'keyUsage=critical,digitalSignature,keyEncipherment',
                        '-addext', 'extendedKeyUsage=serverAuth',
                        '-keyout', str(key), '-out', str(certificate)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)

    def close(self) -> None:
        for edit in reversed(self.edits):
            if edit.path.read_bytes() == edit.applied:
                edit.path.write_bytes(edit.original)
            else:
                print(f'Proxy certificate file changed; original retained at {edit.backup}', flush=True)
        self.edits.clear()

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        self.close()


def main() -> None:
    """Apply to an already running owned proxy until that exact process exits."""
    import win32api
    import win32event
    from local_windows import process_path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--watch-proxy', type=int, required=True)
    parser.add_argument('--proxy', type=Path, required=True)
    args = parser.parse_args()
    handle = win32api.OpenProcess(0x1000 | 0x100000, False, args.watch_proxy)
    try:
        if Path(process_path(int(handle))).resolve() != args.proxy.resolve():
            raise RuntimeError('Proxy process identity does not match the expected executable')
        with LocalProxyCertificate(args.proxy):
            print('Current local proxy certificate installed; waiting for owned proxy exit', flush=True)
            while win32event.WaitForSingleObject(handle, 250) == win32event.WAIT_TIMEOUT:
                pass
    finally:
        handle.Close()


if __name__ == '__main__':
    main()
