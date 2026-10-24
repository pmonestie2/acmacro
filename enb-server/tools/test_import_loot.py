"""Public drop importer tests: percentages, orphan rows, diagnostics and identity."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from import_loot import compile_loot
from test_import_sector import table


class LootImportTests(unittest.TestCase):
    def test_percentages_quantities_and_rejected_rows_are_preserved(self) -> None:
        rows = [dict(id=1, mob_id=10, item_base_id=50, drop_chance=.5, usage_chance=500, qty=3, type=1),
                dict(id=2, mob_id=10, item_base_id=99, drop_chance=100, usage_chance=0, qty=1, type=0),
                dict(id=3, mob_id=11, item_base_id=50, drop_chance=10, usage_chance=0, qty=1, type=0),
                dict(id=4, mob_id=10, item_base_id=50, drop_chance=101, usage_chance=0, qty=1, type=0),
                dict(id=5, mob_id=10, item_base_id=50, drop_chance=100, usage_chance=0, qty=0, type=0)]
        with tempfile.TemporaryDirectory() as temporary:
            dump, items = Path(temporary)/'public.sql', Path(temporary)/'items.json'
            dump.write_text(table('mob_base', [dict(mob_id=10, type=2)]) + table('mob_items', rows))
            items.write_text(json.dumps([{'id': 50}]))
            result = compile_loot(dump, items)
            self.assertEqual(result['mobs'], [{'id': 10, 'kind': 2, 'drops': [
                {'source_id': 1, 'item': 50, 'chance_percent': .5, 'usage_chance': 500, 'quantity': 3, 'kind': 1}]}])
            self.assertEqual([row['source_id'] for row in result['rejected']], [2, 3, 4, 5])
            dump.write_text(table('mob_base', [dict(mob_id=10, type=2)]) + table('mob_items', [rows[0], rows[0]]))
            with self.assertRaisesRegex(ValueError, 'Duplicate MOB item'): compile_loot(dump, items)
