"""Export only public SQL missions, retaining original numeric rules and order.

The active C++ TalkTreeParser uses XML stage order, explicit dialogue node IDs,
and Count -> Data -> 1 for completion operands. Unknown rule IDs are preserved
for runtime support checks. Content repairs are narrow and recorded per mission.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import TypedDict
import xml.etree.ElementTree as ET

from build import ROOT
from import_npcs import client_text
from import_sector import SqlRow, public_rows


class Condition(TypedDict):
    kind: int
    data: int | str
    flags: int
    attributes: dict[str, str]


class Completion(TypedDict):
    kind: int
    data: int | str
    count: int
    text: str
    attributes: dict[str, str]


class Reward(TypedDict):
    kind: int
    data: int | str
    flags: int
    attributes: dict[str, str]


class Branch(TypedDict):
    node: int
    text: str


class TalkNode(TypedDict):
    id: int
    text: str
    flag: int
    destination: int
    sound: str
    branches: list[Branch]


class Stage(TypedDict):
    index: int
    source_id: int
    description: str
    completion: list[Completion]
    rewards: list[Reward]
    nodes: list[TalkNode]


class Mission(TypedDict):
    id: int
    xml_id: int
    name: str
    xml_name: str
    summary: str
    key: int
    kind: int
    minimum_security: int
    forfeitable: bool
    time_limit: int
    job_category: int
    conditions: list[Condition]
    stages: list[Stage]
    repairs: list[str]
    issues: list[str]


class UnavailableMission(TypedDict):
    id: int
    name: str
    error: str
    source_xml: str


class Catalog(TypedDict):
    version: int
    missions: list[Mission]
    unavailable: list[UnavailableMission]


def integer(text: str | None, default: int = 0) -> int:
    return default if text is None or not text.strip() else int(text.strip())


def operand(text: str | None) -> int | str:
    value = (text or '').strip()
    try:
        return int(value) if value else 0
    except ValueError:
        # Later SQL content uses named scripts absent from this C++ revision.
        # Retain that operand rather than silently converting it to zero.
        return value


def repair_xml(source: str) -> tuple[str, list[str]]:
    repairs: list[str] = []
    escaped, count = re.subn(r'&(?!#\d+;|#x[0-9a-fA-F]+;|amp;|lt;|gt;|quot;|apos;)',
                            '&amp;', source)
    if count:
        repairs.append(f'Escaped {count} bare XML ampersand(s)')
    # Public missions99/354 have <Tree Node="1"Sound="...">. Insert only
    # that known missing attribute separator, leaving dialogue text untouched.
    separated, count = re.subn(r'(<Tree\s+Node="[^"]*")(?=Sound=")', r'\1 ', escaped)
    if count:
        repairs.append(f'Inserted {count} missing Tree Node/Sound separator(s)')
    return separated, repairs


def talk_nodes(stage: ET.Element) -> tuple[list[TalkNode], list[str]]:
    result: list[TalkNode] = []
    issues: list[str] = []
    for tree in stage.findall('Tree'):
        flag = integer(tree.findtext('Trade')) or integer(tree.findtext('Flags'))
        flags = tree.find('Flags')
        result.append(TalkNode(
            id=integer(tree.get('Node')), text=client_text(tree.findtext('Text')),
            flag=flag, destination=integer(flags.get('Data') if flags is not None else None),
            sound=client_text(tree.get('Sound')),
            branches=[Branch(node=integer(branch.get('Node')), text=client_text(branch.text))
                      for branch in tree.findall('Branch')]))
    identities = {node['id'] for node in result}
    if result and (len(identities) != len(result) or 1 not in identities):
        issues.append('Dialogue has duplicate nodes or no root1')
    if any(not 1 <= node['id'] < 230 or len(node['branches']) > 20
           or any(not 0 <= branch['node'] < 230 or branch['node'] != 0
                  and branch['node'] not in identities for branch in node['branches'])
           for node in result):
        issues.append('Dialogue has an out-of-range or dangling branch')
    return result, issues


def parse_mission(row: SqlRow) -> Mission:
    source, repairs = repair_xml(row.get('mission_XML') or '')
    root = ET.fromstring(source)
    if root.tag != 'Mission':
        raise ValueError('Mission XML has an unexpected root')
    stages: list[Stage] = []
    issues: list[str] = []
    for index, stage in enumerate(root.findall('Stage')):
        nodes, node_issues = talk_nodes(stage)
        issues.extend(f'Stage{index}: {issue}' for issue in node_issues)
        stages.append(Stage(
            index=index, source_id=integer(stage.get('ID'), index),
            description=client_text(stage.findtext('Description')),
            completion=[Completion(kind=integer(node.get('ID')), data=operand(node.text),
                                   count=integer(node.get('Count')) or integer(node.get('Data')) or 1,
                                   text=client_text(node.get('Text')), attributes=dict(node.attrib))
                        for node in stage.findall('Completion')],
            rewards=[Reward(kind=integer(node.get('ID')), data=operand(node.text),
                            flags=integer(node.get('Flags')), attributes=dict(node.attrib))
                     for node in stage.findall('Reward')],
            nodes=nodes))
    if not 1 <= len(stages) <= 20:
        issues.append('Mission stage count exceeds the native20-stage capacity')
    if any(node['flag'] == 5 and node['destination'] != -2
           and not 0 <= node['destination'] < len(stages)
           for stage in stages for node in stage['nodes']):
        issues.append('Mission dialogue jumps to a missing stage')
    return Mission(
        id=integer(row.get('mission_id')), xml_id=integer(root.get('ID')),
        name=client_text(row.get('mission_name')), xml_name=client_text(root.findtext('Name')),
        summary=client_text(root.findtext('Summary')), key=integer(row.get('mission_key')),
        kind=integer(row.get('mission_type')), minimum_security=integer(row.get('mission_minSecurityLevel')),
        forfeitable=bool(integer(root.get('forfeitable'), 1)), time_limit=integer(root.get('time')),
        job_category=integer(root.get('jobcategory')),
        conditions=[Condition(kind=integer(node.get('ID')), data=operand(node.text),
                              flags=integer(node.get('Flags')), attributes=dict(node.attrib))
                    for node in root.findall('Condition')],
        stages=stages, repairs=repairs, issues=issues)


def export_missions(dump: Path, output: Path) -> Counter[str]:
    catalog = Catalog(version=1, missions=[], unavailable=[])
    counts: Counter[str] = Counter()
    identities: set[int] = set()
    for _, row in public_rows(dump, frozenset({'missions'})):
        identity = integer(row.get('mission_id'))
        if identity <= 0 or identity in identities:
            raise ValueError('Duplicate or invalid public mission identity')
        identities.add(identity)
        try:
            mission = parse_mission(row)
        except (ValueError, ET.ParseError) as error:
            catalog['unavailable'].append(UnavailableMission(
                id=identity, name=client_text(row.get('mission_name')), error=str(error),
                source_xml=row.get('mission_XML') or ''))
            counts['unavailable'] += 1
            continue
        catalog['missions'].append(mission)
        counts['missions'] += 1
        counts['stages'] += len(mission['stages'])
        counts['repaired_missions'] += bool(mission['repairs'])
        counts['missions_with_content_issues'] += bool(mission['issues'])
        counts['unknown_completion_rules'] += sum(node['kind'] > 19
            for stage in mission['stages'] for node in stage['completion'])
        counts['unknown_reward_rules'] += sum(node['kind'] > 10
            for stage in mission['stages'] for node in stage['rewards'])
    catalog['missions'].sort(key=lambda mission: mission['id'])
    catalog['unavailable'].sort(key=lambda mission: mission['id'])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(catalog, ensure_ascii=True, indent=2) + '\n', encoding='utf-8')
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dump', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/missions.json')
    args = parser.parse_args()
    print(dict(export_missions(args.dump, args.output)))


if __name__ == '__main__':
    main()
