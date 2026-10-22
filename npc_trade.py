"""Search local NPC/item names, or open a known NPC on an exact game window."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import struct
from typing import TypedDict, cast
from text_search import matches, normalize

ROOT = Path(__file__).resolve().parent


class ReplyData(TypedDict):
    selection: int
    text: str


class DialogData(TypedDict):
    text: str
    replies: list[ReplyData]


class ItemData(TypedDict):
    name: str


class VendorData(TypedDict):
    npc_id: int
    items: list[ItemData]
    dialog: DialogData


class CatalogData(TypedDict):
    npcs: list[VendorData]


@dataclass
class NpcRecord:
    npc_id: int
    name: str = ''
    items: list[str] = field(default_factory=list)
    greeting: str = ''
    trade_choice: int | None = None
    trade_text: str = ''
    name_source: str = ''


def trade_choice(replies: list[ReplyData]) -> int | None:
    for reply in replies:
        if re.search(r'\b(trade|what do you have|show me|take a look)\b', reply['text'], re.I):
            return reply['selection']
    return None


class NpcCatalog:
    def __init__(self, root: Path = ROOT) -> None:
        self.records: dict[int, NpcRecord] = {}
        raw = cast(CatalogData, json.loads((root / 'protocol/npc_vendor_catalog.json').read_text(encoding='utf-8')))
        for vendor in raw['npcs']:
            dialog = vendor.get('dialog', {'text': '', 'replies': []})
            items = [item['name'] for item in vendor['items'] if item.get('name')]
            choice = trade_choice(dialog['replies'])
            # Older replies sometimes say only "Sure thing" or "Yes".
            if items and choice is None:
                choice = 3 if vendor['npc_id'] in (599, 600) else 2
            self.records[vendor['npc_id']] = NpcRecord(vendor['npc_id'], items=items,
                greeting=dialog['text'], trade_choice=choice if items else None,
                trade_text=next((reply['text'] for reply in dialog['replies']
                                 if reply['selection'] == choice), ''))
        sql = root / 'diagnostics/npc_source/sigseg1v_db_mysql_net7.sql'
        if sql.exists():
            with sql.open(encoding='utf-8', errors='replace') as stream:
                for line in stream:
                    if not line.startswith('INSERT INTO `starbase_npcs` VALUES'):
                        continue
                    values = re.findall(r"'((?:\\.|[^'])*)'", line)[:3]
                    if len(values) != 3 or not values[0].isdigit():
                        continue
                    npc_id = int(values[0])
                    record = self.records.setdefault(npc_id, NpcRecord(npc_id))
                    record.name = ' '.join(part.replace("\\'", "'") for part in values[1:] if part)
                    record.name_source = 'server SQL'
        interactions = root / 'protocol/npc_interaction_ids.json'
        if interactions.exists():
            data = json.loads(interactions.read_text(encoding='utf-8'))
            for key, row in data['station_npcs'].items():
                record = self.records.setdefault(int(key), NpcRecord(int(key)))
                record.name, record.name_source = row['name'], 'recorded interaction'
        # Historical lounge roster IDs belong to another namespace; never merge them.

    def search(self, query: str) -> list[NpcRecord]:
        return sorted((record for record in self.records.values()
                       if matches(query, record.name) or
                       any(matches(query, item) for item in record.items)), key=lambda record: record.npc_id)

    def require(self, npc_id: int) -> NpcRecord:
        try:
            return self.records[npc_id]
        except KeyError:
            raise ValueError(f'NPC {npc_id} is absent from the local catalog') from None


def open_npc(window_name: str, record: NpcRecord, *, dialogue_only: bool = False) -> None:
    from ac import AcController
    from arrange_windows import client_path
    from chatproc import require_admin
    from focus import SharedFocus, process_identity
    from game_instance import parent_process_id
    from gui import find_window_title
    from macro_control import PauseControl
    from player import PLAYER
    from protocol.dialog_capture import decode_talk_tree
    from protocol.proxy_commands import SUPPORTED_VENDOR_NPC_IDS
    from protocol.vendor_trading import VendorRoute, VendorSession, VendorTrader
    from util import configure_event_log, log_event
    import win32process

    if not window_name.startswith('ENB_') or not re.fullmatch(r'[A-Za-z0-9_-]+', window_name[4:]):
        raise ValueError('Use the exact character window title, for example ENB_macboytt')
    if record.npc_id not in SUPPORTED_VENDOR_NPC_IDS:
        raise ValueError(f'NPC {record.npc_id} is not enabled by the existing packet guard')
    require_admin()
    window = find_window_title(window_name)
    if not window:
        raise RuntimeError(f'No game window titled {window_name}; no game started')
    pid = win32process.GetWindowThreadProcessId(window)[1]
    born = process_identity(pid)
    if client_path(pid).name.casefold() != 'client.exe':
        raise RuntimeError('The requested window does not belong to client.exe')
    proxy_pid = parent_process_id(pid)
    if proxy_pid is None or client_path(proxy_pid).name.casefold() != 'net7proxy.exe':
        raise RuntimeError('The requested game has no identifiable Net-7 proxy')
    proxy_born = process_identity(proxy_pid)
    if born is None or proxy_born is None or proxy_born >= born:
        raise RuntimeError('Client/proxy birth identities are unavailable or inconsistent')
    name = window_name[4:]
    PLAYER.toon_name = name
    configure_event_log(ROOT / f'macro_{name}.log', player=name)
    controller: AcController | None = None
    trader: VendorTrader | None = None
    reason = 'completed'
    try:
        controller = AcController(window_name)
        controller.focus = SharedFocus(f'named-window:{pid}', name, on_wait=controller._focus_wait)
        controller._pause_control = PauseControl(name)
        controller.station_lock = False
        if (controller.window != window or process_identity(pid) != born or
                parent_process_id(pid) != proxy_pid):
            raise RuntimeError('Exact game window identity changed; no packets sent')
        session = VendorSession.from_processes(pid, born, proxy_pid, proxy_born)
        route = VendorRoute(record.npc_id, record.npc_id, 0, b'', b'', 0)
        trader = VendorTrader(controller, session, route, 1, 1)
        trader.capture.open()
        trader.capture_active = True
        if controller.focus:
            controller.focus.release(reason='open NPC trade')
        log_event(f'NPC opening started: npc={record.npc_id}, name={record.name}, window={window_name}')
        trader.close_trade()
        trader.capture.read()
        trader.send(0x4e, struct.pack('<IIB', session.game_id & 0xffffff, record.npc_id, 4))
        deadline = controller.active_time() + 10
        dialog: DialogData | None = None
        while dialog is None:
            trader.checkpoint()
            if controller.active_time() >= deadline:
                raise TimeoutError('NPC greeting timed out; no retry')
            for reply in trader.capture.read(.05):
                if reply.opcode == 0x54:
                    dialog = cast(DialogData, decode_talk_tree(reply.body))
                    break
        print(dialog['text'])
        choice = record.trade_choice if record.trade_choice is not None else trade_choice(dialog['replies'])
        if dialogue_only or choice is None:
            log_event(f'NPC dialogue acknowledged: npc={record.npc_id}; no trade selection sent')
            print('Dialogue acknowledged; left open without selecting a reply.')
            return
        current_text = next((reply['text'] for reply in dialog['replies']
                             if reply['selection'] == choice), None)
        if current_text is None or (record.trade_choice is not None and
                                   normalize(current_text) != normalize(record.trade_text)):
            raise RuntimeError('Recorded trade choice differs from the current greeting; dialogue left open')
        trader.capture.read()
        trader.send(0x55, struct.pack('<IB', session.game_id & 0xffffff, choice))
        trader.wait_reply(0x56, struct.pack('<i', 3), exact=True, context=f'NPC {record.npc_id} trade')
        log_event(f'NPC trade acknowledged: npc={record.npc_id}; left open')
        print(f'Trade acknowledged on {window_name}: NPC {record.npc_id}. Left open; UI visibility not verified.')
    except BaseException as error:
        reason = f'{type(error).__name__}: {error}'
        raise
    finally:
        try:
            if trader is not None:
                trader.capture_active = False
                trader.capture.close()
        finally:
            try:
                if controller is not None:
                    controller.close()
            finally:
                log_event(f'NPC opening stopped: npc={record.npc_id}, reason={reason}')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('window', nargs='?', help='Exact window title, e.g. ENB_macboytt')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--search', '--grep', metavar='TEXT', help='Lenient NPC-name and item-name search; sends no packets')
    mode.add_argument('--id', type=int, metavar='NPC_ID', help='Open this NPC on the specified window')
    parser.add_argument('--dialogue', action='store_true', help='Leave the greeting open without selecting trade')
    args = parser.parse_args(argv)
    if args.id is not None and not args.window:
        parser.error('--id requires an exact window title')
    try:
        catalog = NpcCatalog()
        if args.search is not None:
            found = catalog.search(args.search)
            for record in found:
                print(f'{record.npc_id:4}  {record.name or "(name unknown)"}  '
                      f'[{"vendor" if record.items else "dialogue"}; {record.name_source or "captured inventory"}]')
                for item in record.items:
                    if matches(args.search, item):
                        print(f'      {item}')
            print(f'{len(found)} matching NPC(s).')
            return 0
        open_npc(args.window, catalog.require(args.id), dialogue_only=args.dialogue)
        return 0
    except (OSError, ValueError, RuntimeError, InterruptedError) as error:
        print(f'NPC open/search failed: {error}; no retry')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
