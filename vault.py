"""List vault item IDs, withdraw by ID, or unload hull cargo, including in space."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import struct
from typing import TypedDict, cast

from ac import AcController
from chatproc import require_admin
from config import WINDOW_TITLE
from player import PLAYER, load_player_config
from protocol import proxy_commands
from protocol.client_cargo import ClientCargo
from protocol.client_vault import ClientVault
from protocol.vendor_cargo import CargoItem
from protocol.vendor_trading import ROUTES, VendorCapture, VendorSession
from util import configure_event_log, log_event
from watchdog import StopMacro
from text_search import matches, normalize


@dataclass(frozen=True)
class VaultEntry:
    slot: int
    item_id: int
    quantity: int
    name: str


class ItemNameCache(TypedDict):
    names: dict[str, str]


def item_names(root: Path) -> dict[int, str]:
    names: dict[int, str] = {}
    cache = root / 'diagnostics/survey_item_name_cache.json'
    if cache.exists():
        data = cast(ItemNameCache, json.loads(cache.read_text(encoding='utf-8')))
        names.update({int(key): str(value) for key, value in data['names'].items() if key.isdigit()})
    sql = root / 'diagnostics/npc_source/sigseg1v_db_mysql_net7.sql'
    if sql.exists():
        with sql.open(encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if line.startswith('INSERT INTO `item_base` VALUES'):
                    fields = re.findall(r"'((?:\\.|[^'])*)'", line)
                    if len(fields) > 6 and fields[0].isdigit():
                        names.setdefault(int(fields[0]), fields[6].replace("\\'", "'"))
    return names


def matches_item(query: str, item_id: int, name: str) -> bool:
    if query.strip().isdigit():
        return int(query.strip()) == item_id
    return bool(name) and matches(query, name)


class VaultController:
    def __init__(self, controller: AcController, session: VendorSession) -> None:
        self.controller = controller
        self.session = session
        self.cargo = ClientCargo(session.client_pid, session.client_born, session.game_id)
        self.vault = ClientVault(self.cargo)
        self.capture = VendorCapture(session)
        self.capture_active: bool = False
        self.pending: tuple[int, int, CargoItem] | None = None
        self.pending_withdrawal: tuple[int, int, CargoItem] | None = None
        self.moved: int = 0
        self.skipped: int = 0

    def checkpoint(self) -> None:
        if self.capture_active:
            self.capture.poll()
        self.controller._focus_wait()
        if self.capture_active:
            self.capture.poll()
        self.session.check()

    def cargo_snapshot(self) -> dict[int, CargoItem]:
        return self.cargo.snapshot(checkpoint=self.checkpoint, clock=self.controller.active_time)

    def vault_snapshot(self) -> dict[int, CargoItem]:
        return self.vault.snapshot(self.checkpoint, self.controller.active_time)

    def before_submit(self) -> None:
        self.checkpoint()
        if self.pending is None:
            raise RuntimeError('No pending vault deposit')
        source, target, item = self.pending
        if self.cargo_snapshot().get(source) != item:
            raise RuntimeError('Cargo source changed; no transfer sent')
        if self.vault_snapshot().get(target) != CargoItem(0xffffffff, 0):
            raise RuntimeError('Vault destination is no longer empty; no transfer sent')

    def deposit(self, source: int, target: int, item: CargoItem) -> bool:
        if item.item_id is None or item.quantity is None or item.quantity <= 0:
            raise ValueError('Deposit requires a known item and positive quantity')
        self.capture.read()  # Only later replies can describe this transfer.
        self.capture.clear_server_messages()
        self.pending = (source, target, item)
        report: dict[str, object] = {}
        frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', self.session.game_id,
                                                       1, source, 3, target, item.quantity)
        try:
            self.before_submit()
            proxy_commands.submit(self.session.proxy_pid, report=report,
                                  frame=frame, before_submit=self.before_submit)
        finally:
            self.pending = None
        if report.get('state') != 'handler_returned':
            raise RuntimeError(f'Uncertain vault submission: {report.get("state")}; no retry')
        deadline = self.controller.active_time() + 10
        while True:
            self.checkpoint()
            self.capture.read(.05)
            cargo = self.cargo_snapshot().get(source)
            vault = self.vault_snapshot().get(target)
            if cargo == CargoItem(0xffffffff, 0) and vault == item:
                self.moved += 1
                log_event(f'Vault deposit confirmed: item={item.item_id}, quantity={item.quantity}, '
                          f'cargo_slot={source}, vault_slot={target}, moved_stacks={self.moved}')
                return True
            messages = self.capture.recent_server_messages()
            if any('cannot move trade item into vault' in message.casefold() for message in messages):
                if cargo != item or vault != CargoItem(0xffffffff, 0):
                    raise RuntimeError('Vault rejection conflicts with inventory changes; no retry')
                log_event(f'Vault deposit skipped: item={item.item_id}, cargo_slot={source}; server rejects trade goods')
                return False
            if self.controller.active_time() >= deadline:
                raise TimeoutError(f'Vault deposit unconfirmed: item={item.item_id}, cargo_slot={source}, '
                                   f'vault_slot={target}, cargo={cargo}, vault={vault}, '
                                   f'server_messages={messages!r}; no retry')

    def entries(self, grep: str | None = None) -> list[VaultEntry]:
        names = item_names(Path(__file__).resolve().parent)
        result: list[VaultEntry] = []
        for slot, item in sorted(self.vault_snapshot().items()):
            if item.item_id in (0, 0xfffffffe, 0xffffffff):
                continue
            if item.item_id is None or item.quantity is None or item.quantity <= 0:
                raise RuntimeError(f'Unidentified vault slot {slot}')
            name = names.get(item.item_id, '')
            if grep is None or matches_item(grep, item.item_id, name):
                result.append(VaultEntry(slot, item.item_id, item.quantity, name or '(name unknown)'))
        return result

    def before_withdraw(self) -> None:
        self.checkpoint()
        if self.pending_withdrawal is None:
            raise RuntimeError('No pending vault withdrawal')
        source, target, item = self.pending_withdrawal
        if self.vault_snapshot().get(source) != item:
            raise RuntimeError('Vault source changed; no withdrawal sent')
        if self.cargo_snapshot().get(target) != CargoItem(0xffffffff, 0):
            raise RuntimeError('Cargo destination is no longer empty; no withdrawal sent')

    def withdraw_one(self, source: int, target: int, item: CargoItem) -> None:
        if item.item_id is None or item.quantity is None or item.quantity <= 0:
            raise ValueError('Withdrawal requires a known item and positive source quantity')
        self.capture.read()
        self.pending_withdrawal = source, target, item
        report: dict[str, object] = {}
        frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', self.session.game_id, 3, source, 1, target, 1)
        try:
            self.before_withdraw()
            proxy_commands.submit(self.session.proxy_pid, report=report,
                                  frame=frame, before_submit=self.before_withdraw)
        finally:
            self.pending_withdrawal = None
        if report.get('state') != 'handler_returned':
            raise RuntimeError(f'Uncertain withdrawal submission: {report.get("state")}; no retry')
        expected = CargoItem(item.item_id, item.quantity - 1) if item.quantity > 1 else CargoItem(0xffffffff, 0)
        deadline = self.controller.active_time() + 10
        while True:
            self.checkpoint()
            self.capture.read(.05)
            if (self.cargo_snapshot().get(target) == CargoItem(item.item_id, 1)
                    and self.vault_snapshot().get(source) == expected):
                self.moved += 1
                log_event(f'Vault withdrawal confirmed: item={item.item_id}, quantity=1, '
                          f'vault_slot={source}, cargo_slot={target}, moved_items={self.moved}')
                return
            if self.controller.active_time() >= deadline:
                raise TimeoutError(f'Vault withdrawal unconfirmed: item={item.item_id}, '
                                   f'vault_slot={source}, cargo_slot={target}; no retry')

    def withdraw(self, item_id: int, quantity: int, *, dry_run: bool = False) -> None:
        if not 0 < item_id < 0xfffffffe or quantity < 1:
            raise ValueError('Withdrawal item ID and quantity must be positive')
        available = sum(item.quantity or 0 for item in self.vault_snapshot().values() if item.item_id == item_id)
        capacity = self.cargo.capacity()
        empty = [slot for slot, item in self.cargo_snapshot().items()
                 if slot < capacity and item == CargoItem(0xffffffff, 0)]
        if available < quantity:
            raise ValueError(f'Vault has {available} of item {item_id}; requested {quantity}; no transfers sent')
        if len(empty) < quantity:
            raise ValueError(f'Need {quantity} empty cargo slots; found {len(empty)}; no transfers sent')
        if dry_run:
            log_event(f'Vault withdrawal preview: item={item_id}, quantity={quantity}, available={available}')
            return
        for target in sorted(empty)[:quantity]:
            candidates = [(slot, value) for slot, value in sorted(self.vault_snapshot().items())
                          if value.item_id == item_id and (value.quantity or 0) > 0]
            if not candidates:
                raise RuntimeError('Vault contents changed; no further withdrawal sent')
            source, item = candidates[0]
            self.withdraw_one(source, target, item)

    def run(self, *, list_only: bool = False, withdraw_item: int | None = None,
            quantity: int = 1, dry_run: bool = False, grep: str | None = None) -> None:
        if quantity < 1 or (withdraw_item is not None and not 0 < withdraw_item < 0xfffffffe):
            raise ValueError('Withdrawal item ID and quantity must be positive')
        if grep is not None and not normalize(grep):
            raise ValueError('--grep needs a nonempty item name or item ID')
        if grep is not None and withdraw_item is not None:
            raise ValueError('Use --list --grep to find an ID, then --withdraw ITEM_ID')
        reason = 'completed'
        operation = 'list' if list_only else 'withdraw' if withdraw_item is not None else 'unload'
        log_event(f'Vault {operation} started: preview={dry_run}, item={withdraw_item}, quantity={quantity}, grep={grep!r}')
        try:
            self.checkpoint()
            self.cargo.open()
            self.vault.locate(self.checkpoint)
            if list_only:
                entries = self.entries(grep)
                print('Slot  Item ID  Quantity  Item')
                for entry in entries:
                    print(f'{entry.slot:4}  {entry.item_id:7}  {entry.quantity:8}  {entry.name}')
                print(f'{len(entries)} occupied vault slots.')
                return
            self.capture.open()
            self.capture_active = True
            if self.controller.focus:
                self.controller.focus.release(reason='packet vault unloading')
            if withdraw_item is not None:
                self.withdraw(withdraw_item, quantity, dry_run=dry_run)
                return
            initial = self.cargo_snapshot()
            names = item_names(Path(__file__).resolve().parent) if grep is not None else {}
            matched = 0
            trade_ids = {route.item_id for route in ROUTES.values()}
            for slot in range(self.cargo.capacity()):
                self.checkpoint()
                item = initial[slot]
                if item.item_id in (0, 0xfffffffe, 0xffffffff):
                    continue
                if item.item_id is None or item.quantity is None or item.quantity <= 0:
                    raise RuntimeError(f'Unidentified cargo slot {slot}; no further transfers')
                if grep is not None and not matches_item(grep, item.item_id, names.get(item.item_id, '')):
                    self.skipped += 1
                    continue
                matched += 1
                if item.item_id in trade_ids:
                    self.skipped += 1
                    log_event(f'Vault deposit skipped: item={item.item_id}, cargo_slot={slot}; trade goods')
                    continue
                empty = [target for target, value in self.vault_snapshot().items()
                         if value == CargoItem(0xffffffff, 0)]
                if not empty:
                    reason = 'vault full; remaining hull cargo preserved'
                    break
                if dry_run:
                    log_event(f'Vault eligible cargo: item={item.item_id}, quantity={item.quantity}, cargo_slot={slot}; '
                              f'currently_empty_vault_slots={len(empty)}')
                elif not self.deposit(slot, min(empty), item):
                    self.skipped += 1
            if grep is not None and matched == 0:
                reason = f'no hull items matched {grep!r}; no deposits sent'
        except BaseException as error:
            reason = f'{type(error).__name__}: {error}'
            raise
        finally:
            self.capture_active = False
            try:
                self.capture.close()
            finally:
                self.cargo.close()
                log_event(f'Vault {operation} stopped: moved={self.moved}, skipped={self.skipped}, reason={reason}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('player_config', type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='List vault item IDs, quantities, names and slots; no transfers')
    mode.add_argument('--withdraw', type=int, metavar='ITEM_ID', help='Move this item ID from vault to hull')
    mode.add_argument('--all', action='store_true', help='Explicitly deposit all eligible hull cargo')
    parser.add_argument('--grep', metavar='TEXT', help='Lenient item-name or exact item-ID filter for deposits/listing')
    parser.add_argument('--quantity', type=int, help='Units to withdraw; default 1, one empty hull slot per unit')
    parser.add_argument('--dry-run', action='store_true', help='Preview deposits or withdrawals without transferring')
    args = parser.parse_args(argv)
    if args.quantity is not None and (args.withdraw is None or args.quantity < 1):
        parser.error('--quantity requires --withdraw and must be positive')
    if args.grep is not None and (args.all or args.withdraw is not None or not normalize(args.grep)):
        parser.error('--grep needs a name or ID and cannot be combined with --all or --withdraw')
    if not args.list and args.withdraw is None and not args.all and args.grep is None:
        parser.error('Select hull items with --grep TEXT, or use --all to deposit everything eligible')
    controller: AcController | None = None
    try:
        settings = load_player_config(args.player_config)
        PLAYER.configure(settings)
        configure_event_log(Path(__file__).parent / f'macro_{settings.name}.log', player=settings.name)
        require_admin()
        controller = AcController(WINDOW_TITLE, settings=settings, shared_install=True)
        if controller.instance is None or controller.window is None or controller.startup_popup:
            raise RuntimeError('Log this character into the game before unloading hull cargo')
        session = VendorSession.from_instance(controller.instance)
        VaultController(controller, session).run(list_only=args.list, withdraw_item=args.withdraw,
                                                 quantity=args.quantity or 1, dry_run=args.dry_run, grep=args.grep)
        return 0
    except (KeyboardInterrupt, InterruptedError, StopMacro) as error:
        log_event(f'Vault operation stopped: {error}; no retry')
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        log_event(f'Vault operation failed: {type(error).__name__}: {error}; no retry')
        return 1
    finally:
        if controller is not None:
            controller.close()


if __name__ == '__main__':
    raise SystemExit(main())
