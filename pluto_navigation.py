"""Run Pluto's travel milestones through the shared packet destination engine."""
from __future__ import annotations

from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING

from explore_gates import SectorGraph
from navigate_destination import DestinationGraph, DestinationNavigator, normalized
from navigation.stations import StationCatalog
from protocol.client_sector import read_current_sector
from protocol.vendor_trading import VendorSession
from util import log_event
from watchdog import StopMacro

if TYPE_CHECKING:
    from ac import AcController

ROOT = Path(__file__).resolve().parent
SECTORS: dict[str, str] = {
    'earth': 'Earth', 'aba': 'Asteroid Belt Alpha', 'saturn': 'Saturn',
    'uranus': 'Uranus', 'pluto': 'Pluto and Charon', 'akeron': "Akeron's Gate",
    'jupiter': 'Jupiter', 'glenn': 'Glenn', 'carp': 'Carpenter',
    'newed': 'New Edinburgh', 'xipe': 'Xipe Totec', 'swoop': 'Swooping Eagle',
}
STATIONS: dict[str, tuple[str, str]] = {
    'earthS': ('Earth', 'Earth Station'),
    'charonS': ('Pluto and Charon', 'Charon Cloister'),
    'somerS': ('New Edinburgh', 'Somerled Station'),
    'prasadS': ('Xipe Totec', 'Prasad Station'),
}


class PlutoPacketNavigator(DestinationNavigator):
    """Borrow Pluto's controller, focus owner, pause clock and cancellation path."""

    stop_on_private_message: bool = False  # Pluto's existing chat responder owns PM decisions.

    def __init__(self, controller: AcController) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise StopMacro('Pluto packet routes must start on the main thread')
        instance = controller.instance
        if (instance is None or not instance.valid() or instance.pid is None
                or instance.born is None or instance.proxy_pid is None or instance.proxy_born is None):
            raise StopMacro('Packet navigation requires Pluto\'s active owned client/proxy')
        if controller._pause_control is None:
            raise StopMacro('Packet navigation requires Pluto\'s pause controller')
        session = VendorSession.from_processes(instance.pid, instance.born,
                                               instance.proxy_pid, instance.proxy_born)
        name = controller.player.toon_name
        graph = SectorGraph(ROOT / 'navigation/sector_graph.json')
        graph.refresh_peers()
        destinations = DestinationGraph(graph, ROOT / 'navigation/sector_transfers.json',
                                       StationCatalog(ROOT / 'navigation/stations.json'))
        capture = ROOT / f'diagnostics/pluto_navigation_{name}_{time.time_ns()}.jsonl'
        super().__init__(session, name, capture, destinations, control=controller._pause_control)
        self.controller: AcController = controller
        self.owner_thread: int = threading.get_ident()
        self.closed: bool = False

    def active_time(self) -> float:
        return float(self.controller.active_time())

    def checkpoint(self) -> None:
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError('Pluto packet navigation must run on its main thread')
        if self.closed or self.stop_requested.is_set() or self.stop.exists():
            raise StopMacro('Pluto packet navigation stopped')
        # Use the existing controller's pause/chat/watchdog boundary. A queued
        # restart calls controller.terminate(), which closes this capture first.
        self.controller._yield(.01)
        if self.closed:
            raise StopMacro('Pluto packet navigation closed during a controller interrupt')
        self.session.check()
        self.poll()

    def travel(self, current: str, target: str) -> None:
        if current not in SECTORS:
            raise StopMacro(f'Packet navigation cannot depart Pluto position {current!r}; undock first')
        destination = STATIONS[target][1] if target in STATIONS else SECTORS.get(target)
        if destination is None:
            raise StopMacro(f'Unknown Pluto packet destination: {target}')
        source = read_current_sector(self.session.client_pid, self.session.client_born, self.session.game_id)
        if normalized(source) != normalized(SECTORS[current]):
            raise StopMacro(f'Pluto position {current} differs from live sector {source}; no navigation sent')
        self.output = self.capture_path.open('a', encoding='utf-8')
        self.receiver.open()
        self.world.sector = source
        self.checkpoint()
        route = self.destination_graph.route(source, destination)
        log_event('Navigation plan: ' + ' -> '.join([source, *(leg.destination for leg in route)]))
        for leg in route:
            self.transfer(leg)
            if leg.action == 7:
                position = target
            else:
                position = next((alias for alias, sector in SECTORS.items()
                                 if normalized(sector) == normalized(leg.destination)), leg.destination)
            self.controller.player.set_pos(position)
        if not route:
            self.controller.player.set_pos(target, progress=False)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.stop_requested.set()
        self.receiver.close()
        self.cargo.close()
        self.stream.close()
        if self.output is not None:
            self.output.close()
        self.graph.save()
        # Focus, PauseControl and the job monitor belong to AcController.
