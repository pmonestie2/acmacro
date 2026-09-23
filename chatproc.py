"""Read the whole locked chat log and retain replies across game reconnects."""
from collections import deque
import ctypes
import gc
from pathlib import Path
import re
import threading
import time

import pythoncom
import wmi

from config import CHAT_LOG_PATH
from chat_reply import ReplyGenerator
from util import log, log_every, log_event

_lock = threading.Lock()
_snapshot_lock = threading.Lock()
_marker = None
_ready = False
_next_line = 0
_last_line = None
_pending = deque()
_self_name = ""
_responder = ReplyGenerator()
_private = re.compile(r"(?P<sender>[^\s:()]+)\s+\(private\):\s*(?P<message>.*)$", re.IGNORECASE)


def require_admin():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise PermissionError("Right-click run_pluto.cmd and choose Run as administrator.")


def _read_snapshot(path):
    service = wmi.WMI(find_classes=False)
    snapshot, snapshot_id = None, None
    try:
        result, created_id = service.Win32_ShadowCopy.Create(
            Context="ClientAccessible", Volume=path.anchor
        )
        if result:
            raise OSError(f"Cannot create chat snapshot: VSS error {result}")
        snapshot_id = created_id
        snapshot = service.Win32_ShadowCopy(ID=snapshot_id)[0]
        source = Path(snapshot.DeviceObject + "\\" + str(path.relative_to(path.anchor)))
        return source.read_text(encoding="cp1252", errors="replace")
    finally:
        try:
            if snapshot is not None:
                snapshot.Delete_()
            elif snapshot_id:
                for item in service.Win32_ShadowCopy(ID=snapshot_id):
                    item.Delete_()
        finally:
            # WMI 1.5.1 caches classes that point back to their namespace. Break
            # that cycle on the creating thread, including create/delete errors.
            service._classes_map.clear()


def read_chat_log(path=CHAT_LOG_PATH):
    """Read the whole text file into memory and remove the temporary snapshot."""
    require_admin()
    with _snapshot_lock:
        pythoncom.CoInitialize()
        text, error = None, None
        try:
            text = _read_snapshot(Path(path).resolve())
        except Exception as exc:
            error = OSError(str(exc))
        finally:
            # The except block has exited: its traceback no longer retains WMI
            # frames. Release any remaining cycles before uninitializing COM.
            gc.collect()
            pythoncom.CoUninitialize()
    if error:
        raise error
    return text


def _complete_lines(text):
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\r", "\n")):
        lines.pop()
    return lines


def reset_chat(toon_name):
    """Once per macro run: ignore history, then retain every new message."""
    global _marker, _ready, _next_line, _last_line, _self_name, _responder
    require_admin()
    with _lock:
        lines = _complete_lines(read_chat_log()) if CHAT_LOG_PATH.exists() else []
        _marker, _ready = None, False
        _next_line = len(lines)
        _last_line = lines[-1] if lines else None
        _self_name = toon_name.casefold()
        _pending.clear()
        _responder.close()
        _responder = ReplyGenerator()


def stop_chat():
    """Cancel background generation when Pluto stops; relogins use suspend_chat."""
    with _lock:
        _responder.close()


def suspend_chat():
    global _ready
    with _lock:
        _ready = False


def begin_chat():
    """Arm a UI-ready marker without resetting the cursor or pending replies."""
    global _marker, _ready
    with _lock:
        _marker = f"hello_{time.time_ns()}"
        _ready = False
        return _marker


def current_marker():
    return _marker


def chat_ready():
    with _lock:
        return _ready


def next_reply():
    with _lock:
        return _pending[0] if _pending else None


def reply_decision(reply):
    with _lock:
        if _pending and _pending[0] is reply:
            return _responder.next_decision(reply)
    return None


def reply_sent(reply, text):
    with _lock:
        if _pending and _pending[0] is reply:
            _responder.sent(reply, text)
            _pending.popleft()


def read_chat_internal():
    """Poll independently of login; only complete, previously unseen lines count."""
    global _ready, _next_line, _last_line
    with _lock:
        try:
            lines = _complete_lines(read_chat_log())
        except OSError as exc:
            log_every("chat_read", f"Cannot read chat.log; will retry: {exc}", 60, important=True)
            return None
        # A new/truncated log starts at zero; already queued replies survive.
        if _next_line and (len(lines) < _next_line or lines[_next_line - 1] != _last_line):
            _next_line = 0
        for line in lines[_next_line:]:
            match = _private.search(line.rstrip())
            if not match:
                continue
            sender, message = match.group("sender", "message")
            if sender.casefold() == _self_name:
                if _marker and message == _marker:
                    _ready = True
                continue
            log_event(f"Private message from {sender}: {message}")
            _pending.append((sender, message))
        _next_line = len(lines)
        _last_line = lines[-1] if lines else None
