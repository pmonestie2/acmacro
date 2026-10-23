import argparse
from pathlib import Path

from .server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description='Internal ENB web client backed by the shared game protocol')
    parser.add_argument('--host', default='192.168.47.128', help='Local interface to listen on')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--server', default='sunrise.net-7.org', help='Game server for the direct connection test')
    parser.add_argument('--capture', type=Path, help='Relogin capture to display as an offline snapshot')
    parser.add_argument('--capture-server', default='216.219.87.147')
    parser.add_argument('--certificate-sha256', help='Optional SHA256 pin for the legacy authentication server certificate')
    args = parser.parse_args()
    try:
        serve(args.host, args.port, args.server, args.capture, args.capture_server, args.certificate_sha256)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Web client failed: {error}\n')


if __name__ == '__main__':
    main()
