"""Reproduce the exact archived C++ reference files on any development machine."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import TypedDict
import urllib.request

from build import ROOT


class SourceFile(TypedDict):
    path: str
    sha: str


def blob_hash(data: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def download(entry: SourceFile, repository: str, revision: str, directory: Path) -> str:
    path = PurePosixPath(entry['path'])
    if path.is_absolute() or '..' in path.parts or path.parts[0] != 'trunk':
        raise ValueError('Reference manifest contains an unsafe source path')
    destination = directory.joinpath(*path.parts)
    if destination.is_file() and blob_hash(destination.read_bytes()) == entry['sha']:
        return entry['path']
    url = f'https://raw.githubusercontent.com/{repository}/{revision}/{entry["path"]}'
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024 or blob_hash(data) != entry['sha']:
        raise ValueError(f'Reference source hash mismatch: {entry["path"]}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return entry['path']


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'reference/upstream')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'reference/manifest.json').read_text())
    files: list[SourceFile] = manifest['files']
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(download, entry, manifest['repository'], manifest['revision'], args.output) for entry in files]
        for count, job in enumerate(as_completed(jobs), 1):
            job.result()
            if count % 25 == 0 or count == len(jobs):
                print(f'Verified C++ reference files: {count}/{len(jobs)}', flush=True)
    print(f'Reference restored to {args.output}')


if __name__ == '__main__':
    main()
