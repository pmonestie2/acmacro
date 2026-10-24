"""Run the Rust server in the current terminal; Ctrl+C saves and exits."""
import argparse
import os
from pathlib import Path
import subprocess

from build import ROOT, build
from start_proxy import default_server_address, sandbox_address


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', help='Bind IP; defaults to this VM LAN address for Net7Proxy')
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    address = sandbox_address(args.bind) if args.bind else default_server_address()
    binary: Path = ROOT / 'target/debug' / ('enb-server.exe' if os.name == 'nt' else 'enb-server') if args.no_build else build()
    process = subprocess.Popen([str(binary), '--bind', address, '--content', str(ROOT / 'data/asteroid_belt_alpha.json'), '--state-dir', str(ROOT / 'state')])
    try:
        raise SystemExit(process.wait())
    except KeyboardInterrupt:
        # The child shares this foreground console and receives Ctrl+C too.
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)


if __name__ == '__main__':
    main()
