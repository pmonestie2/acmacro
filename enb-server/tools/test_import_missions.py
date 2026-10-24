"""Public mission preservation and audited XML repairs, without live clients."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from import_missions import export_missions, parse_mission, repair_xml
from import_sector import SqlRow
from test_import_sector import table


def row(xml: str, identity: str = '5') -> SqlRow:
    return dict(mission_id=identity, mission_XML=xml, mission_name='SQL display name',
                mission_key='41', mission_type='0', mission_minSecurityLevel='0')


class MissionImportTests(unittest.TestCase):
    def test_order_operands_script_names_and_unknown_rule_attributes_survive_import(self) -> None:
        mission = parse_mission(row('''<Mission ID="9" forfeitable="0" time="60" jobcategory="2">
<Name>XML name</Name><Summary>Summary</Summary><Condition ID="5">0</Condition>
<Condition ID="5">1</Condition><Stage ID="7"><Completion ID="11" Data="17">55</Completion>
<Reward ID="12" Flags="2">TECL_test</Reward><Tree Node="1"><Text>Offer</Text>
<Flags Data="1">5</Flags></Tree></Stage><Stage ID="3"><Completion ID="23" Count="0"
Data="3" Text="TECL_test2">1</Completion><Reward ID="1">100</Reward></Stage></Mission>'''))
        self.assertEqual((mission['id'], mission['xml_id'], mission['name']), (5, 9, 'SQL display name'))
        self.assertEqual([(stage['index'], stage['source_id']) for stage in mission['stages']], [(0, 7), (1, 3)])
        self.assertEqual([condition['data'] for condition in mission['conditions']], [0, 1])
        self.assertEqual(mission['stages'][0]['completion'][0]['count'], 17)
        self.assertEqual(mission['stages'][1]['completion'][0]['count'], 3)
        self.assertEqual(mission['stages'][1]['completion'][0]['attributes']['Text'], 'TECL_test2')
        self.assertEqual(mission['stages'][0]['rewards'][0]['data'], 'TECL_test')
        self.assertFalse(mission['forfeitable'])
        self.assertEqual((mission['time_limit'], mission['job_category']), (60, 2))
        self.assertEqual(mission['issues'], [])

    def test_known_xml_repairs_preserve_dialogue_and_sound_and_are_reported(self) -> None:
        mission = parse_mission(row('''<Mission ID="5"><Stage ID="0"><Tree Node="1"Sound="voice">
<Text>Research &amp; trade & rescue</Text></Tree></Stage></Mission>'''))
        node = mission['stages'][0]['nodes'][0]
        self.assertEqual(node['text'], 'Research & trade & rescue')
        self.assertEqual(node['sound'], 'voice')
        self.assertEqual(len(mission['repairs']), 2)
        source = '<Mission><Text>A &amp; B</Text></Mission>'
        self.assertEqual(repair_xml(source), (source, []))

    def test_repeat_sentinel_is_valid_but_structural_faults_remain_explicit(self) -> None:
        mission = parse_mission(row('''<Mission><Stage ID="0"><Tree Node="1"><Text>Repeat</Text>
<Flags Data="-2">5</Flags></Tree></Stage></Mission>'''))
        self.assertEqual(mission['issues'], [])
        broken = parse_mission(row('''<Mission><Stage ID="0"><Tree Node="1"><Text>Broken</Text>
<Branch Node="5">Missing branch</Branch><Flags Data="20">5</Flags></Tree></Stage></Mission>'''))
        self.assertEqual(len(broken['issues']), 2)
        self.assertEqual(broken['stages'][0]['nodes'][0]['branches'][0]['node'], 5)

    def test_export_reads_only_public_rows_and_retains_unparseable_sources(self) -> None:
        rows: list[dict[str, str | int | float]] = [
            {key: value or '' for key, value in row('<Mission><Stage ID="0"/></Mission>').items()},
            {key: value or '' for key, value in row('not XML', '7').items()},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            dump, output = Path(temporary) / 'input.sql', Path(temporary) / 'missions.json'
            dump.write_text(table('missions', rows) + 'INSERT INTO `accounts` VALUES (private malformed);\n')
            counts = export_missions(dump, output)
            result = json.loads(output.read_text())
            self.assertEqual((counts['missions'], counts['unavailable']), (1, 1))
            self.assertEqual(result['unavailable'][0]['source_xml'], 'not XML')
            self.assertEqual(result['unavailable'][0]['id'], 7)
            dump.write_text(table('missions', [rows[0], rows[0]]))
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                export_missions(dump, output)


if __name__ == '__main__':
    unittest.main()
