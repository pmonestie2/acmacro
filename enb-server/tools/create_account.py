"""Create a local account and JE character without password arguments."""
import argparse
import getpass
import os
import subprocess

from build import ROOT, build


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account')
    parser.add_argument('--character', required=True)
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    binary = ROOT / 'target/debug' / ('enb-server.exe' if os.name == 'nt' else 'enb-server') if args.no_build else build()
    password = getpass.getpass('Local account password: ')
    subprocess.run([str(binary), '--create-account', args.account, '--character', args.character, '--state-dir', str(ROOT / 'state')],
                   input=password + '\n', text=True, check=True)


if __name__ == '__main__':
    main()
