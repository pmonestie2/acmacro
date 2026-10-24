"""Temporarily direct the original client's INI files to the local proxy."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import uuid

from build import ROOT


def local_configuration(data: bytes, kind: str, *, hostname: str = 'local.net-7.org') -> bytes:
    """Preserve comments and line endings while replacing active endpoints."""
    if kind == 'auth':
        replacements = {'AAIUrl': b'local.net-7.org',
                        'LKeyUrl': b'https://local.net-7.org/misc/touchsession.jsp?lkey=%s'}
    elif kind == 'network':
        replacements = {'Name': b'local.net-7.org'}
    elif kind == 'registration':
        replacements = {'regserverurl': b'https://local.net-7.org/subsxml'}
    else:
        raise ValueError(f'Unknown client configuration kind: {kind}')
    replacements = {key: value.replace(b'local.net-7.org', hostname.encode('ascii'))
                    for key, value in replacements.items()}
    for key, value in replacements.items():
        pattern = rb'(?im)^([ \t]*' + key.encode('ascii') + rb'[ \t]*=[ \t]*)[^\r\n]*'
        data, count = re.subn(pattern, lambda match: match[1] + value, data)
        if count == 0:
            raise ValueError(f'Client {kind} configuration is missing {key}')
    return data


@dataclass(frozen=True)
class ConfigurationEdit:
    path: Path
    original: bytes
    applied: bytes
    backup: Path


class LocalClientConfiguration:
    def __init__(self, client: Path, backup_directory: Path | None = None) -> None:
        self.directory = client.parent.parent
        self.backup_directory = backup_directory if backup_directory is not None else ROOT / 'logs'
        self.edits: list[ConfigurationEdit] = []

    def __enter__(self) -> 'LocalClientConfiguration':
        files = [('Data/client/ini/auth.ini', 'auth'),
                 ('Data/common/network.ini', 'network'),
                 ('Data/client/ini/rg_regdata.ini', 'registration')]
        planned: list[ConfigurationEdit] = []
        token = uuid.uuid4().hex
        # Validate every file before modifying any of them.
        for relative, kind in files:
            path = self.directory / relative
            original = path.read_bytes()
            applied = local_configuration(original, kind)
            planned.append(ConfigurationEdit(path, original, applied,
                           self.backup_directory / f'client-before-{token}-{path.name}.bin'))
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        try:
            for edit in planned:
                edit.backup.write_bytes(edit.original)
                # Register before writing so even a partial write is reported.
                self.edits.append(edit)
                edit.path.write_bytes(edit.applied)
        except BaseException:
            self.restore()
            raise
        return self

    def restore(self) -> None:
        for edit in reversed(self.edits):
            if edit.path.read_bytes() == edit.applied:
                edit.path.write_bytes(edit.original)
            else:
                print(f'Client configuration changed while running; backup retained at {edit.backup}', flush=True)
        self.edits.clear()

    def __exit__(self, error_type: object, error: object, traceback: object) -> None:
        self.restore()
