from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from import_asset_radii import export, load_radii, load_resource_radii, parse_radii


class AssetRadiusTests(unittest.TestCase):
    def test_export_numeric_geometry_with_provenance_and_identical_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'cbasset.ini'
            target = Path(temporary) / 'radii.json'
            source.write_text('[BASE-2]\nRadius=12.5\nRadiusAdjust=999\nMount0=opaque\n'
                              '[BASE-2]\nRadius=12.5\n[BASE-1]\nRadius=0\n[BASE-3]\nMount0=other\n')
            export(source, target)
            self.assertEqual(load_radii(target), {1: 0., 2: 12.5})
            document = json.loads(target.read_text())
            self.assertEqual(document['source'], 'cbasset.ini')
            self.assertEqual(len(document['sha256']), 64)
            self.assertNotIn('Mount0', target.read_text())
            geometry = load_resource_radii(target)
            self.assertEqual(geometry[2], 12.5)
            self.assertEqual(geometry[1822], 800.)
            self.assertEqual(geometry[1827], 630.)
            self.assertEqual(geometry[1830], 510.)
            self.assertEqual(geometry[1833], 750.)
            self.assertEqual(geometry[1834], 0.)

    def test_bad_geometry_and_conflicting_duplicates_are_rejected(self) -> None:
        for source in ['[BASE-1]\nRadius=nan', '[BASE-1]\nRadius=-1',
                       '[BASE-1]\nRadius=inf', '[BASE-65536]\nRadius=1',
                       'Radius=1', '[BASE-1]\nRadius=1\n[BASE-1]\nRadius=2',
                       '[BASE-1]\nRadius=2000001', '[BASE-1]\nMount0=anything']:
            with self.subTest(source=source), self.assertRaises(ValueError):
                parse_radii(source)

    def test_serialized_catalog_rejects_aliases_nonfinite_and_wrong_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / 'radii.json'
            for radii in [{'1': 2., '01': 3.}, {'1': '2'}, {'1': True},
                          {'1': float('nan')}, {'-1': 5}, {'65536': 2}]:
                target.write_text(json.dumps(dict(version=1, radii=radii)))
                with self.subTest(radii=radii), self.assertRaises(ValueError):
                    load_radii(target)


if __name__ == '__main__':
    unittest.main()
