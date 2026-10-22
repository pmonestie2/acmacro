"""Optional gate navigation using recorded sector-specific IDs, never coordinates.

Only pre-submission failures permit click fallback. Once a native thread has
been submitted, uncertain results stop the macro rather than issue two routes.
"""
import struct
import json
import time
from pathlib import Path

from focus import process_identity
from protocol import proxy_commands


# Captured October 8, 2026. AB Gamma IDs must never be used for AB Alpha.
GATE_IDS = {
    ("pluto", "akeron"): 0x186a5,
    ("pluto", "uranus"): 0x186a2,
    ("akeron", "pluto"): 0x187b6,
    ("akeron", "saturn"): 0x186a0,
    ("jupiter", "saturn"): 0x186a1,
    ("saturn", "akeron"): 0x1870f,
    ("saturn", "aba"): 0x186a3,
    ("aba", "saturn"): 0x186ae,
    ("aba", "earth"): 0x186cc,
    ("earth", "aba"): 0x186a4,
}


class NavigationUncertain(RuntimeError):
    pass


def submit(instance, target_id, via=()):
    """Return a report; fail closed after native submission, otherwise allow fallback."""
    report = {"state": "preparing"}
    def record():
        folder = Path(__file__).resolve().parent / 'diagnostics'
        folder.mkdir(exist_ok=True)
        with (folder / 'navigation_attempts.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(report, time=time.time(), target_id=hex(target_id),
                                         via=[hex(value) for value in via],
                                         proxy_pid=getattr(instance, 'proxy_pid', None))) + '\n')
    try:
        if not instance or not instance.valid() or not instance.proxy_pid:
            raise RuntimeError("No owned live game/proxy")
        if process_identity(instance.proxy_pid) != instance.proxy_born:
            raise RuntimeError("Owned proxy exited or its PID was reused")
        process = proxy_commands.Process(instance.proxy_pid)
        try:
            root = process.u32(0x44e620)
            sender = process.u32(root + 0x22e)
            entity = process.u32(sender + 0x10) & 0xffffff
        finally:
            process.k.CloseHandle(process.handle)
        targets = (*via, target_id)
        frame = struct.pack("<HHIH", 10 + 4 * len(targets), 0x9b, entity, len(targets))
        frame += struct.pack('<' + 'I' * len(targets), *targets)
        proxy_commands.submit(instance.proxy_pid, report=report, frame=frame)
    except Exception as error:
        report['error'] = str(error)
        record()
        if report.get("thread_id"):
            raise NavigationUncertain(f"Gate navigation was submitted but failed: {error}") from error
        return {"state": "fallback", "error": str(error)}
    record()
    if report["state"] == "not_sent" and report.get("native_handler_attempted") is False:
        return {"state": "fallback", "error": report["error"]}
    if report["state"] != "handler_returned":
        raise NavigationUncertain(f"Gate navigation result uncertain: {report.get('error', report['state'])}")
    return report
