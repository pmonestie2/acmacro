"""Stop all registered macros first, then all configured E&B clients and proxies."""
import argparse
import ctypes
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import win32api
import win32con
import win32event
import win32process

from arrange_windows import client_path
from config import GAME_CLIENT, GAME_CLIENTS, PROJECT_DIR, net7_proxy
from focus import SharedFocus, process_identity
from game_instance import GameInstance
from player import load_player_config


def command_arguments(command):
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    count = ctypes.c_int()
    argv = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        return []
    kernel = ctypes.WinDLL("kernel32")
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(argv)


def macro_script(command):
    args = command_arguments(command)
    index = 1
    while index < len(args) and args[index].startswith("-"):
        option = args[index]
        if option in ("-c", "-m"):
            return None
        index += 2 if option in ("-W", "-X") else 1
    if index >= len(args):
        return None
    path = Path(args[index])
    allowed = {"pluto.py", "pluto_console.py", "trade.py", "vendor.py"}
    if path.name.casefold() not in allowed:
        return None
    # run_pluto.cmd/UAC use absolute paths. Also accept direct local invocation.
    if path.is_absolute() and path.resolve().parent != PROJECT_DIR.resolve():
        return None
    if not path.is_absolute() and path.parent != Path("."):
        return None
    return path.name


def find_macros():
    query = ("Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" "
             "| Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", query],
                            capture_output=True, text=True, check=True, timeout=15)
    rows = json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(rows, dict):
        rows = [rows]
    found = []
    for row in rows:
        script = macro_script(row.get("CommandLine") or "")
        if script:
            pid = row["ProcessId"]
            born = process_identity(pid)
            if born:
                found.append({"pid": pid, "born": born, "toon": script})
    return found


def collect_claims():
    coordination = SharedFocus("stop-all", "StopAll", claim_game=False)
    try:
        with coordination.state() as state:
            coordination.clean(state)
            return [dict(member) for member in state["members"].values() if member.get("game", True)]
    finally:
        coordination.close()


def configured_paths(claims):
    paths = {GAME_CLIENT.resolve(), net7_proxy().resolve()}
    paths.update(GAME_CLIENTS)
    paths.update(Path(member["instance"]).resolve() for member in claims)
    for config in PROJECT_DIR.glob("config_player*.txt"):
        try:
            paths.add(GameInstance(load_player_config(config)).client)
        except (OSError, ValueError):
            continue
    return paths


def find_games(paths):
    wanted = {str(path).casefold() for path in paths}
    found = []
    for pid in win32process.EnumProcesses():
        if not pid:
            continue
        try:
            path = client_path(pid)
            if str(path).casefold() in wanted:
                born = process_identity(pid)
                if born:
                    found.append((pid, born, path.name))
        except OSError:
            continue
    return found


def terminate(pid, born):
    """Use one verified handle so PID reuse cannot kill another process."""
    try:
        handle = win32api.OpenProcess(win32con.PROCESS_TERMINATE | 0x1000 | win32con.SYNCHRONIZE, False, pid)
    except win32api.error as error:
        if error.winerror == 87:
            return False
        raise
    try:
        if str(win32process.GetProcessTimes(handle)["CreationTime"]) != born:
            return False
        if win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_OBJECT_0:
            return False
        win32api.TerminateProcess(handle, 0)
        if win32event.WaitForSingleObject(handle, 5000) == win32event.WAIT_TIMEOUT:
            raise RuntimeError(f"Process {pid} did not stop")
        return True
    finally:
        handle.Close()


def stop_all(list_only=False):
    claims = collect_claims()
    paths = configured_paths(claims)
    output = []
    # Stop macro recovery loops before clients, so they cannot relaunch games.
    macros = {member["pid"]: member for member in claims}
    for member in find_macros():
        macros.setdefault(member["pid"], member)
    for member in macros.values():
        if list_only or terminate(member["pid"], member["born"]):
            output.append(f"{'Running' if list_only else 'Stopped'} macro {member['toon']} (PID {member['pid']})")
    for pid, born, name in find_games(paths):
        if list_only or terminate(pid, born):
            output.append(f"{'Running' if list_only else 'Stopped'} {name} (PID {pid})")
    return output or ["No running E&B games or macros found."]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="List matching processes without stopping them")
    parser.add_argument("--player", help="Character name or player config file; stop only its macros, clients and proxies")
    parser.add_argument("--report", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.player is not None:
        from kill_player import resolve_player
        try:
            args.player = resolve_player(args.player)
        except ValueError as error:
            parser.error(str(error))
    if not ctypes.windll.shell32.IsUserAnAdmin():
        from win32com.shell import shell, shellcon
        with tempfile.TemporaryDirectory(prefix="enb-stop-") as directory:
            report = Path(directory) / "result.txt"
            argv = [str(Path(__file__).resolve()), "--report", str(report)]
            if args.list:
                argv.append("--list")
            if args.player:
                argv.extend(["--player", args.player])
            print("Requesting administrator access...", flush=True)
            result = shell.ShellExecuteEx(fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
                lpFile=sys.executable, lpParameters=subprocess.list2cmdline(argv), nShow=win32con.SW_HIDE)
            handle = result["hProcess"]
            try:
                win32event.WaitForSingleObject(handle, win32event.INFINITE)
                code = win32process.GetExitCodeProcess(handle)
            finally:
                handle.Close()
            print(report.read_text(encoding="utf-8") if report.exists() else "Stop-all helper did not produce a report.")
            return code
    try:
        if args.player is not None:
            from kill_player import stop_player
            output = "\n".join(stop_player(args.player, list_only=args.list))
        else:
            output = "\n".join(stop_all(args.list))
        code = 0
    except Exception as error:
        output, code = f"Could not stop all games: {error}", 1
    if args.report:
        args.report.write_text(output, encoding="utf-8")
    else:
        print(output)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
