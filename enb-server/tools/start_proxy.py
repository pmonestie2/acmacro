"""Start Net7Proxy against a local or private LAN Rust sandbox."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import time

from build import ROOT, build
from client_config import LocalClientConfiguration


@dataclass(frozen=True)
class ProxyPlan:
    server: str
    client: Path
    proxy: Path

    def arguments(self) -> list[str]:
        return [str(self.proxy), f'/ADDRESS:{self.server}', f'/CLIENT:{self.client}',
                '/LC', '/SSL:443', '/POPT', '/DML']


def sandbox_address(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    allowed = any(address in network for network in (ipaddress.IPv4Network('127.0.0.0/8'), ipaddress.IPv4Network('10.0.0.0/8'), ipaddress.IPv4Network('172.16.0.0/12'), ipaddress.IPv4Network('192.168.0.0/16')))
    if not allowed or str(address) == '127.0.0.1':
        raise ValueError('Use another loopback address or a private LAN server; 127.0.0.1 belongs to the local proxy')
    return str(address)


def default_server_address() -> str:
    """Match the LAN interface used by Net7Proxy's fixed UDP socket.

    Net7Proxy binds that socket to the resolved machine hostname. Windows
    rejects connecting a LAN-bound UDP socket to a loopback destination.
    """
    address = sandbox_address(socket.gethostbyname(socket.gethostname()))
    if ipaddress.IPv4Address(address).is_loopback:
        raise ValueError('Machine hostname resolves to loopback; use --server with the VM private LAN address')
    return address


def proxy_server_address(value: str | None) -> str:
    address = default_server_address() if value is None else sandbox_address(value)
    if ipaddress.IPv4Address(address).is_loopback:
        raise ValueError('Net7Proxy requires a private LAN server address; its LAN-bound UDP socket cannot connect to loopback')
    return address


def registered(server: str, timeout: float = .3) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
        channel.settimeout(timeout)
        channel.connect((server, 3806))
        channel.send(struct.pack('<HHIII', 16, 0x1000, 0, 0, 246))
        try:
            reply = channel.recv(1024)
        except (TimeoutError, ConnectionError, OSError):
            return False
    return len(reply) >= 12 and struct.unpack_from('<HHI', reply) == (len(reply), 0x1001, 0)


def ensure_server(server: str, no_build: bool = False) -> subprocess.Popen[bytes] | None:
    if registered(server):
        print(f'Local server is responding at {server}', flush=True)
        return None
    local_addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    if not ipaddress.IPv4Address(server).is_loopback and server not in local_addresses:
        raise RuntimeError(f'Remote sandbox {server} is not responding; start the Rust server on that machine first')
    binary = ROOT / 'target/debug/enb-server.exe' if no_build else build()
    if not binary.is_file():
        raise FileNotFoundError(f'Build the server first: {binary}')
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    with (logs / 'server.log').open('ab') as output:
        process = subprocess.Popen([str(binary), '--bind', server, '--content', str(ROOT / 'data/asteroid_belt_alpha.json'),
                                    '--state-dir', str(ROOT / 'state')], cwd=ROOT, stdout=output, stderr=output,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Local server exited; see {logs / "server.log"}')
        if registered(server):
            print(f'Local Rust server started: PID {process.pid}, address={server}', flush=True)
            return process
        time.sleep(.1)
    process.terminate()
    process.wait(timeout=5)
    raise TimeoutError('Local server did not become ready; no proxy launched')


def launch(plan: ProxyPlan, no_build: bool = False) -> None:
    if os.name != 'nt':
        raise RuntimeError('Net7Proxy and the graphical game require Windows; the Rust server itself is portable')
    for path, name in ((plan.client, 'client.exe'), (plan.proxy, 'net7proxy.exe')):
        if not path.is_file() or path.name.lower() != name:
            raise FileNotFoundError(f'Missing {name}: {path}')
    from local_windows import LocalCertificateDns, ProxyJob, ensure_no_game
    from proxy_certificate import LocalProxyCertificate
    ensure_no_game(plan.client, plan.proxy)
    owned_server = ensure_server(plan.server, no_build)
    try:
        # The DNS override is restored only after the owned proxy/game job ends.
        # Neither normal production clients nor unrelated processes are stopped.
        with LocalCertificateDns(), LocalClientConfiguration(plan.client), LocalProxyCertificate(plan.proxy):
            job = ProxyJob()
            try:
                job.start(plan.arguments(), plan.proxy.parent)
                logs = ROOT / 'logs'
                logs.mkdir(exist_ok=True)
                (logs / 'local-launch.json').write_text(json.dumps({'proxy_pid': job.pid,
                    'server_pid': owned_server.pid if owned_server else None, 'server': plan.server, 'client': str(plan.client)}, indent=2))
                print(f'Local Net7Proxy launched: PID {job.pid}', flush=True)
                account_file = ROOT / 'state/account.local.json'
                if account_file.exists():
                    account = json.loads(account_file.read_text())
                    print(f'Account: {account["username"]}; character: {account["character"]["name"]}. Use the password you created.', flush=True)
                else:
                    print('Demo credentials: local / local; choose SandboxPilot.', flush=True)
                print('Keep this launcher open. Closing the local game restores its temporary DNS entry.', flush=True)
                job.wait()
            finally:
                job.close()
    except OSError:
        if owned_server is not None:
            owned_server.terminate()
            owned_server.wait(timeout=5)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server', help='Private LAN server IP; defaults to this VM hostname address')
    parser.add_argument('--client', type=Path, default=Path('C:/Program Files (x86)/EA GAMES/Earth & Beyond/release/client.exe'))
    parser.add_argument('--proxy', type=Path, default=Path('C:/Program Files (x86)/Net-7/bin/net7proxy.exe'))
    parser.add_argument('--check', action='store_true', help='Print the launch plan without starting or contacting anything')
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--instance', help='Independent native client profile, e.g. buddy or third')
    args = parser.parse_args()
    try:
        plan = ProxyPlan(proxy_server_address(args.server), args.client.resolve(), args.proxy.resolve())
        if args.check:
            print(subprocess.list2cmdline(plan.arguments()))
            account_file = ROOT / 'state/account.local.json'
            if account_file.exists():
                account = json.loads(account_file.read_text())
                print(f'Local account: {account["username"]}; character: {account["character"]["name"]}; race={account["character"]["race"]}; profession={account["character"]["profession"]}. Password is not printed.')
            else:
                print('Local-only demo account: local / local; one sector; no production config or credentials are read.')
            print('During launch only: local.net-7.org maps to the local proxy; the previous hosts file is restored when it exits.')
            print('Client auth/network/registration INI endpoints temporarily use local.net-7.org and are restored on exit.')
        elif args.instance:
            from local_profiles import launch_profile
            launch_profile(args.instance, plan.server, plan.client, plan.proxy, no_build=args.no_build)
        else:
            launch(plan, args.no_build)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Local launch failed: {error}\n')


if __name__ == '__main__':
    main()
