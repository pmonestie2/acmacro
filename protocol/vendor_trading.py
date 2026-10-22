"""Fast vendor trading, bound to one client/proxy session.

Sales use current item IDs from the pinned client's inventory cache. Uncertain
transfers stop without retries; unrelated cargo never authorizes a sale.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import random
import select
import socket
import struct
import time
from typing import NotRequired, TypedDict, cast

from ac import AcController
from focus import process_identity
from game_instance import GameInstance
from protocol import proxy_commands
from protocol.decode_capture import decode_datagram, udp
from protocol.game_packet_monitor import udp_ports
from protocol.ip_reassembly import IPv4Reassembler
from protocol.client_cargo import ClientCargo
from protocol.vendor_cargo import VendorCargo
from util import log_event
from window_health import WindowProbeTimeout, check_window_responsive


@dataclass(frozen=True)
class VendorRoute:
    buy_npc: int
    sell_npc: int
    buy_slot: int
    buy_dialog: bytes
    sell_dialog: bytes
    item_id: int


ROUTES: dict[str, VendorRoute] = {
    'raw-data': VendorRoute(126, 636, 3, b'trade goods?', b'Progen Combine Fleet', 6549),
    'chocolate': VendorRoute(636, 126, 2, b'Progen Combine Fleet', b'trade goods?', 6548),
    'charon-earth': VendorRoute(536, 31, 0, b'Looking for trade goods?', b'Lenorn', 5258),
}


@dataclass(frozen=True)
class VendorSession:
    client_pid: int
    client_born: str
    proxy_pid: int
    proxy_born: str
    game_id: int

    @classmethod
    def from_instance(cls, instance: GameInstance) -> VendorSession:
        if (instance.pid is None or instance.born is None or
                instance.proxy_pid is None or instance.proxy_born is None):
            raise RuntimeError('Vendor trading requires an owned client and proxy')
        return cls.from_processes(instance.pid, instance.born, instance.proxy_pid, instance.proxy_born)

    @classmethod
    def from_processes(cls, client_pid: int, client_born: str,
                       proxy_pid: int, proxy_born: str) -> VendorSession:
        """Bind an already identified client/proxy pair without a login config."""
        session = cls(client_pid, client_born, proxy_pid, proxy_born, 0)
        session.check()
        process = proxy_commands.Process(proxy_pid)
        try:
            root = process.u32(0x44e620)
            sender = process.u32(root + 0x22e)
            entity = process.u32(sender + 0x10) & 0xffffff
        finally:
            process.k.CloseHandle(process.handle)
        session.check()
        if not entity:
            raise RuntimeError('No active character session')
        return cls(client_pid, client_born, proxy_pid, proxy_born,
                   entity | 0x40000000)

    def check(self) -> None:
        if (process_identity(self.client_pid) != self.client_born or
                process_identity(self.proxy_pid) != self.proxy_born):
            raise RuntimeError('Owned client/proxy exited or changed; no further submissions')


class DecodedChat(TypedDict):
    event: int
    name_1: str
    channel: str
    message: str


class DecodedFrame(TypedDict):
    opcode: str
    body_hex: NotRequired[str]
    chat: NotRequired[DecodedChat]


class DecodedDatagram(TypedDict):
    frames: list[DecodedFrame]


class UdpPacket(TypedDict):
    source: str
    destination: str
    payload: bytes


@dataclass(frozen=True)
class VendorReply:
    opcode: int
    body: bytes


class VendorPrivateMessage(InterruptedError):
    def __init__(self, sender: str) -> None:
        self.sender = sender
        super().__init__(f'Private message from {sender}; vendor macro stopped')


class VendorCapture:
    """Main-thread passive capture filtered to this proxy's UDP ports.

    No worker, global capture file, or shared mutable protocol state is used.
    Each macro owns and closes its sockets before releasing its controller.
    """

    def __init__(self, session: VendorSession) -> None:
        self.session = session
        self.sockets: list[socket.socket] = []
        self.addresses: set[str] = set()
        self.ports: set[int] = set()
        self.refreshed: float = 0.0
        self.fragments = IPv4Reassembler()
        self.seen: OrderedDict[bytes, None] = OrderedDict()
        self.private_sender: str | None = None
        self.pending: deque[VendorReply] = deque()
        self.server_messages: deque[str] = deque(maxlen=8)

    def open(self) -> None:
        self.session.check()
        self.addresses = {str(row[4][0]) for row in
                          socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        try:
            for address in self.addresses:
                if address.startswith('127.'):
                    continue
                sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)
                self.sockets.append(sock)
                sock.bind((address, 0))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                sock.ioctl(socket.SIO_RCVALL, 3)
                sock.setblocking(False)
            if not self.sockets:
                raise RuntimeError('No passive capture interface')
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        try:
            for sock in self.sockets:
                sock.close()
        finally:
            self.sockets.clear()
            self.pending.clear()

    def decode(self, packet: bytes) -> list[VendorReply]:
        complete = self.fragments.feed(packet)
        item = cast(UdpPacket | None, udp(bytes(12) + b'\x08\x00' + complete)) if complete else None
        if item is None:
            return []
        address, port = item['destination'].rsplit(':', 1)
        if address not in self.addresses or int(port) not in self.ports:
            return []
        payload = item['payload']
        if payload in self.seen:
            return []
        self.seen[payload] = None
        if len(self.seen) > 4096:
            self.seen.popitem(last=False)
        try:
            decoded = cast(DecodedDatagram, decode_datagram(payload))
        except ValueError:
            return []
        for frame in decoded.get('frames', []):
            chat = frame.get('chat')
            if (frame['opcode'] == '0x00a5' and chat is not None and chat['event'] == 3
                    and chat['channel'].strip().casefold() == 'private'):
                self.private_sender = chat['name_1'] or 'unknown sender'
                raise VendorPrivateMessage(self.private_sender)
            if frame['opcode'] == '0x0020':
                text = bytes.fromhex(frame.get('body_hex', '')).split(b'\0', 1)[0]
                self.server_messages.append(text.decode('cp1252', errors='replace')[:300])
        replies = [VendorReply(int(frame['opcode'], 16), bytes.fromhex(frame.get('body_hex', '')))
                   for frame in decoded.get('frames', []) if frame['opcode'] in ('0x0054', '0x0056', '0x001b', '0x001d')]
        return replies

    def poll(self, timeout: float = 0.0) -> None:
        """Check new packets for PMs while retaining replies for their waiter."""
        if self.private_sender is not None:
            raise VendorPrivateMessage(self.private_sender)
        self.session.check()
        now = time.monotonic()
        if now - self.refreshed >= .5:
            self.ports = udp_ports(self.session.proxy_pid)
            self.refreshed = now
        for sock in select.select(self.sockets, [], [], timeout)[0]:
            # Bound each drain so other clients' traffic cannot monopolize this macro.
            for _ in range(1024):
                try:
                    packet = sock.recv(65535)
                except BlockingIOError:
                    break
                self.pending.extend(self.decode(packet))

    def read(self, timeout: float = 0.0) -> list[VendorReply]:
        self.poll(timeout)
        replies = list(self.pending)
        self.pending.clear()
        return replies

    def clear_server_messages(self) -> None:
        self.server_messages.clear()

    def recent_server_messages(self) -> tuple[str, ...]:
        return tuple(self.server_messages)


class VendorTrader:
    def __init__(self, controller: AcController, session: VendorSession,
                 route: VendorRoute, quantity: int, cycles: int) -> None:
        if not 1 <= quantity <= 40 or cycles < 0:
            raise ValueError('Quantity must be between 1 and 40; cycles must be nonnegative')
        self.controller = controller
        self.session = session
        self.route = route
        self.quantity = quantity
        self.cycles = cycles
        self.capture = VendorCapture(session)
        self.inventory = ClientCargo(session.client_pid, session.client_born, session.game_id)
        self.cargo = VendorCargo(session.game_id)
        self.completed: int = 0
        self.buy_submissions: int = 0
        self.sale_submissions: int = 0
        self.sale_guard: tuple[int, int] | None = None
        self.purchase_guard: int | None = None
        self.capture_active: bool = False
        self.stop_path = Path(__file__).resolve().parents[1] / f'vendor_{controller.player.toon_name}.stop'

    def check_controls(self) -> None:
        if self.capture_active:
            self.capture.poll()
        self.controller._focus_wait()
        if self.capture_active:
            self.capture.poll()
        self.session.check()
        if self.stop_path.exists():
            raise InterruptedError('Vendor stop file requested')

    def checkpoint(self) -> None:
        self.check_controls()
        deadline = self.controller.active_time() + 5.0
        delayed = False
        while True:
            try:
                check_window_responsive(self.controller.window, self.session.client_pid, self.session.client_born)
                if delayed:
                    log_event('Vendor game window responding again; trading may continue')
                return
            except WindowProbeTimeout:
                if not delayed:
                    log_event('Vendor game window probe delayed; holding submissions for up to 5 seconds')
                    delayed = True
                self.check_controls()
                if self.controller.active_time() >= deadline:
                    raise RuntimeError('Owned game window remained unresponsive for 5 seconds '
                                       '(Windows error 1460); no further submissions') from None
                time.sleep(.05)

    def before_submit(self) -> None:
        # Called again after native preflight waits, immediately before submission.
        self.checkpoint()
        if self.purchase_guard is not None:
            self.read_cargo()
            free = self.cargo.free_slots(self.inventory.capacity())
            if free < self.purchase_guard:
                raise RuntimeError(f'Cargo space changed before purchase: free={free}, '
                                   f'requested={self.purchase_guard}; no purchase sent')
        if self.sale_guard is not None:
            self.read_cargo()
            slot, quantity = self.sale_guard
            self.cargo.require(slot, self.route.item_id, quantity)

    def send(self, opcode: int, body: bytes) -> None:
        self.checkpoint()
        report: dict[str, object] = {}
        proxy_commands.submit(self.session.proxy_pid, report=report,
                              frame=struct.pack('<HH', len(body) + 4, opcode) + body,
                              before_submit=self.before_submit)
        if report.get('state') != 'handler_returned':
            raise RuntimeError(f'Native submission {report.get("state")}; stopped without retry')

    def wait_reply(self, opcode: int, expected: bytes, *, exact: bool = False,
                   context: str = 'vendor opening') -> None:
        deadline = self.controller.active_time() + 10
        while True:
            self.checkpoint()
            if self.controller.active_time() >= deadline:
                raise TimeoutError(f'Vendor-opening reply timed out: {context}, opcode={opcode:#06x}; stopped without retry')
            for reply in self.capture.read(.05):
                if reply.opcode == opcode and (reply.body == expected if exact else expected in reply.body):
                    return

    def open_trade(self, npc: int, dialog: bytes) -> None:
        self.capture.read()  # Discard earlier replies before starting a new dialogue.
        player = self.session.game_id & 0xffffff
        self.send(0x4e, struct.pack('<IIB', player, npc, 4))
        self.wait_reply(0x54, dialog, context=f'NPC {npc} greeting')
        self.capture.read()
        self.send(0x55, struct.pack('<IB', player, 2))
        self.wait_reply(0x56, struct.pack('<i', 3), exact=True, context=f'NPC {npc} trade acknowledgement')

    def close_trade(self) -> None:
        for choice in (0, 230):
            self.send(0x55, struct.pack('<IB', self.session.game_id & 0xffffff, choice))

    def wait_cargo(self, *, accept_purchase_reply: bool = False,
                   expected_quantity: int | None = None) -> None:
        deadline = self.controller.active_time() + 10
        purchase = VendorCargo(self.session.game_id)
        while True:
            self.checkpoint()
            replies = self.capture.read(.05)
            if accept_purchase_reply:
                for reply in replies:
                    if reply.opcode == 0x1b:
                        purchase.apply(reply.body)
            self.read_cargo()
            slots = self.cargo.matching(self.route.item_id)
            count = sum(slots.values())
            if count:
                log_event(f'Vendor sale cargo identified: item={self.route.item_id}, '
                          f'quantity={count}, slots={sorted(slots)}; includes previously held matching items')
                return
            # A fresh server cargo reply confirms the purchase even when the
            # client's cache lags. Opening the seller is safe; sales still need
            # current client identity checks, including native preflight.
            if accept_purchase_reply and purchase.error is None:
                confirmed = sum(purchase.matching(self.route.item_id).values())
                if confirmed >= (self.quantity if expected_quantity is None else expected_quantity):
                    log_event(f'Vendor purchase confirmed by server: item={self.route.item_id}, '
                              f'quantity={confirmed}, client_quantity={count}; '
                              'opening seller; client cargo must agree before selling')
                    return
            if self.controller.active_time() >= deadline:
                log_event(f'Vendor purchase timeout diagnostics: npc={self.route.buy_npc}, '
                          f'item={self.route.item_id}, client_quantity={count}, '
                          f'packet_cargo_error={purchase.error!r}, '
                          f'server_messages={self.capture.recent_server_messages()!r}')
                raise TimeoutError(f'Cargo identity unconfirmed after purchase: item={self.route.item_id}, '
                                   f'known_quantity={count}; no sales sent, no purchase retry')

    def read_cargo(self) -> None:
        self.cargo.slots = self.inventory.snapshot(checkpoint=self.checkpoint,
                                                   clock=self.controller.active_time)

    def submit_sale(self, slot: int, quantity: int) -> None:
        """Submit one unit from a verified slot, without waiting for its result."""
        if quantity <= 0:
            raise ValueError('Sale source quantity must be positive')
        self.read_cargo()
        self.cargo.require(slot, self.route.item_id, quantity)
        self.capture.clear_server_messages()
        self.sale_guard = (slot, quantity)
        try:
            self.send(0x27, struct.pack('>6I', self.session.game_id, 1, slot, 4, 0, 1))
        finally:
            self.sale_guard = None
        self.sale_submissions += 1

    def sell_one(self, slot: int, quantity: int) -> None:
        self.submit_sale(slot, quantity)
        deadline = self.controller.active_time() + 10
        while True:
            self.checkpoint()
            self.capture.read(.05)
            self.read_cargo()
            if self.cargo.sale_confirmed(slot, self.route.item_id, quantity):
                return
            if self.controller.active_time() >= deadline:
                current = self.cargo.slots.get(slot)
                log_event(f'Vendor sale timeout diagnostics: npc={self.route.sell_npc}, '
                          f'item={self.route.item_id}, slot={slot}, quantity_before={quantity}, '
                          f'current={current}, server_messages={self.capture.recent_server_messages()!r}')
                raise TimeoutError(f'Sale cargo unconfirmed: item={self.route.item_id}, slot={slot}, '
                                   f'quantity_before={quantity}, current={current}; '
                                   'stopped without retry')

    def sell_many(self, slots: dict[int, int], *,
                  on_confirmed: Callable[[int], None] | None = None) -> int:
        """Keep one sale per distinct slot in flight; confirm each independently."""
        self.read_cargo()
        for slot, quantity in slots.items():
            if quantity <= 0:
                raise ValueError('Sale source quantity must be positive')
            self.cargo.require(slot, self.route.item_id, quantity)
        pending: dict[int, tuple[int, float]] = {}
        for slot, quantity in slots.items():
            self.submit_sale(slot, quantity)
            pending[slot] = (quantity, self.controller.active_time() + 10)
        confirmed = 0
        while pending:
            self.checkpoint()
            self.capture.read(.05)
            self.read_cargo()
            for slot, (quantity, deadline) in list(pending.items()):
                if self.cargo.sale_confirmed(slot, self.route.item_id, quantity):
                    del pending[slot]
                    confirmed += 1
                    if on_confirmed is not None:
                        on_confirmed(slot)
            for slot, (quantity, deadline) in pending.items():
                if self.controller.active_time() >= deadline:
                    log_event(f'Vendor parallel sale timeout: npc={self.route.sell_npc}, '
                              f'item={self.route.item_id}, slot={slot}, quantity_before={quantity}, '
                              f'confirmed={confirmed}, pending={sorted(pending)}, '
                              f'server_messages={self.capture.recent_server_messages()!r}')
                    raise TimeoutError(f'Parallel sale unconfirmed: item={self.route.item_id}, '
                                       f'slot={slot}; stopped without retry')
        return confirmed

    def round(self) -> None:
        route = self.route
        self.buy_submissions = self.sale_submissions = 0
        self.read_cargo()
        if not self.cargo.matching(route.item_id):
            self.open_trade(route.buy_npc, route.buy_dialog)
            self.capture.read()  # Only subsequent cargo replies may confirm this purchase.
            self.capture.clear_server_messages()
            self.read_cargo()
            capacity = self.inventory.capacity()
            free = self.cargo.free_slots(capacity)
            quantity = min(self.quantity, free)
            log_event(f'Vendor purchase planned: item={route.item_id}, capacity={capacity}, '
                      f'free_slots={free}, configured_quantity={self.quantity}, quantity={quantity}')
            if quantity == 0:
                raise RuntimeError('No verified free cargo slots; no purchase sent')
            self.purchase_guard = quantity
            try:
                self.send(0x27, struct.pack('>6I', self.session.game_id, 4, route.buy_slot, 1,
                                            0xffffffff, quantity))
            finally:
                self.purchase_guard = None
            self.buy_submissions = 1
            self.wait_cargo(accept_purchase_reply=True, expected_quantity=quantity)
            self.close_trade()
        self.open_trade(route.sell_npc, route.sell_dialog)
        self.read_cargo()
        if not self.cargo.matching(route.item_id):
            # Never start another round/buy against a stale empty client cache.
            self.wait_cargo()
        slots = self.cargo.matching(route.item_id)
        log_event(f'Vendor sale started: item={route.item_id}, quantity={sum(slots.values())}, '
                  f'slots={sorted(slots)}; unrelated and unidentified cargo excluded')
        for slot, quantity in sorted(slots.items(), reverse=True):
            for remaining in range(quantity, 0, -1):
                self.sell_one(slot, remaining)
        self.close_trade()
        self.completed += 1
        log_event(f'Vendor round {self.completed}/{self.cycles or "continuous"} sent: '
                  f'item={route.item_id}, buy submissions={self.buy_submissions}, sale cargo confirmed={self.sale_submissions}')

    def pause_between_rounds(self) -> None:
        duration = random.uniform(.3, .8)
        deadline = self.controller.active_time() + duration
        while True:
            self.checkpoint()
            remaining = deadline - self.controller.active_time()
            if remaining <= 0:
                return
            self.capture.read(min(.05, remaining))

    def run(self) -> None:
        reason = 'completed'
        log_event(f'Vendor trading started: count=0, target={self.cycles or "continuous"}, '
                  f'route={self.route.buy_npc}->{self.route.sell_npc}, quantity={self.quantity}; '
                  f'item={self.route.item_id}; item-checked packet sales; random round delay=0.3..0.8s')
        log_event(f'Vendor session: client={self.session.client_pid}/{self.session.client_born}, '
                  f'proxy={self.session.proxy_pid}/{self.session.proxy_born}, '
                  f'game_id={self.session.game_id:#x}')
        try:
            self.checkpoint()
            self.inventory.open()
            self.read_cargo()
            log_event(f'Vendor current cargo: item={self.route.item_id}, '
                      f'quantity={sum(self.cargo.matching(self.route.item_id).values())}; '
                      'source=owned client inventory; matching existing items sell before buying')
            self.capture.open()
            self.capture_active = True
            log_event('Vendor private-message detection active: any incoming private message logs off this character and peers')
            if self.controller.focus:
                self.controller.focus.release(reason='packet vendor trading')
            self.close_trade()
            while not self.cycles or self.completed < self.cycles:
                if self.completed:
                    self.pause_between_rounds()
                self.round()
        except BaseException as error:
            reason = f'{type(error).__name__}: {error}'
            raise
        finally:
            self.capture_active = False
            try:
                self.capture.close()
            finally:
                try:
                    self.inventory.close()
                finally:
                    log_event(f'Vendor trading stopped: count={self.completed}, target={self.cycles or "continuous"}, '
                              f'last_round_buy_submissions={self.buy_submissions}, '
                              f'last_round_sale_submissions={self.sale_submissions}, reason={reason}')
