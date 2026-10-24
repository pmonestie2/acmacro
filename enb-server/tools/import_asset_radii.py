"""Export numeric native asset radii from the installed client's cbasset.ini.

Only BASE identity and Radius are exported. Mounts, textures and other assets
are not copied. Historical C++ parses Radius from cbasset.xml; equivalence to
that unavailable XML is not assumed. RadiusAdjust is deliberately ignored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import TypedDict

from build import ROOT


class RadiiDocument(TypedDict):
    version: int
    source: str
    sha256: str
    radii: dict[str, float]
    resource_overrides: dict[str, float]


# Active Resource::SetBasset and Husk::SetBasset cases, independently of scale.
RESOURCE_OVERRIDES = {**dict.fromkeys(range(1822, 1825), 800.),
                      **dict.fromkeys(range(1825, 1828), 630.),
                      **dict.fromkeys(range(1828, 1831), 510.),
                      **dict.fromkeys(range(1831, 1834), 750.), 1834: 0.}


def valid_radius(value: object) -> bool:
    return (isinstance(value, (float, int)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 2_000_000)


def parse_radii(source: str) -> dict[int, float]:
    radii: dict[int, float] = {}
    asset: int | None = None
    for line_number, raw in enumerate(source.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith((';', '#')):
            continue
        if line.startswith('['):
            match = re.fullmatch(r'\[BASE-(\d+)\]', line)
            asset = int(match[1]) if match else None
            if asset is not None and not 0 <= asset <= 65535:
                raise ValueError(f'Asset ID exceeds native width at line {line_number}')
        elif line.partition('=')[0].strip().casefold() == 'radius':
            if asset is None:
                raise ValueError(f'Radius without a BASE section at line {line_number}')
            value = float(line.partition('=')[2].strip())
            if not valid_radius(value):
                raise ValueError(f'Invalid asset radius at line {line_number}')
            if asset in radii and radii[asset] != value:
                raise ValueError(f'Conflicting radius for asset {asset}')
            radii[asset] = value
    if not radii:
        raise ValueError('No native asset radii found')
    return dict(sorted(radii.items()))


def radius_map(values: object) -> dict[int, float]:
    if not isinstance(values, dict) or not values:
        raise ValueError('Asset radii document has no radii')
    radii: dict[int, float] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal():
            raise ValueError('Invalid asset radius identity')
        asset = int(key)
        if not 0 <= asset <= 65535 or asset in radii or not valid_radius(value):
            raise ValueError(f'Invalid or duplicate asset radius: {key}')
        radii[asset] = float(value)
    return radii


def load_radii(path: Path) -> dict[int, float]:
    raw: object = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(raw, dict) or raw.get('version') != 1:
        raise ValueError('Unsupported asset radii document')
    return radius_map(raw.get('radii'))


def load_resource_radii(path: Path) -> dict[int, float]:
    radii = load_radii(path)
    raw: object = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(raw, dict):
        raise ValueError('Invalid asset radii document')
    radii.update(radius_map(raw.get('resource_overrides')))
    return radii


def export(source: Path, output: Path) -> None:
    content = source.read_bytes()
    radii = parse_radii(content.decode('utf-8-sig'))
    document: RadiiDocument = dict(version=1, source=source.name,
                                  sha256=hashlib.sha256(content).hexdigest(),
                                  radii={str(asset): radius for asset, radius in radii.items()},
                                  resource_overrides={str(asset): radius for asset, radius in RESOURCE_OVERRIDES.items()})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2) + '\n', encoding='utf8')
    print(f'Exported {len(radii)} numeric asset radii to {output}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='Installed Data/client/ini/cbasset.ini')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/asset_radii.json')
    args = parser.parse_args()
    export(args.source, args.output)


if __name__ == '__main__':
    main()
