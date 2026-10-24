"""Export the archived public space-sector maps for gates and wormholes.

Static objects and public MOB spawns are included. Three sectors lack a backdrop asset;
their exported records explicitly use the sandbox skybox fallback (220).
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unicodedata

from build import ROOT
from import_sector import export_sector, number, public_rows
from import_spawns import PublicSpawns
from import_resources import PublicResources


def export_universe(dump: Path, output: Path, *, default_sector_output: Path | None = None) -> int:
    ids = sorted(int(number(row, 'sector_id')) for table, row in public_rows(dump, frozenset({'sectors'}))
                 if 0 < number(row, 'sector_id') < 10000)
    sectors: list[object] = []
    spawns = PublicSpawns(dump)
    resources = PublicResources(dump)
    with tempfile.TemporaryDirectory(prefix='enb-public-maps-') as folder:
        path = Path(folder) / 'sector.json'
        for index, sector_id in enumerate(ids):
            with redirect_stdout(io.StringIO()):
                export_sector(dump, sector_id, path)
            sector = json.loads(path.read_text())
            sector['mobs'] = spawns.for_sector(sector_id)
            sector['resources'] = resources.for_sector(sector_id)
            # Preserve unresolved model references for maintainers. Asset zero
            # itself is valid; a missing template/model relationship is not.
            referenced = {mob for obj in spawns.objects[sector_id]
                          for mob in spawns.groups.get(int(number(obj, 'sector_object_id')), [])}
            sector['missing_mob_models'] = [
                {'template_id': identity, 'name': spawns.missing_models[identity]}
                for identity in sorted(referenced & spawns.missing_models.keys())]
            for nav in sector['navpoints'] + sector['mobs']:
                if not nav['name'].isascii():
                    nav['original_name'] = nav['name']
                    nav['name'] = unicodedata.normalize('NFKD', nav['name']).encode('ascii', 'ignore').decode('ascii')
            if not sector['backdrop_asset']:
                sector['backdrop_asset'] = 220
                sector['backdrop_fallback'] = True
            sector['source'] = 'Archived net7 public sector, static objects, MOB spawn rules and resource fields'
            if sector_id == 1076 and default_sector_output is not None:
                default_sector_output.parent.mkdir(parents=True, exist_ok=True)
                default_sector_output.write_text(json.dumps(sector, indent=2) + '\n', encoding='utf8')
            sectors.append(sector)
            if index % 20 == 0:
                print(f'Exported map {index + 1}/{len(ids)}: {sector["name"]}', flush=True)
    output.write_text(json.dumps(sectors, separators=(',', ':')) + '\n')
    return len(sectors)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/universe.json')
    parser.add_argument('--default-sector-output', type=Path,
                        help='Also export the complete Alpha map (including MOBs/resources) for --sector-file')
    args = parser.parse_args()
    print(f'Exported {export_universe(args.dump, args.output, default_sector_output=args.default_sector_output)} space sectors')


if __name__ == '__main__':
    main()
