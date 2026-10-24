"""Export public faction definitions/matrix, never private faction_data rows."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
from typing import TypedDict
from build import ROOT
from import_npcs import client_text
from import_sector import number, public_rows


class Faction(TypedDict):
    id: int
    name: str
    description: str
    pda: bool
    pda_text: str
    gain_sound: str


class Relationship(TypedDict):
    source: int
    target: int
    base: float
    current: float
    reward: float


class Catalog(TypedDict):
    factions: list[Faction]
    relationships: list[Relationship]
    unresolved_factions: list[int]


def export_factions(dump: Path, output: Path) -> tuple[int, int]:
    factions: dict[int, Faction] = {}
    relationships: dict[tuple[int, int], Relationship] = {}
    for table, row in public_rows(dump, frozenset({'factions', 'faction_matrix'})):
        if table == 'factions':
            identity = int(number(row, 'faction_id'))
            if identity <= 0 or identity in factions:
                raise ValueError('Duplicate or invalid faction identity')
            factions[identity] = Faction(id=identity, name=client_text(row.get('name')),
                description=client_text(row.get('description')), pda=number(row, 'player_PDA') == 1,
                pda_text=client_text(row.get('PDA_text')), gain_sound=client_text(row.get('faction_gain_sound')))
        else:
            source, target = int(number(row, 'faction_id')), int(number(row, 'faction_entry_id'))
            if (source, target) in relationships:
                raise ValueError('Duplicate faction matrix pair')
            relationship = Relationship(source=source, target=target, base=number(row, 'base_value'),
                current=number(row, 'current_value'), reward=number(row, 'reward_faction'))
            if not all(math.isfinite(relationship[key]) for key in ('base', 'current', 'reward')):
                raise ValueError('Nonfinite faction matrix value')
            relationships[source, target] = relationship
    # The public matrix contains IDs absent from the names table. Native code
    # can still query these numeric relationships; keep them without invented
    # faction names/PDA slots, and make the missing definitions explicit.
    unresolved = sorted({identity for pair in relationships for identity in pair} - factions.keys())
    if sum(faction['pda'] for faction in factions.values()) > 32:
        raise ValueError('Faction PDA exceeds32 native slots')
    catalog = Catalog(factions=[factions[key] for key in sorted(factions)],
                      relationships=[relationships[key] for key in sorted(relationships)],
                      unresolved_factions=unresolved)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, indent=2) + '\n', encoding='utf-8')
    return len(factions), len(relationships)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/factions.json')
    args = parser.parse_args()
    factions, matrix = export_factions(args.dump, args.output)
    print(f'Exported {factions} public factions and {matrix} matrix relationships')


if __name__ == '__main__':
    main()
