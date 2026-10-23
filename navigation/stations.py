"""Sector-scoped station identities observed in server world packets."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from macro_control import atomic_json, request_lock


@dataclass(frozen=True)
class Station:
    sector: str
    name: str
    item_id: int
    observed_at: float
    evidence: str
    raw_xyz: tuple[float, float, float] | None = None

    @property
    def label(self) -> str:
        return f'{self.name} ({self.sector})'


class StationCatalog:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.stations: dict[tuple[str, str], Station] = {}
        if path is not None and path.exists():
            self.merge_file(path)

    def merge_file(self, path: Path) -> None:
        for row in json.loads(path.read_text(encoding='utf-8'))['stations']:
            xyz = row.get('raw_xyz')
            position = (float(xyz[0]), float(xyz[1]), float(xyz[2])) if xyz is not None else None
            station = Station(str(row['sector']), str(row['name']), int(row['item_id']),
                              float(row['observed_at']), str(row['evidence']),
                              position)
            self.record(station)

    def record(self, station: Station) -> bool:
        if not station.sector or not station.name or not 0 < station.item_id < 0xffffffff:
            raise ValueError('Station must have a sector, name, and valid local object ID')
        key = station.sector, station.name
        previous = self.stations.get(key)
        if previous is not None and previous.observed_at >= station.observed_at:
            return False
        self.stations[key] = station
        return True

    def observe(self, objects: Iterable[Mapping[str, object]], evidence: str) -> list[Station]:
        added: list[Station] = []
        for obj in objects:
            if obj.get('object_type') != 12:
                continue
            name, sector, identity = obj.get('name'), obj.get('sector'), obj.get('id')
            observed = obj.get('position_seen', obj.get('first_seen'))
            if not (isinstance(name, str) and isinstance(sector, str) and isinstance(identity, str)
                    and isinstance(observed, (float, int))):
                continue
            xyz = obj.get('raw_xyz')
            position: tuple[float, float, float] | None = None
            if isinstance(xyz, (list, tuple)) and len(xyz) == 3 and all(isinstance(v, (float, int)) for v in xyz):
                position = float(xyz[0]), float(xyz[1]), float(xyz[2])
            station = Station(sector, name, int(identity, 16), float(observed), evidence, position)
            if self.record(station):
                added.append(station)
        return added

    def labels(self) -> set[str]:
        return {station.label for station in self.stations.values()}

    def by_label(self, label: str) -> Station:
        return next(station for station in self.stations.values() if station.label == label)

    def save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with request_lock(self.path.parent / 'station_catalog_lock'):
            if self.path.exists():
                self.merge_file(self.path)
            atomic_json(self.path, {'stations': [asdict(station) for station in
                        sorted(self.stations.values(), key=lambda row: (row.sector, row.name))]})
