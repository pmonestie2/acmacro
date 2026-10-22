"""Cooperative desktop ownership across up to four Windows macro processes.

A named mutex protects input. A small named mapping, protected by a second
mutex, holds a FIFO request queue and instance claims. Each macro's HTTP thread
delivers focus requests and commands; no separate server process is required.
Windows abandons a mutex on owner death; stale requests are checked by PID and
process creation time so a crashed borrower cannot block the remaining scripts.
"""
from contextlib import contextmanager
from functools import wraps
import json
import mmap
import os
import threading
import time

import win32api
import win32con
import win32event
import win32process


def process_identity(pid):
    try:
        handle = win32api.OpenProcess(0x1000 | win32con.SYNCHRONIZE, False, pid)
        try:
            if win32event.WaitForSingleObject(handle, 0) != win32event.WAIT_TIMEOUT:
                return None
            return str(win32process.GetProcessTimes(handle)["CreationTime"])
        finally:
            handle.Close()
    except win32api.error as error:
        if error.winerror == 5:
            # Do not interpret access denial as a dead owner.
            raise RuntimeError("All macros must run under the same Windows account with administrator rights") from error
        return None


def focused(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self.input_action():
            return method(self, *args, **kwargs)
    return run


class SharedFocus:
    SIZE = 16384
    HANDOFF_PAUSE = 0.2

    def __init__(self, instance, toon, *, namespace="Local\\acmacro-focus-v1", on_wait=None, claim_game=True, singleton=False, slots=None, blocked_slots=(), preferred_slot=None):
        self.thread = threading.get_ident()
        self.key = f"{os.getpid()}:{process_identity(os.getpid())}"
        self.instance = str(instance).casefold()
        self.toon = toon
        self.on_wait = on_wait
        self.waiting = False
        self.owned = False
        self.depth = 0
        self.closed = False
        self.singleton = singleton
        self.acquired_at = None
        self.total_wait = 0.0
        self.max_hold = 20.0
        self.requested = threading.Event()
        self.peer = None
        self.station_name = None
        self.trace = None
        self.section = None
        self.slot = None
        self.lock = win32event.CreateMutex(None, False, namespace + "-state")
        self.mutex = win32event.CreateMutex(None, False, namespace + "-input")
        self.mapping = mmap.mmap(-1, self.SIZE, tagname=namespace + "-mapping")
        try:
            with self.state() as state:
                self.clean(state)
                members = state["members"]
                if self.key in members:
                    raise RuntimeError("Only one game controller may run per Python process")
                games = [item for item in members.values() if item.get("game", True)]
                if slots:
                    busy = {item["instance"] for item in games}
                    order = list(range(len(slots)))
                    if preferred_slot in order:
                        order.remove(preferred_slot)
                        order.insert(0, preferred_slot)
                    self.slot = next((index for index in order
                                      if index not in blocked_slots and str(slots[index]).casefold() not in busy), None)
                    if self.slot is None:
                        raise RuntimeError("Both game/client slots are occupied")
                    self.instance = str(slots[self.slot]).casefold()
                if claim_game and (any(item.get("singleton") for item in games) or (singleton and games)):
                    raise RuntimeError("--singleton requires that no other game macro is running")
                if claim_game and any(item["instance"] == self.instance or item["toon"].casefold() == toon.casefold()
                                      for item in games):
                    raise RuntimeError("Another macro already owns this game directory or toon")
                if claim_game and len(games) >= 4:
                    raise RuntimeError("At most four macro instances may run together")
                members[self.key] = {"pid": os.getpid(), "born": process_identity(os.getpid()),
                                     "instance": self.instance, "toon": toon, "game": claim_game, "singleton": singleton}
                members[self.key]["slot"] = self.slot
        except BaseException:
            self.mapping.close()
            self.mutex.Close()
            self.lock.Close()
            raise

    @contextmanager
    def state(self):
        result = win32event.WaitForSingleObject(self.lock, 5000)
        if result not in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
            raise RuntimeError("Focus coordination state is unresponsive")
        try:
            raw = self.mapping[:].split(b"\0", 1)[0]
            state = json.loads(raw) if raw else {"members": {}, "queue": [], "owner": None}
            yield state
            encoded = json.dumps(state).encode("utf-8")
            if len(encoded) >= self.SIZE:
                raise RuntimeError("Focus coordination state exceeds its capacity")
            self.mapping[:] = encoded + b"\0" * (self.SIZE - len(encoded))
        finally:
            win32event.ReleaseMutex(self.lock)

    def clean(self, state):
        for key, member in list(state["members"].items()):
            if process_identity(member["pid"]) != member["born"]:
                del state["members"][key]
        state["queue"] = [key for key in state["queue"] if key in state["members"]]
        if state["owner"] not in state["members"]:
            state["owner"] = None

    def check_thread(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError("Only the macro's main thread may send game input")
        if self.closed:
            raise RuntimeError("Focus coordinator is closed")

    def enable_trace(self, path):
        from focus_trace import FocusTrace
        self.trace = FocusTrace(path, self.toon)
        self.record("started", singleton=self.singleton, pid=os.getpid(), slot=self.slot)

    def record(self, event, **fields):
        if self.trace:
            try:
                if self.section:
                    fields.setdefault("section", self.section)
                self.trace.record(event, **fields)
            except OSError:
                # Diagnostics must never prevent an input lock from releasing.
                try:
                    self.trace.close()
                except OSError:
                    pass
                self.trace = None

    def acquire(self):
        self.check_thread()
        if self.owned:
            return
        started = time.monotonic()
        self.waiting = True
        notified = None
        request_logged = False
        previous_owner = None
        previous_toon = None
        try:
            while True:
                with self.state() as state:
                    self.clean(state)
                    if self.key not in state["queue"]:
                        state["queue"].append(self.key)
                    # Leave a brief idle gap before a different process takes
                    # ownership. Never sleep while holding the state mutex.
                    handoff_ready = (self.singleton or state.get("last_owner") in (None, self.key)
                                     or time.monotonic() >= state.get("released_at", 0) + self.HANDOFF_PAUSE)
                    if state["queue"][0] == self.key and state["owner"] is None and handoff_ready:
                        result = win32event.WaitForSingleObject(self.mutex, 0)
                        if result in (win32event.WAIT_OBJECT_0, win32event.WAIT_ABANDONED):
                            self.owned = True
                            self.acquired_at = time.monotonic()
                            previous_owner = state.get("last_owner")
                            previous_toon = state.get("last_owner_toon")
                            state["owner"] = self.key
                            state["queue"].pop(0)
                    owner = state["members"].get(state["owner"], {})
                if self.owned:
                    self.record("acquired", previous=previous_toon,
                        handoff=previous_owner is not None and previous_owner != self.key,
                        wait_seconds=round(time.monotonic() - started, 3))
                    return
                if not request_logged:
                    self.record("requested", owner=owner.get("toon"), reason="awaiting ownership")
                    request_logged = True
                endpoint = owner.get("http")
                if endpoint and endpoint != notified:
                    from comms import post
                    import uuid
                    try:
                        post(endpoint["port"], "/focus/request", {"id": uuid.uuid4().hex}, endpoint["token"])
                    except (OSError, ValueError):
                        pass  # The mutex and FIFO still arbitrate a disconnected peer.
                    notified = endpoint
                if self.on_wait:
                    self.on_wait()
                time.sleep(0.05)
        except BaseException:
            with self.state() as state:
                state["queue"] = [key for key in state["queue"] if key != self.key]
            raise
        finally:
            self.waiting = False
            self.last_wait = time.monotonic() - started
            self.total_wait += self.last_wait

    def release(self, reason="completed action"):
        self.check_thread()
        if self.depth:
            return
        if self.owned:
            held = time.monotonic() - self.acquired_at
            with self.state() as state:
                if state["owner"] == self.key:
                    state["owner"] = None
                    state["last_owner"] = self.key
                    state["last_owner_toon"] = self.toon
                    state["released_at"] = time.monotonic()
                win32event.ReleaseMutex(self.mutex)
                self.owned = False
                self.acquired_at = None
                self.requested.clear()
            self.record("released", reason=reason, held_seconds=round(held, 3))

    def boundary(self):
        if self.singleton or self.depth or not self.owned:
            return
        with self.state() as state:
            self.clean(state)
            waiting = bool(state["queue"])
        if waiting or self.requested.is_set() or self.expired():
            self.release(reason="waiting peer" if waiting else "HTTP focus request" if self.requested.is_set() else "20s budget")

    def cancel_wait(self):
        """A paused contender must not remain at the head of the input queue."""
        self.check_thread()
        self.release(reason="macro paused")
        with self.state() as state:
            state['queue'] = [key for key in state['queue'] if key != self.key]

    def expired(self):
        return bool(not self.singleton and self.owned and self.acquired_at is not None
                    and time.monotonic() - self.acquired_at >= self.max_hold)

    def start_comms(self, on_logoff):
        from comms import PeerServer
        self.peer = PeerServer(on_logoff, self.requested.set,
                               lambda: {"toon": self.toon, "focus": self.owned, "singleton": self.singleton,
                                        "station": self.station_name})
        with self.state() as state:
            state["members"][self.key]["http"] = {"port": self.peer.port, "token": self.peer.token}

    def station_blocker(self, station):
        """Atomic first-arrival reservation: exactly one macro proceeds."""
        if self.singleton:
            return None
        self.check_thread()
        with self.state() as state:
            self.clean(state)
            member = state["members"][self.key]
            if member.get("station") != station:
                state["station_ticket"] = state.get("station_ticket", 0) + 1
                member["station"] = station
                member["station_ticket"] = state["station_ticket"]
            self.station_name = station
            if not station:
                return None
            occupants = [item for item in state["members"].values()
                         if item.get("game", True) and item.get("station") == station
                         and item.get("station_ticket", 0) < member["station_ticket"]]
            return min(occupants, key=lambda item: item["station_ticket"])["toon"] if occupants else None

    def claim_available_slot(self, slots, blocked_slots=(), preferred_slot=None):
        """Re-evaluate both client/position slots atomically after stopping a client."""
        with self.state() as state:
            self.clean(state)
            busy = {member["instance"] for key, member in state["members"].items()
                    if key != self.key and member.get("game", True)}
            order = list(range(len(slots)))
            if preferred_slot in order:
                order.remove(preferred_slot)
                order.insert(0, preferred_slot)
            slot = next((index for index in order
                         if index not in blocked_slots and str(slots[index]).casefold() not in busy), None)
            if slot is None:
                raise RuntimeError("Both game/client slots are occupied")
            self.slot, self.instance = slot, str(slots[slot]).casefold()
            state["members"][self.key].update(slot=slot, instance=self.instance)
            return slot

    def send_logoff(self, toon, request_id=None):
        from comms import post
        import uuid
        with self.state() as state:
            self.clean(state)
            peers = [member for key, member in state["members"].items()
                     if key != self.key and member["toon"].casefold() == toon.casefold()]
            endpoint = peers[0].get("http") if peers else None
        if not endpoint:
            raise RuntimeError(f"No HTTP peer is running for {toon}")
        return post(endpoint["port"], "/message",
                    {"id": request_id or uuid.uuid4().hex, "command": "logoff"}, endpoint["token"])

    def broadcast_logoff(self):
        """Best-effort HTTP STOP fanout, without acquiring desktop focus."""
        from comms import post
        import uuid
        with self.state() as state:
            self.clean(state)
            peers = [dict(member) for key, member in state["members"].items()
                     if key != self.key and member.get("game", True)]
        results = []
        for peer in peers:
            endpoint = peer.get("http")
            request_id = uuid.uuid4().hex
            error = "Peer has no HTTP endpoint"
            if endpoint:
                for attempt in range(2):
                    try:
                        reply = post(endpoint["port"], "/message",
                            {"id": request_id, "command": "logoff"}, endpoint["token"])
                        if reply.get("status") != "queued":
                            raise ValueError("Unexpected logoff acknowledgement")
                        error = None
                        break
                    except (OSError, ValueError) as exc:
                        error = str(exc)
            results.append((peer["toon"], error))
        return results

    def close(self):
        if self.closed:
            return
        if self.peer:
            self.peer.close()
        self.depth = 0
        self.release()
        with self.state() as state:
            state["members"].pop(self.key, None)
            state["queue"] = [key for key in state["queue"] if key != self.key]
        self.mapping.close()
        self.mutex.Close()
        self.lock.Close()
        self.closed = True
        if self.trace:
            self.record("closed")
            if self.trace:
                self.trace.close()
                self.trace = None
