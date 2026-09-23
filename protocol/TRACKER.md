# Continuous object tracking

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
