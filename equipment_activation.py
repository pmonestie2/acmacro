"""Verified Kraken packet activation for an owned Net-7 proxy session.

Caller must self-target first (the macro uses the backtick key). The EquipUse
body identifies the player using the device, not its buff recipient. This API
does not infer an equipment slot from a character name: slot 1 must actually
contain Kraken, as verified in the live Macboypw session.
"""
import json
from pathlib import Path
import struct
import time

from focus import process_identity
from gate_navigation import NavigationUncertain
from protocol import proxy_commands


def kraken_frame(game_id):
    frame = struct.pack('<HHIBB', 10, 0x5d, game_id, 2, 1)
    proxy_commands.validate_command(frame)
    return frame


def activate_kraken(instance, *, self_targeted=False, slot_verified=False):
    """Submit once; require confirmation of self-target and equipped device.

    A handler return confirms submission, not that the buff took effect. Verify
    the game's activation message. Never retry automatically on uncertain calls.
    No focus is required by this call; self-targeting may require UI focus.
    """
    if not self_targeted or not slot_verified:
        raise ValueError('Self-target and Kraken equipment slot must be verified')
    if (not instance or not instance.valid() or not instance.proxy_pid
            or process_identity(instance.proxy_pid) != instance.proxy_born):
        raise RuntimeError('Owned proxy session is no longer valid')
    process = proxy_commands.Process(instance.proxy_pid)
    try:
        root = process.u32(0x44e620)
        sender = process.u32(root + 0x22e)
        game_id = (process.u32(sender + 0x10) & 0xffffff) | 0x40000000
    finally:
        process.k.CloseHandle(process.handle)
    report = {}
    try:
        proxy_commands.submit(instance.proxy_pid, report=report, frame=kraken_frame(game_id))
    except Exception as error:
        report['error'] = str(error)
        if report.get('native_handler_attempted') or report.get('thread_id'):
            raise NavigationUncertain('Kraken activation result uncertain') from error
        raise
    finally:
        path = Path(__file__).resolve().parent / 'diagnostics/equipment_attempts.jsonl'
        path.parent.mkdir(exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(report, time=time.time(), proxy_pid=instance.proxy_pid)) + '\n')
    if report.get('state') != 'handler_returned':
        raise NavigationUncertain('Kraken activation not confirmed: ' + str(report))
    return report
