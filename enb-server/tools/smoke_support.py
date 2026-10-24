"""Owned disposable-server lifecycle shared by protocol smoke tests."""
from __future__ import annotations

from pathlib import Path
import subprocess
import time

from start_proxy import registered


def start_server(binary: Path, host: str, state: Path, content: Path, log: Path) -> subprocess.Popen[bytes]:
    with log.open('ab') as output:
        process = subprocess.Popen([str(binary), '--bind', host, '--state-dir', str(state),
            '--content', str(content)], stdout=output, stderr=output)
    try:
        # Restoring catalogs and an in-flight checkpoint on the ARM64 Windows
        # VM can exceed fifteen seconds. Keep polling this owned process and
        # require an actual registration reply rather than a startup log line.
        deadline = time.monotonic() + 30
        while not registered(host):
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('Disposable protocol server did not start')
            time.sleep(.1)
    except BaseException:
        # The caller has not received the handle yet. Startup owns cleanup if
        # readiness checks fail, so an observation error cannot leak a server.
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise
    return process
