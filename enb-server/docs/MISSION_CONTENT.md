# Public mission content checkpoint

Mission runtime work is deferred while combat and native navigation are repaired.
The importer is complete enough to preserve the available public definitions;
these records are not yet offered or rewarded by the game server.

`tools/import_missions.py` reads only the public SQL `missions` table through
`public_rows`. `data/missions.json` contains all 364 missions and 1,430 stages.
SQL mission IDs/names remain authoritative; XML IDs/names and stage IDs are also
retained. Stage indices follow XML order, matching the active TalkTreeParser.

Three source records require narrow, recorded repairs: mission319 contains a
bare ampersand; missions99 and354 omit whitespace between Tree Node and Sound
attributes. Each definition carries its repair list. Dialogue sound IDs,
completion Count/Data/Text attributes, raw rule attributes and nonnumeric script
operands survive export. Unknown numeric rules are retained without invented
semantics. Completion Count falls back to Data and then1, as in the parser;
it is not universally a repetition counter (it can be a skill ID or radius).

Twenty-eight completion entries use newer kinds20/21/23/24; twenty-four rewards
use kinds11/12, absent from the archived active enums. A script operand such as
`TECL_test` remains text rather than silently becoming zero. Mission410 has a
dangling dialogue branch, recorded in its issues list; the definition is retained.
Destination-2 is a valid repeat-completion sentinel, not a missing stage.

Future runtime acceptance must reject unsupported actions or unresolved content
issues before costs/rewards, and store inventory plus mission progress in one
atomic transaction. The archived progress bitmask and partial cost/reward mutation
bugs should not be reproduced. Native mission AUX has12 slots and20 stage
records per slot; the existing macro's job pickup target is a separate policy.

Rebuild with `C:\PYTHON\python.exe enb-server/tools/import_missions.py PATH_TO_SQL`.
Synthetic tests verify stage ordering, rules/attributes, script operands, repairs,
repeat sentinels, malformed content retention, duplicate IDs and exclusion of
private tables. These tests do not exercise a live client or mission progression.
