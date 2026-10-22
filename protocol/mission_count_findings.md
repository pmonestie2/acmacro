# Mission count evidence

Implemented on 2026-10-18 in `mission_state.py`. The job-terminal opcodes
0x0093/0x0094 describe available jobs, not the character's accepted mission count.
Accepted missions are in incoming 0x001b player AUX data (object ID zero, index
type 1), under player field 9. Each mission slot has an ID, name, summary,
forfeitable/completed flags and stage information. Empty slots retain their IDs;
counting IDs would overcount.

Primary layout references:

- https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/AuxClasses/AuxPlayerIndex.cpp
- https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/AuxClasses/AuxMissions.cpp
- https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/AuxClasses/AuxMission.cpp
- https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/AuxClasses/AuxMissionStages.cpp

Existing per-proxy UDP captures contained 4,456 player AUX observations. Thirteen
distinct mission-update shapes decoded with the supported layout. Two additional
shapes combined missions with unsupported inventory/RPG or trailing fields and
are deliberately rejected. Full captured mission snapshots enumerate slots
0..10; the older public source supports 12. Both full-list variants are accepted.
Tests retain actual empty, promotion-only, and promotion + Sample Delivery
snapshots (89, 920 and 1,138 body bytes) in `tests/fixtures/mission_aux.json`.
This validates actual mission data, not just the obsolete public source.

The tracker requires a full enumerated snapshot before applying deltas. Duplicate
and older sequenced envelopes are ignored; sequence gaps invalidate the count.
Proxy identity and remote/session changes reset state. Current owned incoming
traffic and a short settle interval are required before returning a count.
Unsupported/malformed mission updates invalidate the snapshot. Screen fallback
is explicit in the macro log. These changes were checked against saved captures
and unit tests; a new live job acceptance cycle has not been run for validation.
