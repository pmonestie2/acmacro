"""One supervised Callipus M2 batch: fill the hull, then sell only item 3199."""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import struct
import re

from ac import AcController
from player import PLAYER, load_player_config
from protocol import proxy_commands
from protocol.vendor_trading import VendorRoute, VendorSession, VendorTrader
from util import configure_event_log, log_event

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / 'diagnostics/je_manufacture_batch.json'
ITEM = 3199


@dataclass(frozen=True)
class BuildState:
    products: int
    free: int


class ManufactureBatch:
    def __init__(self, trader: VendorTrader, *, output: Path | None = None, parallel: int = 1) -> None:
        if parallel < 1:
            raise ValueError('parallel must be a positive integer')
        self.trader = trader
        self.built: int = 0
        self.sold: int = 0
        self.pending: BuildState | None = None
        self.pending_count: int = 1
        self.pending_submitted: int = 0
        self.parallel = parallel
        self.sale_parallel: int = max(1, parallel // 2)
        self.recipe_selected: bool = False
        self.output = output if output is not None else OUTPUT
        self.report: dict[str, object] = {'state': 'starting', 'item_id': ITEM, 'sell_npc': 36,
                                         'manufacture_parallel': parallel, 'sale_parallel': self.sale_parallel}

    def save(self, state: str) -> None:
        self.report.update(state=state, built=self.built, sold=self.sold)
        self.output.write_text(json.dumps(self.report, indent=2), encoding='utf-8')

    def snapshot(self) -> BuildState:
        trader = self.trader
        trader.read_cargo()
        trader.cargo.check()
        capacity = trader.inventory.capacity()
        slots = [value for slot, value in trader.cargo.slots.items() if slot < capacity]
        return BuildState(sum(value.quantity or 0 for value in slots if value.item_id == ITEM),
                          trader.cargo.free_slots(capacity))

    def before_build(self) -> None:
        self.trader.checkpoint()
        current = self.snapshot()
        if self.pending is None:
            raise RuntimeError('Manufacture cargo changed before submission; no build sent')
        created = current.products - self.pending.products
        if not 0 <= created <= self.pending_submitted:
            raise RuntimeError('Manufacture cargo changed before submission; no build sent')
        if current.free < self.pending_count - created:
            raise RuntimeError('Insufficient cargo space for pending builds; no build sent')

    def build_one(self) -> None:
        self.build_many(1)

    def select_recipe(self) -> None:
        trader = self.trader
        entity = trader.session.game_id & 0xffffff
        trader.capture.read()
        trader.send(0x7b, struct.pack('>II', entity, ITEM))
        deadline = trader.controller.active_time() + 10
        while True:
            trader.checkpoint()
            replies = trader.capture.read(.05)
            if any(reply.opcode == 0x1d and b'Callipus M2 requires:' in reply.body for reply in replies):
                self.recipe_selected = True
                break
            if trader.controller.active_time() >= deadline:
                raise TimeoutError('Recipe selection unconfirmed; no build sent')

    def build_many(self, count: int) -> None:
        if count < 1:
            raise ValueError('Build count must be a positive integer')
        trader = self.trader
        entity = trader.session.game_id & 0xffffff
        if not self.recipe_selected:
            self.select_recipe()
        before = self.snapshot()
        self.pending = before
        self.pending_count = count
        self.pending_submitted = 0
        self.report.update(requested_in_flight=count, submitted_in_flight=0, observed_created=0)
        self.save('build_submitting')
        try:
            for submitted in range(count):
                report: dict[str, object] = {}
                self.before_build()
                proxy_commands.submit(trader.session.proxy_pid, report=report,
                    frame=struct.pack('<HH', 12, 0x7e) + struct.pack('>II', entity, 2),
                    before_submit=self.before_build)
                if report.get('state') != 'handler_returned':
                    raise RuntimeError(f'Uncertain manufacture submission: {report.get("state")}; no retry')
                self.pending_submitted = submitted + 1
                self.report['submitted_in_flight'] = submitted + 1
                self.save('awaiting_creation')
        finally:
            self.pending = None
        deadline = trader.controller.active_time() + 15
        while True:
            trader.checkpoint()
            trader.capture.read(.05)
            after = self.snapshot()
            created = after.products - before.products
            self.report['observed_created'] = created
            if created == count:
                self.built += created
                self.report['products_in_hull'] = after.products
                self.report['free_slots'] = after.free
                self.save('manufacturing')
                log_event(f'Callipus build confirmed: built={self.built}, hull_products={after.products}, free_slots={after.free}')
                return
            if trader.controller.active_time() >= deadline:
                self.built += max(0, created)
                raise TimeoutError(f'Manufacture creation incomplete: requested={count}, created={created}; no retry and no sale')

    def run(self) -> dict[str, object]:
        trader = self.trader
        try:
            trader.checkpoint()
            trader.inventory.open()
            trader.capture.open()
            trader.capture_active = True
            if trader.controller.focus:
                trader.controller.focus.release(reason='packet manufacturing batch')
            self.save('manufacturing')
            log_event('Callipus batch started: fill hull, then sell item=3199 to npc=36; one batch')
            while (free := self.snapshot().free) > 0:
                if self.parallel == 1:
                    self.build_one()
                else:
                    self.build_many(min(self.parallel, free))
            full = self.snapshot()
            if full.free != 0 or full.products <= 0:
                raise RuntimeError('Hull-full sale condition not met; no sales sent')
            self.save('hull_full_opening_seller')
            trader.send(0x7e, struct.pack('>II', trader.session.game_id & 0xffffff, 0))
            trader.close_trade()
            trader.open_trade(36, b'I stock level II components')
            self.sell_products()
            trader.close_trade()
            remaining = self.snapshot()
            if remaining.products:
                raise RuntimeError('Callipus cargo remains after sales; no retry')
            self.report.update(products_in_hull=remaining.products, free_slots=remaining.free)
            self.save('completed')
        except BaseException as error:
            self.report['reason'] = f'{type(error).__name__}: {error}; no retry'
            self.save('stopped')
        finally:
            trader.capture_active = False
            try:
                trader.capture.close()
            finally:
                trader.inventory.close()
                log_event(f'Callipus batch stopped: built={self.built}, sold={self.sold}, '
                          f'reason={self.report.get("reason", self.report["state"])}')
        return self.report

    def sale_confirmed(self, slot: int) -> None:
        self.sold += 1
        self.report['last_sold_slot'] = slot
        self.save('selling')

    def sell_products(self) -> None:
        trader = self.trader
        trader.read_cargo()
        remaining = trader.cargo.matching(ITEM)
        while remaining:
            group = dict(sorted(remaining.items(), reverse=True)[:self.sale_parallel])
            if self.sale_parallel == 1:
                for slot, quantity in group.items():
                    trader.sell_one(slot, quantity)
                    self.sale_confirmed(slot)
            else:
                trader.sell_many(group, on_confirmed=self.sale_confirmed)
            # Only one sale per slot was in flight, even for stacked cargo.
            for slot, quantity in group.items():
                if quantity == 1:
                    del remaining[slot]
                else:
                    remaining[slot] = quantity - 1


def run_window(window_name: str, *, batches: int = 1, parallel: int = 1,
               expected_client: tuple[int, str] | None = None) -> dict[str, object]:
    from gui import find_window_title
    from focus import process_identity
    import win32process
    if not re.fullmatch(r'ENB_[A-Za-z0-9_-]+', window_name):
        raise ValueError('Specify the exact window title, for example ENB_macboyje')
    if batches < 0:
        raise ValueError('batches must be nonnegative; zero means continuous')
    if parallel < 1:
        raise ValueError('parallel must be a positive integer')
    settings = load_player_config(ROOT / f'config_player_{window_name[4:].casefold()}.txt')
    window = find_window_title(window_name)
    if not window:
        raise RuntimeError('Requested character window is missing; no game started')
    pid = win32process.GetWindowThreadProcessId(window)[1]
    born = process_identity(pid)
    PLAYER.configure(settings)
    configure_event_log(ROOT / f'macro_{settings.name}.log', player=settings.name)
    controller = AcController(window_name, settings=settings, shared_install=True)
    try:
        controller.station_lock = False
        if controller.instance is None or controller.window is None or controller.startup_popup:
            raise RuntimeError('Character must already be logged in with its manufacture terminal open')
        session = VendorSession.from_instance(controller.instance)
        if controller.window != window or session.client_pid != pid or session.client_born != born:
            raise RuntimeError('Requested character window identity changed; no submissions')
        if expected_client is not None and (session.client_pid, session.client_born) != expected_client:
            raise RuntimeError('JE changed since the authorized manufacture capture; no submissions')
        completed = total_built = total_sold = 0
        output = (OUTPUT if expected_client is not None else
                  ROOT / f'diagnostics/manufacture_{settings.name}.json')
        while True:
            controller._focus_wait()
            session.check()
            trader = VendorTrader(controller, session, VendorRoute(36, 36, 0, b'', b'I stock level II components', ITEM), 1, 1)
            batch = ManufactureBatch(trader, output=output, parallel=parallel)
            batch.report.update(batch=completed + 1, target_batches=batches or 'continuous')
            report = batch.run()
            total_built += batch.built
            total_sold += batch.sold
            if report['state'] == 'completed':
                completed += 1
            report.update(completed_batches=completed, total_built=total_built, total_sold=total_sold)
            output.write_text(json.dumps(report, indent=2), encoding='utf-8')
            if report['state'] != 'completed' or (batches and completed >= batches):
                return report
    finally:
        controller.close()


def run_je(*, parallel: int = 1) -> dict[str, object]:
    expected = json.loads((ROOT / 'diagnostics/je_manufacture_current.json').read_text())
    return run_window('ENB_macboyje', parallel=parallel, expected_client=(expected['client_pid'], expected['client_born']))


def main(argv: list[str] | None = None) -> int:
    from chatproc import require_admin
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('window', help='Exact character window title; matching player config required')
    parser.add_argument('--batches', type=int, default=1, help='Full-hull batches; default 1, 0 repeats until stopped')
    parser.add_argument('--parallel', type=int, default=1, metavar='N',
                        help='Maximum builds in flight; positive integer, capped by free hull slots')
    args = parser.parse_args(argv)
    try:
        require_admin()
        report = run_window(args.window, batches=args.batches, parallel=args.parallel)
        print(json.dumps(report, indent=2))
        return 0 if report['state'] == 'completed' else 1
    except (OSError, ValueError, RuntimeError, InterruptedError, KeyboardInterrupt) as error:
        log_event(f'Manufacturing stopped: {type(error).__name__}: {error}; no retry')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
