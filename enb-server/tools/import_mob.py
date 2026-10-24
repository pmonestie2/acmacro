"""Export one low-level public MOB definition from the archived MySQL dump.

Only mob_base INSERT values are read. No account, password, character, or other
private database table is exported. This parser never executes SQL.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
import json
from pathlib import Path

from build import ROOT


def mysql_rows(values: str) -> Iterator[list[str | None]]:
    row: list[str | None] | None = None
    token: list[str] = []
    quoted = False
    was_quoted = False
    escaped = False
    escape_values = {'n': '\n', 'r': '\r', 't': '\t', '0': '\0', 'b': '\b', 'Z': '\x1a'}
    for char in values:
        if quoted:
            if escaped:
                token.append(escape_values.get(char, char))
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == "'":
                quoted = False
            else:
                token.append(char)
            continue
        if char == "'":
            if token and all(value.isspace() for value in token):
                token.clear()
            quoted, was_quoted = True, True
        elif char == '(':
            if row is not None:
                raise ValueError('SQL expressions are not supported in content INSERT values')
            row, token, was_quoted = [], [], False
        elif char in ',)' and row is not None:
            value = ''.join(token) if was_quoted else ''.join(token).strip()
            row.append(None if not was_quoted and value == 'NULL' else value)
            token, was_quoted = [], False
            if char == ')':
                yield row
                row = None
        elif row is not None:
            if not (was_quoted and char.isspace()):
                token.append(char)
        elif char not in ',; \t\r\n':
            raise ValueError('Unexpected token outside SQL content row')
    if quoted or escaped or row is not None:
        raise ValueError('Truncated SQL content INSERT')


def mob_rows(path: Path) -> Iterator[list[str | None]]:
    with path.open(encoding='utf-8', errors='replace') as stream:
        for line in stream:
            if line.startswith('INSERT INTO `mob_base` VALUES '):
                yield from mysql_rows(line.partition(' VALUES ')[2])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/sector.json')
    args = parser.parse_args()
    count = 0
    chosen: list[str | None] | None = None
    for row in mob_rows(args.dump):
        count += 1
        if len(row) < 11 or row[2] is None or row[7] is None:
            continue
        name = row[1] or ''
        if int(row[2]) == 1 and 0 < int(row[7]) <= 65535 and name.isascii() and len(name) < 60:
            if chosen is None or int(row[0] or '0') < int(chosen[0] or '0'):
                chosen = row
    if chosen is None:
        raise SystemExit('No suitable level-one MOB definition in the dump')
    content = json.loads(args.output.read_text(encoding='utf-8'))
    content['mobs'] = [{'source_id': int(chosen[0] or '0'), 'name': chosen[1], 'level': int(chosen[2] or '1'),
                        'asset': int(chosen[7] or '0'), 'spawn': [15000, 15000, 0],
                        'respawn_seconds': 20, 'aggressive': True}]
    args.output.write_text(json.dumps(content, indent=2) + '\n', encoding='utf-8')
    print(f'Read {count} MOB definitions; exported {chosen[1]} (source ID {chosen[0]}, level {chosen[2]}, model {chosen[7]})')


if __name__ == '__main__':
    main()
