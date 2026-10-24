"""Public matrix provenance, PDA capacity and explicit missing definitions."""
from __future__ import annotations
import json
from pathlib import Path
import tempfile
import unittest
from import_factions import export_factions
from test_import_sector import table


class FactionImportTests(unittest.TestCase):
    def test_base_current_reward_and_unresolved_numeric_ids_are_preserved_without_private_rows(self) -> None:
        factions: list[dict[str, str | int | float]] = [dict(faction_id=3, name='Centuriata',
            description='Public', player_PDA=1, PDA_text='Centuriata', faction_gain_sound='gain.wav')]
        matrix: list[dict[str, str | int | float]] = [dict(faction_id=3, faction_entry_id=32,
            base_value=-2000, current_value=9999, reward_faction=0.5)]
        with tempfile.TemporaryDirectory() as temporary:
            dump, output = Path(temporary) / 'input.sql', Path(temporary) / 'factions.json'
            dump.write_text(table('factions', factions) + table('faction_matrix', matrix)
                + 'INSERT INTO `faction_data` VALUES (private malformed player data);\n')
            self.assertEqual(export_factions(dump, output), (1, 1))
            result = json.loads(output.read_text())
            self.assertEqual(result['unresolved_factions'], [32])
            self.assertEqual(result['relationships'][0], dict(source=3, target=32, base=-2000., current=9999., reward=0.5))
            self.assertEqual(result['factions'][0]['pda_text'], 'Centuriata')
            dump.write_text(table('factions', factions) + table('faction_matrix', [matrix[0], matrix[0]]))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                export_factions(dump, output)

    def test_nonfinite_matrix_values_do_not_create_an_output_catalog(self) -> None:
        factions: list[dict[str, str | int | float]] = [dict(faction_id=3, name='Centuriata',
            player_PDA=1, PDA_text='Centuriata')]
        matrix: list[dict[str, str | int | float]] = [dict(faction_id=3, faction_entry_id=3,
            base_value='nan', current_value=0, reward_faction=0)]
        with tempfile.TemporaryDirectory() as temporary:
            dump, output = Path(temporary) / 'input.sql', Path(temporary) / 'factions.json'
            dump.write_text(table('factions', factions) + table('faction_matrix', matrix))
            with self.assertRaisesRegex(ValueError, 'Nonfinite'):
                export_factions(dump, output)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
