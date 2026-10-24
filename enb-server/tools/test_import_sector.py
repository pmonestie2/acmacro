"""Public content import tests using a small synthetic SQL file."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from import_sector import export_sector, public_rows


def table(name: str, rows: list[dict[str, str | int | float]]) -> str:
    columns = list(rows[0])
    schema = f'CREATE TABLE `{name}` (\n' + ''.join(f'  `{column}` text,\n' for column in columns) + ');\n'
    values = []
    for row in rows:
        values.append('(' + ','.join("'" + str(row[column]).replace("'", "\\'") + "'" for column in columns) + ')')
    return schema + f'INSERT INTO `{name}` VALUES ' + ','.join(values) + ';\n'


class SectorImportTests(unittest.TestCase):
    def test_static_content_is_normalized_and_spawns_are_excluded(self) -> None:
        sector: dict[str, str | int | float] = {'sector_id': 1076, 'name': 'Asteroid Belt Alpha', 'system_id': 5,
                  'x_min': -10, 'y_min': -10, 'z_min': -10, 'x_max': 10, 'y_max': 10, 'z_max': 10,
                  'backdrop_asset': 220}
        objects: list[dict[str, str | int | float]] = [{'sector_object_id': 1, 'sector_id': 1076, 'type': 11, 'base_asset_id': 48,
                    'name': "Gate to Test's Sector", 'gate_to': 1077, 'scale': 0,
                    'position_x': 5, 'position_y': 0, 'position_z': 0, 'appears_in_radar': 1},
                   {'sector_object_id': 2, 'sector_id': 1076, 'type': 0, 'base_asset_id': 0,
                    'name': 'spawn rule', 'gate_to': 0, 'scale': 0,
                    'position_x': 0, 'position_y': 0, 'position_z': 0, 'appears_in_radar': 1}]
        sql = table('sectors', [sector]) + table('systems', [{'system_id': 5, 'name': 'Sol'}])
        sql += table('sector_objects', objects)
        sql += table('sector_nav_points', [{'sector_object_id': 1, 'nav_type': 1, 'signature': 60000, 'is_huge': 1},
                                          {'sector_object_id': 2, 'nav_type': 1, 'signature': 0, 'is_huge': 0}])
        sql += 'INSERT INTO `accounts` VALUES (malformed private content);\n'
        with tempfile.TemporaryDirectory() as temporary:
            dump, output = Path(temporary) / 'content.sql', Path(temporary) / 'sector.json'
            dump.write_text(sql)
            export_sector(dump, 1076, output)
            result = json.loads(output.read_text())
        self.assertEqual(result['system'], 'Sol')
        self.assertEqual(len(result['navpoints']), 1)
        nav = result['navpoints'][0]
        self.assertEqual((nav['id'], nav['source_id'], nav['scale']), (100001, 1, 1.))
        self.assertEqual(nav['orientation'], [0., 0., 0., 1.])
        self.assertEqual(nav['gate_destination'], 1077)
        self.assertTrue(nav['navigation_present'])
        self.assertEqual(nav['name'], "Gate to Test's Sector")

    def test_mismatched_row_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'bad.sql'
            path.write_text('CREATE TABLE `sectors` (\n  `sector_id` int\n);\nINSERT INTO `sectors` VALUES (1,2);\n')
            with self.assertRaisesRegex(ValueError, 'wrong field count'):
                list(public_rows(path))


if __name__ == '__main__':
    unittest.main()
