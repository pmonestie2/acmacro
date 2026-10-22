# Continuous object tracking

## Two-client capture

`C:\PYTHON\python.exe protocol\capture_world.py` runs a separate read-only
catalog per proxy PID and process creation time. It refreshes endpoint ownership
every 10 seconds and saves reports every 2 seconds under `captures/multi_world/`.
`catalogs.json` retains all decoded ID/name/position mappings for each process
session; its `recent` view still expires observations after 30 seconds. Raw UDP
logs are separated by proxy and rotate at 10 MB with three backups.

Token changes archive the previous catalog rather than losing those mappings.
Changes to a local socket's remote endpoint and session token remain transition
candidates. The observed `0x0097` layout now supplies the system/sector strings;
a change in its sector name is recorded as a sector transition and archives the
previous catalog. Observed `0x001d` messages also contain explicit sector-entry
text. `0x2018` map-object records supply IDs, names and XYZ coordinates (ID at
offset 0, XYZ float32 at 25, name length at 58 and name at 60). Only this validated
layout is decoded; map-object classification does not guarantee navigability.
The capture resumes from the current proxy lifetime's raw logs. No commands
are injected.

Stop this capture gracefully by creating `captures/multi_world/capture.stop`.
`status.json` identifies the collector PID and reports per-proxy packet/catalog
counts. The original single-world tracker below can mix two clients' tokens;
use this per-proxy collector for two games.

Run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File protocol\start_tracker.ps1` to launch the tracker hidden with administrator access. It runs until stopped; it is independent of the macro. Stop it gracefully with `powershell.exe -NoProfile -ExecutionPolicy Bypass -File protocol\stop_tracker.ps1`. Only one instance is allowed.

The Windows raw-IPv4 socket reader uses host-only IP-level mode (`RCVALL_IPLEVEL`), without requesting promiscuous mode from the NIC/VM switch. It stays active without timed Packet Monitor start/stop cycles. The socket can receive other host IPv4 traffic, but the application discards it before decoding or saving unless it matches the current game/proxy UDP endpoints. Those endpoints refresh every ten seconds. The tracker does not inject traffic or change game memory. The live output files update once per second:

- `captures/world_state.json`: remembered object IDs, names, coordinates, last-seen times, candidate removals, player position and approximate distances.
- `captures/tracker_status.json`: running/stopped/error state, process identities, endpoint filters, packet count and object counts.
- `captures/packet_stream.jsonl`: filtered raw UDP payloads for later analysis, rotated at 10 MB with three backups (about 40 MB maximum).
- `captures/tracker.log`: operational errors, rotated separately.

The public dump contains only objects seen in the last **30 seconds**, sorted newest first. Expired objects disappear from the dump and its counts. Their metadata remains in the internal `tracker_cache.json` so later updates can retain known names. `0x0007` marks a candidate removal; receiving the object again clears that marker. Asteroid/gas-cloud names, shared type IDs and positions are decoded from the observed `0x2019` layout; the supported `0x003e` movement layout updates existing records or adds unnamed objects. The tracker does not classify every moving object as an enemy.

Levels from supported explicit `Level N` target labels are recorded as confirmed packet fields. User calibration lives in `protocol/level_evidence.json`: the selected gas cloud `0x1873e` was confirmed as level 3, with shared type ID 1834. Other objects with the same type and name may show `inferred_level: 3`, clearly marked low confidence, based on that one sample. Conflicting samples disable the inference. A shared type ID is not proven to determine level. The instance object ID remains distinct from the shared type ID.

On restart, saved observations are resumed only when the game/proxy process identities match. Game process or session-token changes reset the current observations so old sectors/sessions are not presented as current. Sector transitions that do not change these identifiers are not yet independently detected; last-seen/removal information remains important. Historical capture seeds are optional and ignored if they predate the current process session.

Counts mean **remembered or recently observed objects**, not the game's exact current visible count. Objects already loaded before tracking began may remain unknown until resent. Unsupported packet layouts and IP fragments are not decoded. Raw socket capture does not include the localhost TCP leg and cannot guarantee that Windows dropped no packets. Distances use the latest available, potentially non-simultaneous coordinates divided by 1,000.

The continuous reader was verified receiving live game traffic on this Windows installation. The protocol tests include object persistence, stale records, removal/reappearance, historical classification and session resets.

## Per-client navpoint exploration

The installed proxy SHA256 `dbcece6433882234e842696713fef5454da91b90d3fa199e327e3301cd7faf87`
expands `0x2018` body byte 57 in its handler at `0x40f3c0`, calling
`0x401f50`. That routine emits `0x0099` Navigation with bit 7 as
PlayerHasVisited, bit 6 as IsHuge, and the low nibble as NavType, only when
bit 4 is set. See `diagnostics/nav_visited_bits_disassembly.txt`.
Standalone `0x0099` (14-byte body) supplies object ID, signature, visited,
nav type and huge fields and updates discovery without replacing the name.
These states belong to the captured client session, not the global nav catalog.
The old published source calls NavType 1 a warp-path nav and NavType 2 a
destination-only nav: https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/NavTypeClass.cpp

`python exploration_plan.py --proxy PID` saves the current sector's unexplored
navpoints and a route in `navigation/exploration_plan.json`. Navigation flags
decide eligibility: asteroid nav targets count; ordinary resources do not.
Known unsafe targets are listed separately. Route cost includes a final leg to
the closest safe recorded sector gate to the last exploration target, even if the gate
is already explored. Up to nine targets use exact search; larger sets use
nearest-neighbor and 2-opt. Destination-only targets end separate submissions.

Add `--run` to execute through `explore_sector.py`. Keep the normal macro stopped
for that client to avoid competing navigation commands. The runner validates
proxy process birth, sector and target coordinates before each submission and
while waiting; observed hazards are checked before new submissions. New mobs
near via points do not interrupt an already submitted warp path. Arrival requires
discovery flags and a player position observed during that leg within 5 km of the
ordinary endpoint (10 km for gate centers), with position updates settled for
two seconds before another route. Large gates report distance to their surface
in the UI, while captured XYZ measures distance to the object's center.
Position packets can stop several km before the client finishes coasting.
Each leg has at least 240 seconds,
extended for long multi-waypoint routes at 1 km/s plus 120 seconds;
uncertain submission or arrival stops the runner without retries or relogin.
It finishes at the gate without clicking gate entry. Gates near known hazards are
excluded; the closest remaining gate is used. If no safe gate is known, ordinary
execution refuses. A stale starting position is recorded explicitly. Packet
visibility limits hazard detection; no observed enemies does not prove safety.

After completing a tour, `--run` reads discovery state again and tours newly
streamed navpoints, again ending at the closest gate to the last target.
`navigation/gravity_wells.json`, when available, maps exact captured sector names
to arrays of verified zones: `raw_xyz` center, `radius` in raw units, optional
`margin` (default 5000), and source metadata. Every segment is checked against
these zones; detours use captured NavType 1 IDs with shortest-path search. If no
detour is possible, execution refuses. Missing well data means unknown coverage,
not a verified well-free sector. enbmaps.de's ABA map layer was unavailable on
October 11, 2026; no well coordinates were guessed from its empty menu page.

`explore_frontier.py --session PID_BIRTH --sector NAME` supports supervised
touring when only nearby navpoints are streamed. Every submitted leg ends at a
captured gate outside known hazards; ordinary navs are via points only.
Destination-only non-gates are deferred. Departure gates within 15 km are
excluded: the server rejects a route ending at the origin gate even with distant
via points. If no usable gate is known, exploration refuses to depart.
Newly streamed targets are incorporated at gate stops. It never enters a gate.

Kraken L3 activation was verified live on October 11, 2026: opcode 0x005D,
6-byte body <IBB: full player GameID (little endian), inventory 2, slot 1.
The slot is equipment inventory indexing, not UI hotkey numbering (hotkey 4).
The guarded native proxy submission emitted the same wire command and the game
confirmed activation. Validation currently permits only this observed slot.
Target selection remains through the UI; selection packet injection is disabled.

`equipment_activation.activate_kraken(instance, self_targeted=True,
slot_verified=True)` packages the verified packet operation for reuse. Both
conditions must be checked by the caller; inventory slot 1 must contain Kraken.
A native handler return is submission evidence, not proof of a successful buff.

`route_replanning.change_clearance(world, hazards)` permits a route change only
with player position and capture no more than 3 seconds old and no hostile or
unidentified live contacts or saved hazards within 20 km of the player.
`replace_warp_route(...)` requires confirmed active warp and a caller-provided
fresh stop acknowledgement. It validates a gate-ended replacement, submits one
warp toggle, waits for confirmed stop, then submits the replacement once to
restart. A failed stop acknowledgement never causes an automatic second toggle.
The caller must own navigation input throughout the operation. This API is not
automatically enabled in the live touring runner.

Live test in Glenn on October 11 confirmed that replacement during warp stopped
movement (about 3.5 km/s to zero); a separate submission restarted it. Evidence:
`diagnostics/live_route_update_result.json`. This matches the published server's
`Player::HandleWarp`, which calls `TerminateWarpGroup(true)` when already warping:
https://github.com/therealKyp/Earth-and-Beyond-server/blob/master/trunk/Net7/PlayerConnection.cpp#L1443
Packet visibility cannot guarantee that an area or a gate is free of enemies.
