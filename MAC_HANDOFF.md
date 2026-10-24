# Server development handoff to Mac

Recorded 2026-10-24, approximately 12:56 America/Los_Angeles.

Development stopped at the user's request. No files were moved or renamed and
no server migration was performed. The user will rearrange the projects first.
Leave the full server reimplementation goal unfinished; resume it on the Mac
after the user finishes relocation. Missions were explicitly deferred in favor
of combat, world content, equipment, navigation and multiplayer.

## Git and ownership

- Repository: `git@github.com:pmonestie2/acmacro.git`, branch `main`.
- Latest server implementation commit: **`207e2a0`**, already pushed. This handoff is a
  subsequent documentation-only commit.
- Worktree was clean before creating this file. No implementation edits remain
  only in the VM's working tree.
- The Mac UI agent's work through **`3095d9c`** is integrated. It owns
  `enb-unity/**`; server work owns `enb-server/**`. Shared protocol changes and
  server restarts are coordinated through the endpoint below.
- User requested regular pulls from the other agent and pushes at least every
  20 minutes. Fetch before pushing; concurrent pushes have occurred. Rebase only
  unpublished local commits when needed; no force pushes were used.
- The server subagent was interrupted after the stop request. It has no pending
  source edits. Its last assignment was a read-only CPU investigation.

## Running VM server: deliberately behind Git

**The running executable is build `b4a7363`, not `207e2a0`.** Do not assume the
new recovery formulas or resource radii are loaded merely because they are in Git.

At handoff, the owned server is still running:

| Item | Value |
|---|---|
| PID | `11028` — revalidate before any process action |
| Executable | `C:\MACROS\acmacro\enb-server\target\debug\enb-server.exe` |
| Bind address | `192.168.47.128` |
| Content argument | `C:\MACROS\acmacro\enb-server\data\asteroid_belt_alpha.json` |
| State directory | `C:\MACROS\acmacro\enb-server\state` |
| Ownership record | `enb-server/state/server.local.json` |
| Runtime log | `enb-server/logs/server.log` |

The most recently observed native toon was in Carpenter, sector `4520`, with
74 static navs and 99 MOBs, and had activated Advanced Cloak. This is an
observation, not a guarantee of its position when the next agent resumes.

No Cargo, rustc or Rust test processes remained in the final process check.
The original Windows game/proxy were left alone. The server was not stopped.

### Current CPU finding, unresolved

The full unit-test executable was briefly using about **2.5 CPU cores**. It has
finished. Separately, the live **debug** server used approximately **0.6–0.7 of
one core** in two 3-second samples. These are core-equivalent percentages, not
percentages of the entire host. Do not attribute all of the observed load to tests.

The live-server cost was being investigated when the user requested stopping.
No CPU optimization, release deployment or tick-rate change was performed.
Likely investigation points, not established causes: `GameState::advance`,
repeated session attachment, active/inactive sector ticking, rendering/AUX work,
world persistence and per-character checkpoints. The VM build uses the Windows
GNU Rust toolchain; compare an optimized native Mac build before drawing a
conclusion about final server performance.

### Other observed runtime error, unresolved

Recent VM logs contain `Invalid corpse world file` while handling action
packets after selecting object `100667`. The cause was not investigated before
the stop request. Preserve the relevant saved world files and logs; do not
silently delete/reseed loot state to hide the error.

## Latest committed checkpoint: `207e2a0`

### Recovery rules

The original client now gets native Group/Follow verbs when targeting players,
and native Jumpstart verb `12` / action `26` are implemented. Self recovery and
rescue of a visible, ungrouped incapacitated player route through the ability
lifecycle and fresh coordinator eligibility checks.

The latest, **not yet deployed**, checkpoint replaces temporary catalog behavior
with the active C++ Jumpstart execution rules:

- Target hull-upgrade level `H` costs `20 * H` energy, with the native surcharge
  when `H` exceeds trained skill and rejection when it exceeds skill by more
  than two levels.
- Current sandbox ships advertise hull upgrade `6`; with trained Jumpstart `7`,
  a new cast costs **120 energy**, executes in **5 seconds**, restores **70% hull**,
  leaves shield at zero, and has **no cooldown**.
- The source's active `Update` uses 10% hull per trained level; old comments
  describing 7% are stale. The native call path uses `SetTimer(5000)` despite
  the unused seven-second charge calculation elsewhere in that class.
- A typed `JumpstartPlan` is frozen on `Cast`, validated on checkpoint restore,
  and carries range/repair into support delivery. An already-committed legacy
  cast without that plan finishes under its old paid cost, remaining time,
  repair and cooldown; it is not repriced or restarted.
- Native effects distinguish self `411` from another ship `414`.

Important remaining gap: **recipient confirmation is not implemented**. The
active C++ flow sends `0x1e` with a yes/no message and handles actions `11/12`,
sharing the group-confirmation UI. This is not the rescue/NPC `0x55` dialog.
Coordinate any confirmation work with the UI agent to avoid mixing it up with
group invitations. Cross-player support still uses the existing transient
coordinator outbox; this checkpoint does not add an atomic recovery delivery
journal/receipt.

Relevant files: `jumpstart_rules.rs`, `native_jumpstart.rs`, `ability_actions.rs`,
`skills.rs`, `session_checkpoint.rs`, `session_peers.rs`, `game_actions.rs` and
`docs/NATIVE_JUMPSTART.md`.

### Resource and wreck geometry

- Added explicit radii to **21,199** exported resource nodes and the **616** nodes
  in the standalone Alpha map. A deep comparison verified that only resource
  radius metadata changed: positions, IDs, ore choices, MOBs and navs were retained.
- Imported the active Resource/Husk size overrides for assets `1822..1834`
  separately from base model radii. All 15 exported resource asset types have
  known geometry.
- `asset_geometry.rs` caches the committed numeric catalog and rejects unknown
  assets instead of inventing dimensions. `NavPoint::range_to` and corpse range
  use unscaled receiver radius, preserving the distinction from visual scale.
- Prospect and loot admission and their native buttons use surface distance.
  **Tractor timing intentionally remains center-based**: the active C++ calls
  `RangeFrom(..., true)` for those animation/collection timings.
- Existing NPC geometry already covers all 6,650 public MOB actors and their
  139 asset types. Static gate/station geometry, spawn-marker geometry and full
  collision/avoidance remain separate work.

Relevant files: `asset_geometry.rs`, `content.rs`, `mining.rs`, `looting.rs`,
`loot_world.rs`, `target_verbs.rs`, `tools/import_asset_radii.py`,
`tools/import_resources.py`, and the committed JSON under `data/`.

The broad geometry documentation still describes some of this as pending;
reconcile `docs/ASSET_GEOMETRY.md` and `docs/ROADMAP.md` with this checkpoint when
resuming. Do not treat older test totals in those cumulative documents as the
latest validation result.

## Validation and work still pending

Completed against the latest source:

- **269 Rust tests passed**, zero failures; full run took about 220 seconds on
  this VM. Log: `diagnostics/resource_recovery_full_tests.log`.
- Clippy, all targets, with warnings denied passed.
  Log: `diagnostics/resource_recovery_clippy.log`.
- **55 Python tool tests passed**.
  Log: `diagnostics/resource_recovery_tools_tests.log`.
- Scoped mypy passed for the changed geometry importers/tests and mining smoke.
  This was not a whole-repository type check.
- Focused tests passed: 14 recovery tests and 7 geometry tests, including native
  and legacy cast restoration, invalid-plan rejection, nongroup rescue,
  surface-range cancellation, stock preservation and center-based tractor time.

Not completed for `207e2a0`:

- No build/deployment to the live VM server after this checkpoint.
- The new `smoke_mining.py --surface-boundary` probe has **not yet run** against
  this checkpoint. It requires native Prospect availability outside the old
  center-distance limit and exercises collection/restart/duplicate prevention.
- `smoke_jumpstart.py` passed earlier against the previous control implementation,
  including real NPC incapacitation, self recovery, warp and nongroup rescue.
  Rerun it against the new native math and strengthen exact cost/timing/repair
  assertions if claiming packet-level verification of those new formulas.
- CPU investigation is unfinished.
- Actual graphical recovery/invitation clicks are distinct from packet tests.

Earlier working foundations include native login, sector handoffs, docking,
equipment/stat effects, nine class presets, weapon/ammunition/journaled impacts,
NPC delayed shots and damage receipts, scan/cloak/factions, roaming and hate,
resources/mining, durable corpse loot, vendors/vault, crafting/refining and
group/cloak/follow/formation. This is **not a complete server**: missions runtime,
full abilities/AI, XP/progression, complete equipment effects and broader native
playtesting still need work. The public mission export preserves 364 missions
and 1,430 stages, but runtime was deferred by the user.

## Files that Git does not carry

The generated public JSON needed to run the server is committed. The following
ignored material must be deliberately preserved if the user wants it on the Mac:

1. **Private server state:** `C:\MACROS\acmacro\enb-server\state`.
   Keep the account files, per-account inventories/positions, combat/loot/resource
   ledgers, group state and sandbox loadout policy together. This contains local
   authentication material and must not be committed to Git or posted into chat.
   `state/clients/**` contains large Windows client installations/hard links;
   those and proxy certificates are not required to run the Rust server on Mac.
2. **Full SQL reference:**
   `C:\MACROS\acmacro\diagnostics\npc_source\sigseg1v_db_mysql_net7.sql`
   (about 11.2 MB). `Database (03-08).sql` and PostgreSQL reference exports are
   in the same directory. Importers read only allowlisted public content tables;
   do not execute or import private player tables as game-world content.
3. **Archived C++:**
   `C:\MACROS\acmacro\enb-server\reference\upstream\trunk`.
   Upstream is `therealKyp/Earth-and-Beyond-server`, pinned revision
   `3b46ad7c8baecca61adf8a0bedd2b23cb1936b9d`. The committed manifest and
   `tools/fetch_reference.py` can reproduce this reference on the Mac.
4. **Original asset reference:** installed Windows game data. The numeric radius
   export came from
   `enb-server/state/clients/buddy/game/Data/client/ini/cbasset.ini`; its hash is
   recorded in committed `data/asset_radii.json`. Full DAT/client assets are
   separate from those numeric exports.
5. **Diagnostics:** root `diagnostics/`, including the validation logs above and
   one-time migration scripts. Useful scripts include
   `refresh_mob_geometry.py` and `refresh_resource_geometry.py`; they verified
   metadata-only content changes. They are not production entry points.

Do not use an active directory copy as the final consistent game-state snapshot.
When the user is ready for the actual cutover, gracefully stop the VM server and
copy the final saved state as one set. No such shutdown or copy has happened yet.
Do not run two servers against the same writable state directory.

`server.local.json` contains Windows PID/path/command ownership metadata and must
not be reused to control Mac processes. Session checkpoints are bound to the
old bind address and UDP endpoints, with a 120-second resume limit. A host/IP
move requires normal login; do not promise transparent cross-host session resume.
Preserve the originals before deciding which transport metadata to rebuild.

## Mac layout and coordination

The other agent's current checkout is:

```text
/Users/pierre/Documents/code/acmacro
```

The user may move or rename it. Do not assume that path after relocation.
The Unity project is `enb-unity`; the portable Rust crate is `enb-server`;
Python packet code is shared through `enb-protocol`. Windows automation/client
helpers remain Windows-specific even though their source can be edited on Mac.

Coordination service is running on the Mac at:

```text
http://192.168.47.1:8765
GET  /messages?after=0
POST /messages  {"from":"server-agent","message":"text"}
```

The service source is `enb-unity/Tools/coordination_server.py`; messages are
retained under `~/Library/Logs/42 and Beyond/`. The explicit server stop/handoff
notification is message58. The Mac agent owns Unity and already builds and
tests the Rust server against disposable local state. Send operational status
and protocol notes here, not credentials or private state contents.

## Resume after the user finishes moving/renaming

1. Pull the handoff and verify the actual checkout/state/reference locations.
   Re-read `AGENTS.md`; keep the existing typed-code and ownership rules.
2. Rebuild on the Mac after a rename. `main.rs` uses compile-time
   `CARGO_MANIFEST_DIR` for defaults, so an existing executable may still point
   at the previous checkout. Pass explicit content and state paths.
3. Run the latest source against **disposable** state first; finish the pending
   mining/recovery probes. Keep the user's private state separate from tests.
4. Coordinate the actual host switch. The Windows native client/proxy can remain
   in the VM, but Net7Proxy must target the Mac's private IP. The current launch
   helper supports `--server`; do not blindly reuse its VM-server defaults.
   Bind/port availability and certificate/DNS wrapper behavior must be checked
   as part of that cutover, not assumed completed by this handoff.
5. Profile the optimized Mac server and resolve the saved corpse-world error.
   Continue the full implementation goal; do not call it complete after migration.

Example Mac commands **to adapt after relocation**, not commands already run:

```sh
cd /actual/new/repository
cargo test --manifest-path enb-server/Cargo.toml
cargo clippy --manifest-path enb-server/Cargo.toml --all-targets -- -D warnings
cargo build --release --manifest-path enb-server/Cargo.toml
./enb-server/target/release/enb-server \
  --bind 192.168.47.1 \
  --content /actual/new/repository/enb-server/data/asteroid_belt_alpha.json \
  --state-dir /actual/private/server-state
```

Do not run the final command until the user has completed the move and the
cutover/state ownership is settled. This handoff does not authorize another
agent to move/rename files while the user is doing so.
