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
from protocol.item_definitions import replay_item_definition
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


@dataclass(frozen=True)
class VaultWithdrawal:
    source: int
    target: int
    item: CargoItem
    quantity: int


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
        self.pending_withdrawal: VaultWithdrawal | None = None
        self.moved: int = 0
        self.skipped: int = 0
        self.loaded_definitions: set[int] = set()

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

    def ensure_item_definition(self, item_id: int) -> None:
        if item_id in self.loaded_definitions:
            return
        self.checkpoint()
        log_event(f'Vault item-definition replay started: item={item_id}; local proxy-to-client')
        replay_item_definition(self.session, item_id, before_submit=self.checkpoint)
        self.loaded_definitions.add(item_id)
        log_event(f'Vault item-definition replay completed: item={item_id}; local handler returned')

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
            self.ensure_item_definition(item.item_id)
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
        source, target, item = (self.pending_withdrawal.source, self.pending_withdrawal.target,
                                self.pending_withdrawal.item)
        if item.quantity is None or not 1 <= self.pending_withdrawal.quantity <= item.quantity:
            raise RuntimeError('Withdrawal quantity exceeds the verified source stack; no transfer sent')
        if self.vault_snapshot().get(source) != item:
            raise RuntimeError('Vault source changed; no withdrawal sent')
        if self.cargo_snapshot().get(target) != CargoItem(0xffffffff, 0):
            raise RuntimeError('Cargo destination is no longer empty; no withdrawal sent')

    def withdraw_stack(self, source: int, target: int, item: CargoItem, quantity: int) -> None:
        if item.item_id is None or item.quantity is None or item.quantity <= 0:
            raise ValueError('Withdrawal requires a known item and positive source quantity')
        if not 1 <= quantity <= min(item.quantity, 0x7fffffff):
            raise ValueError('Withdrawal quantity must be positive and no larger than its source stack')
        self.capture.read()
        self.pending_withdrawal = VaultWithdrawal(source, target, item, quantity)
        report: dict[str, object] = {}
        frame = struct.pack('<HH', 28, 0x27) + struct.pack('>6I', self.session.game_id, 3, source, 1, target, quantity)
        try:
            self.before_withdraw()
            self.ensure_item_definition(item.item_id)
            proxy_commands.submit(self.session.proxy_pid, report=report,
                                  frame=frame, before_submit=self.before_withdraw)
        finally:
            self.pending_withdrawal = None
        if report.get('state') != 'handler_returned':
            raise RuntimeError(f'Uncertain withdrawal submission: {report.get("state")}; no retry')
        expected = (CargoItem(item.item_id, item.quantity - quantity)
                    if item.quantity > quantity else CargoItem(0xffffffff, 0))
        deadline = self.controller.active_time() + 10
        while True:
            self.checkpoint()
            self.capture.read(.05)
            if (self.cargo_snapshot().get(target) == CargoItem(item.item_id, quantity)
                    and self.vault_snapshot().get(source) == expected):
                self.moved += quantity
                log_event(f'Vault withdrawal confirmed: item={item.item_id}, quantity={quantity}, '
                          f'vault_slot={source}, cargo_slot={target}, moved_items={self.moved}')
                return
            if self.controller.active_time() >= deadline:
                raise TimeoutError(f'Vault withdrawal unconfirmed: item={item.item_id}, '
                                   f'vault_slot={source}, cargo_slot={target}; no retry')

    def withdraw(self, item_id: int, quantity: int, *, dry_run: bool = False,
                 whole_stack: bool = False) -> None:
        if not 0 < item_id < 0xfffffffe or not 1 <= quantity <= 0x7fffffff:
            raise ValueError('Withdrawal item ID and quantity must be positive and bounded')
        candidates = [(slot, item) for slot, item in sorted(self.vault_snapshot().items())
                      if item.item_id == item_id and (item.quantity or 0) > 0]
        available = sum(item.quantity or 0 for _, item in candidates)
        if whole_stack and candidates:
            quantity = candidates[0][1].quantity or 0
        if available < quantity:
            raise ValueError(f'Vault has {available} of item {item_id}; requested {quantity}; no transfers sent')
        # Preserve source stacks: each packet moves a whole or partial stack to one empty slot.
        chunks: list[tuple[int, CargoItem, int]] = []
        remaining = quantity
        for source, item in candidates:
            count = min(remaining, item.quantity or 0)
            chunks.append((source, item, count))
            remaining -= count
            if not remaining:
                break
        capacity = self.cargo.capacity()
        empty = sorted(slot for slot, item in self.cargo_snapshot().items()
                       if slot < capacity and item == CargoItem(0xffffffff, 0))
        if len(empty) < len(chunks):
            raise ValueError(f'Need {len(chunks)} empty cargo slots; found {len(empty)}; no transfers sent')
        if dry_run:
            log_event(f'Vault withdrawal preview: item={item_id}, quantity={quantity}, '
                      f'stacks={len(chunks)}, available={available}')
            return
        for target, (source, item, count) in zip(empty, chunks):
            self.withdraw_stack(source, target, item, count)

    def run(self, *, list_only: bool = False, withdraw_item: int | None = None,
            quantity: int = 1, dry_run: bool = False, grep: str | None = None,
            whole_stack: bool = False) -> None:
        if quantity < 1 or (withdraw_item is not None and not 0 < withdraw_item < 0xfffffffe):
            raise ValueError('Withdrawal item ID and quantity must be positive')
        if whole_stack and withdraw_item is None:
            raise ValueError('--stack requires --withdraw')
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
                self.withdraw(withdraw_item, quantity, dry_run=dry_run, whole_stack=whole_stack)
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
    parser = argparse.ArgumentParser(
        description='List vault contents or move items between hull and vault on an already logged-in '
                    'character, including in space.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='Examples:\n'
               '  vault .\\mbboyje.conf --list\n'
               '  vault .\\mbboyje.conf --list --grep unicorn\n'
               '  vault .\\mbboyje.conf --grep "white dragon" --dry-run\n'
               '  vault .\\mbboyje.conf --grep "white dragon"\n'
               '  vault .\\mbboyje.conf --withdraw 3302 --quantity 2\n'
               '  vault .\\mbboyje.conf --withdraw 2838 --stack\n'
               '  vault .\\mbboyje.conf --all\n\n'
               'Choose one operation: --list, --withdraw, --grep (deposit), or --all.')
    parser.add_argument('player_config', type=Path, help='Player config path, e.g. .\\mbboyje.conf; character must already be logged in')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--list', action='store_true', help='Show vault slot, item ID, quantity and name; moves nothing. Add --grep to filter')
    mode.add_argument('--withdraw', type=int, metavar='ITEM_ID', help='Move an item from vault to hull by numeric ID; default 1 unit. Find IDs with --list')
    mode.add_argument('--all', action='store_true', help='Deposit all eligible hull stacks; excludes known route trade goods. Cannot combine with --grep')
    parser.add_argument('--grep', metavar='TEXT', help='Deposit matching hull stacks, or filter --list. Matches approximate names or an exact numeric ID; quote multiword names')
    amounts = parser.add_mutually_exclusive_group()
    amounts.add_argument('--quantity', type=int, metavar='N', help='Units to withdraw (default: 1); transfers whole or partial source stacks into empty hull slots')
    amounts.add_argument('--stack', action='store_true', help='Withdraw the entire first matching vault stack into one empty hull slot; requires --withdraw')
    parser.add_argument('--dry-run', action='store_true', help='Preview deposits or withdrawals without transferring')
    args = parser.parse_args(argv)
    if args.stack and args.withdraw is None:
        parser.error('--stack requires --withdraw')
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
                                                 quantity=args.quantity or 1, dry_run=args.dry_run, grep=args.grep, whole_stack=args.stack)
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
