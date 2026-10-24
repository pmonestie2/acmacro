"""Build the Rust sandbox, including the GNU linker tools on this ARM64 VM."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def cargo_path() -> Path:
    installed = shutil.which('cargo')
    candidate = Path(installed) if installed else Path.home() / '.cargo' / 'bin' / ('cargo.exe' if os.name == 'nt' else 'cargo')
    if not candidate.is_file():
        raise FileNotFoundError('Rust is not installed; install Rustup and its stable toolchain first')
    return candidate


def build_environment(cargo: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment['PATH'] = str(cargo.parent) + os.pathsep + environment.get('PATH', '')
    if os.name != 'nt':
        return environment
    rustc = cargo.parent / 'rustc.exe'
    sysroot = Path(subprocess.check_output([str(rustc), '--print', 'sysroot'], env=environment, text=True).strip())
    host = subprocess.check_output([str(rustc), '-vV'], env=environment, text=True)
    if 'host: x86_64-pc-windows-gnu' not in host:
        return environment
    native = sysroot / 'lib/rustlib/x86_64-pc-windows-gnu/bin'
    environment['PATH'] = os.pathsep.join((str(native / 'self-contained'), str(native), environment['PATH']))
    linker = native / 'self-contained/x86_64-w64-mingw32-gcc.exe'
    if linker.is_file():
        environment.setdefault('CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER', str(linker))
    # Rust's bundled GNU dlltool needs an external assembler. LLVM's dlltool
    # creates import libraries directly, avoiding that dependency on ARM64.
    dlltool = shutil.which('llvm-dlltool', path=environment['PATH'])
    local = ROOT.parent / 'diagnostics/rust_tools/llvm-dlltool.exe'
    if not dlltool and local.is_file():
        dlltool = str(local)
    if dlltool:
        environment['RUSTFLAGS'] = (environment.get('RUSTFLAGS', '') + f' -C dlltool={dlltool}').strip()
    return environment


def build(release: bool = False) -> Path:
    cargo = cargo_path()
    arguments = [str(cargo), 'build', '--manifest-path', str(ROOT / 'Cargo.toml'), '--locked']
    if release:
        arguments.append('--release')
    subprocess.run(arguments, env=build_environment(cargo), check=True)
    return ROOT / 'target' / ('release' if release else 'debug') / ('enb-server.exe' if os.name == 'nt' else 'enb-server')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true')
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--fmt', action='store_true')
    args = parser.parse_args()
    if args.test or args.fmt:
        cargo = cargo_path()
        command = 'fmt' if args.fmt else 'test'
        subprocess.run([str(cargo), command, '--manifest-path', str(ROOT / 'Cargo.toml')], env=build_environment(cargo), check=True)
    else:
        print(build(args.release))


if __name__ == '__main__':
    main()
