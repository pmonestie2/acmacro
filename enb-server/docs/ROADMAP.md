# Full server reimplementation

The active project goal is a complete portable Rust Earth & Beyond server using
the archived C++ implementation, public SQL world definitions and client data.
This is still a development server. A working packet or imported record does not
mean the corresponding gameplay system is complete.

## Working foundation

- Local authentication, distinct durable accounts/characters, native client login.
- Authoritative impulse/warp, selection, sector handoffs, station docking/interiors.
- Native follow impulse 12 for visible peers/living MOBs: C++ speed tiers,
  authoritative facing/movement, manual cancellation and restart continuity.
  Unit and two-client protocol checks pass; graphical follow remains unverified.
- Opt-in revisioned local sandbox grants: 100 million credits, elite 200%-quality
  class-legal equipment and preservation of replaced items in the vault. Existing/new
  accounts share the grant path; repeated restart does not refill inventory.
- Short server replacement preserves authenticated UDP streams and active clients.
- Sector-transition journal commits destination and transport before publishing
  a handoff; retries/startup recover failures between location/checkpoint writes.
  Faulted-character shutdown saving does not skip other characters or groups.
- Saved gate opening/camera/completion state, 5.8-second C++ camera timing,
  queued early acknowledgments, incap/range/source validation and two-client
  replacement checks. Native Gate button Saturn -> Alpha is verified.
- 130 public space maps, 42 station layouts, 305 NPC placements, 6,650 MOB spawns.
- 21,199 deterministic resource nodes expanded from public field definitions;
  distance-based creation/removal, selection and warp destinations.
- SQL-filtered ore contents, prospecting windows, reactor drain, shared mining
  claims, prospect/tractor effects, cargo and exploration XP. Durable harvest
  receipts rebuild depletion; ordinary nodes respawn after 15 minutes and hulks
  stay depleted. Active casts survive short server replacements.
- MOB death attribution and persistent corpse births, native corpse actors and
  loot inventories, killer/group permission checks, credits, timed tractor pickup,
  shared claims, atomic cargo/receipts and cast/depletion recovery after restart.
  Two-client combat/loot smoke passes. Native corpse rendering and one-time
  credit collection are verified; native item pickup still needs a playtest.
- Shared sector combat and peer visibility; six-player groups, support abilities,
  formations and chat. Simulation advances once per sector, not once per viewer.
- Archived MobDPS/MobDAC level interpolation, species hull/shield split and first
  public weapon selection. SQL shield/damage/range modifiers are retained and
  honored (the archived handler omitted them). Three-roll damage dispersion has
  restartable random state. Public maps now carry combat metadata for all 6,650
  spawns; 4,274 resolve a weapon. Organic/energy deaths and leech attribution work;
  dead enemies cannot be revived by contemplation. Recovery rebinds changed
  content/caps while preserving existing damage and death state.
- JE skills/abilities, incap, self-jumpstart, native tow request and home station.
- Nine race/profession identities, public SQL class skill ceilings, racial starter
  gear, class ship models and weapon/device mount limits. Shared implemented
  abilities respect class ownership; other class ability handlers remain pending.
- Loaded ammunition inventory and native cargo-to-launcher loading, stack merges,
  weapon swaps and safe unloading on removal. Ammo metadata and AUX snapshots
  persist. Player launcher travel and durable shared impacts survive owner logout
  and server replacement. Native graphical launcher playtests remain pending.
- NPC beam/launcher launches use archived delays and frozen typed impacts.
  Sector queues retain each hit until HP plus a player receipt is committed,
  then acknowledge removal and shield reflection together. A session epoch
  survives process replacement but changes on real login, preventing old shots
  from striking a new authenticated lifetime. Queue backpressure still permits
  acknowledgment; quiet ticks avoid full-world snapshots. See
  [NPC projectile recovery](NPC_PROJECTILES.md). Native graphical verification
  remains pending; NPC ammunition AI and original boss mechanics remain open.
- Public catalog of 4,568 item templates with effects, restrictions, stacks,
  recipes/refine targets and weapon/reactor/shield/engine data. Starting mounts
  are separate from templates. Only owned item metadata is sent on login.
- Persistent cargo/equipment/vault inventory and credits, split/merge/swap
  transfers, mount/class/skill/quantity validation, trade/no-store vault rules,
  atomic replacement before publication and restart reconciliation.
- Refinery service with 207 inferred/validated input links, explicit SQL ore
  quantities, native lab/recipe/selection/filter packets, single/stack timers,
  atomic components/credits/products/trade XP, cancellation and durable jobs.
  Disposable protocol restart/replay checks pass; graphical refinery use is
  still unverified. Four unresolved links remain recorded for content repair.
- Analysis/manufacturing native packet handlers with owned analyzer escrow,
  learned recipe persistence, SQL costs/components and build-skill ceilings.
  Timed jobs freeze recipes/outcomes, commit atomically and reject replay across
  replacement. Disposable analyze/build/restart checks pass; graphical crafting,
  original balance, quality and overrides remain pending. Native-mode dismantling
  consumes owned escrow and applies a frozen component recovery plan; crafted
  builder names persist through cargo/vault/equipment and block recipe analysis.
- Equipment-derived reactor/shield capacity and recharge, impulse/warp/steering,
  free-warp drain, quality overrides, JE passives and supported passive item buffs.
  Equipped beams use real damage/range/energy/reload; slot firing and cooldowns
  survive restart. Native AUX carries capacities, speed, range and ready state.
- Station-scoped NPC conversations and vendor services: 305 NPC definitions,
  261 usable SQL talk trees, 269 vendor groups and 8,497 item rows. Native
  dialogue/trade packets, contextual quotes, atomic cargo/credit purchases and
  sales, stacked purchases, close/switch cleanup and open-vendor restart restore.
- Separate Windows client profiles, independent proxies/configuration/listeners,
  reusable local TLS certificates and owned process cleanup. Two native clients
  (Buddy and Third) logged in simultaneously and resumed after a server restart.
- Station multiplayer avatar appearance/pose/entry/departure packets, per-room
  visibility buckets, no idle repeats, movement capped at five updates/second,
  native scene-resync requests and checkpointed visibility across replacement.
  `/who` includes docked players. A two-client protocol smoke verifies room
  isolation, entry, movement, restart without duplicate spawns and logout removal.

## Next implementation work

1. Finish native NPC panel interaction. The native vault panel displays a
   deposited owned item correctly and drag withdrawal is now verified.
   Vendor packets pass disposable login/purchase/restart/sale checks; the native
   proxy-triggered test opens the inventory panel but has not rendered the NPC
   side. Vendor trade XP/acquisition history, finite shared stock, faction pricing and services
   beyond basic trade remain pending. Active item effects and remaining crafting
   behavior need implementation and native playtests.
2. Complete active item effects, NPC ammunition AI, full hull-upgrade
   progression and remaining imported stat effects. Player typed damage,
   miss/critical outcomes and class maximum hull capacities are implemented;
   broader native combat playtests remain pending. Native re-equipping
   still needs a fresh client/station check; /equip and /unequip use the same
   validated transaction path and provide a direct alternative.
3. Broaden native gate and self-jumpstart playtests. A native Saturn -> Alpha
   Gate-button transition now passes after correcting camera/completion timing;
   map selection, UI warp and impulse approach were also exercised. Gate jobs
   survive replacement before/after the camera. Cinematic approach/sounds, all
   other gates and the self-jumpstart button still need work. Slash jumpstart
   works; the client button has still reported an incap restriction.
   Native Space/autofollow impulse 12 has MOB/player following with behavioral
   and two-client protocol checks; graphical follow still needs a playtest.
4. Broader native mining playtests, MOB loot and full XP/level progression.
   Native Third opened the prospect panel and collected five Magnetite Ore.
   Prospecting and shared depletion pass behavioral and two-client protocol tests.
   2,228 resource nodes have no eligible ore in this SQL export and stay hidden;
   missing content needs investigation rather than invented item assignments.
   MOB drops now connect combat deaths to native corpse/loot handlers. Full combat
   XP, faction rewards, group loot policies and native item pickup need more work.
   The catalog resolves 8,506 public drop rows for
   2,042 MOB templates; 77 rejected rows remain explicit diagnostics.
5. Native refinery/crafting playtests, additional NPC services, trade progression,
   crafting quality/overrides and original dismantling balance. Refining currently uses
   the archived prototype 200/150 base/negotiated quote; full pricing is pending.
6. Mission/job content, progression, character creation/classes and faction rules.
7. Broader multiplayer: native interior-avatar playtests, group warp, guilds, trading, interest
   management/load tests and durable world state.
8. Runtime content/database loading, remaining DAT-backed definitions, operational
   tooling, migration/recovery, and Linux/macOS builds and playtests.

The SQL dump is currently an **offline importer input**. Rust runs compiled public
JSON catalogs with per-character JSON persistence; it does not yet use SQL at
runtime. Imported appearances and ordinary conversations/vendors now have
runtime handlers; mission progression and other NPC services are not complete.

## Verification boundaries

Rust behavioral tests cover protocol validation, simulation, recovery, multiplayer,
inventory rejection/atomicity, transport continuity and resource visibility.
Shared protocol tests last passed at 20 checks. Current Rust and Python tool
counts are recorded with each verification run; native playtests remain separate.
The nine-class/ammunition change passed 126 Rust tests, 34 Python tool tests,
Clippy with warnings denied and scoped Python type checks. A disposable server
accepted all nine class logins concurrently and published their distinct hull
models; the existing follow/group/cloak/formation/restart smoke also passed.
The subsequent MOB combat change passed 131 Rust tests, 35 Python tool tests,
Clippy and scoped importer/smoke type checks. The disposable Controller/PW fight
checks real attacks, multi-million shield capacity and content-refresh recovery;
the two-client loot/depletion/restart smoke also passes. This is protocol-level
verification; graphical boss playtesting and original boss AI remain pending.
Python tests cover importers and launch isolation; relay tests use local sockets
without driving any game. Disposable protocol smokes use temporary accounts and
state. Native playtesting is separate and remains incomplete for combat, every
ability, equipment transfers, all gates/stations and full multiplayer gameplay.

Local credentials, private profiles, runtime state, logs, build products and
archived upstream copies stay ignored. The root `chat_prompt.txt` edit belongs to the user; it is included unchanged
in the requested commit of all current changes.

The player combat integration passes 189 Rust tests and Clippy with warnings
denied. Scoped type checking passes for the launcher smoke. Beam/projectile
firing cones, missile facing independence, ammo/resource costs on misses,
critical flags and frozen-outcome crash recovery have behavioral coverage.
These checks do not replace graphical combat playtests.

Disposable native-packet launcher checks passed for both missile and projectile
weapons, including a real critical outcome, owner logout, in-flight replacement
and another replacement after resolution. The Controller/PW fight and changed
content-cap recovery also pass with live randomized player shots.

The NPC delayed-impact and receipt integration passed 202 Rust tests and Clippy
with warnings denied. Behavioral coverage includes archived beam/launcher timing,
launch and acknowledgment write failures, reflection exactly once, out-of-order
impacts, process replacement, target-sector departure and fresh-login epoch
rejection. Cosmetic launch effects are transient across replacement. These
results do not establish native graphical NPC projectile behavior.

The disposable NPC projectile smoke also passes: it records initial login frames,
observes a public PL effect before its frozen 12-second impact, cloaks the
target, replaces the server in flight, and verifies one native damage report
with no repeat after another replacement. The Controller/PW fight and refreshed
content-cap recovery pass. After correcting first-launch actor publication order,
all nine coordinator tests and Clippy were rerun successfully. Python tool tests
pass at 35; scoped type checking passes for both changed smoke modules.

Static navigation repair: compact proxy map flags now include the separate
AuxNameSignature.IsNav bit for ordinary nav actors. Cooper contained32 visible
map records, but the missing bit left its generic Type37 navs unmarked while
planets/gates could still appear. Session resume refreshes existing actors with
native name/signature AUX and navigation packets, without duplicate Creates.
Native graphical confirmation now passes on PW in Cooper: ordinary nav markers
appear on the minimap and selections reach the server.

NPC movement now publishes authoritative facing, velocity and movement IDs,
including chase/home/rest/death transitions. Legacy motion-less saves remain
readable. Expired legacy respawn countdowns are normalized and new countdowns
clamp at zero, repairing an observed startup rejection from two old actors.
The full209 Rust tests and Clippy pass. The server is running this build. The native client was relaunched after its
transport recovery expired; PW logged back into Cooper successfully.

Mission runtime is deferred as requested. The completed public-content import
preserves364 definitions/1430 stages, known XML repairs and unknown rule operands;
see MISSION_CONTENT.md. Six focused importer tests and scoped type checks pass; the complete Python
tool suite passes at39 tests.

Combat sensors now use native class base scan/signature plus equipped engine,
scan skill and supported modifiers. Player MOB visibility includes the3000m
MOB signature and200m retention margin; target selection, weapon preparation,
warp and follow use that same visibility. Per-MOB sensor contacts are separate
from hate and persist by authentication epoch. Cloak detection uses3*NPCcombat
level against caster overall level+5*trained Cloak; group recipients retain the
caster value. A fresh login cannot inherit old contact hysteresis. NPCs that
lose their targets return home using the existing prototype speed; full patrol
behavior and faction-aware acquisition remain unfinished.

The sensor integration passes219 Rust tests and Clippy with warnings denied.
Coverage includes all nine class bases, live visibility publication/target loss,
cloak variants/group inheritance, sensor hysteresis and checkpoint continuity.
Sandbox track levels remain the advertised50/50/50 until XP progression owns
levels. The local server is running this build. Disposable native-packet probes pass
for CL61 (cannot detect cloaked JE) and CL62 (detects it), including pending-shot
impacts after cloak and server replacement without duplicate damage. The
two-client group/cloak/follow/formation/replacement smoke also passes. Scoped
Python type checking passes for the extended NPC smoke.

Authenticated character selection now takes over a stale session immediately,
retiring old claims/casts/transport before issuing the replacement ticket.
Two scoped behavioral tests pass, including old comms/sector logout isolation
and invalid-slot preservation. Takeover also discards old queued input, support
actions, client events and replay responses before installing the new owner.

The faction catalog/PDA/acquisition and source-backed roaming integration passes
232 Rust tests and Clippy with warnings denied. Public spawn regeneration keeps
all 6650 actors and every static navigation record; 6643 positive-radius actors
now carry their original spawn regions, faction IDs and numeric aggression.
Seven zero-radius actors remain stationary. The build is deployed locally.
Focused faction/spawn importer tests pass (7); scoped type checking passes for
the four changed importer/test modules.

The disposable `smoke_factions.py` packet check passes for simultaneous friendly
JE and hostile PW observers, idle roaming, numeric tier-three acquisition,
immediate takeover with late old logout, and pursuit/attitude continuity through
replacement. Its scoped type check passes. This does not establish graphical
PDA rendering, field-guardian AI, kill faction awards or complete combat AI.

After integrating the Unity agent's group-warp fix, all 240 Rust tests and
Clippy pass. This includes the new six-case warning-shot suite and signed NPC
follow regression. The Python server-tool suite passes all 43 tests. The new
warning/follow changes and the group-warp fix are deployed locally. The same
faction/takeover/restart packet smoke passes against the rebuilt binary as well.
Native clients resumed across deployment and subsequent warp/cancel requests
reached the server; graphical warning-shot and group-warp playtesting remain
separate from these results.

Public MOB geometry now carries the installed native asset radii. Surface-based
NPC sensors, standoff, warnings, firing and flight, player weapon size reach and
follow stop distance pass the complete 250 Rust tests and Clippy. The numeric
catalog covers all 139 public MOB assets; the migration added only radius to all
6650 actors while preserving other content. All 47 Python tool tests pass;
scoped type checks pass for the four importer/test modules and extended NPC
projectile smoke. See ASSET_GEOMETRY.md for provenance and remaining geometry
scope. The build is deployed locally; both native sessions resumed. The isolated
CL62 projectile smoke passes with radius500 and a content change to1000 during
replacement: its already-launched shot retains the10500ms deadline, damages the
cloaked target once and does not replay after another replacement. The first
probe exceeded its15-second startup readiness window despite logging ready;
the owned-process helper now allows30 seconds and the rerun passed. This result
does not establish graphical collision behavior. The Unity agent's89d730c update
was fetched and integrated without server-source overlap.

Loot investigation confirmed real drops already work; the UI smoke's Achilles
fixture can legitimately roll no items. The corrected two-player backend smoke
requires public template2010's guaranteed item5931, then verifies native opening,
tractor competition, replacement, owner cargo award and durable depletion. It
passes against the deployed radius build. The relocated fixture clears its old
roaming region to avoid a tractor-range race. Corpse-birth diagnostics expose
the server's actual generated stock; see LOOT.md. Diagnostics build c968c73 is
deployed and both native sessions resumed. All ten scoped loot tests and Clippy
pass. The UI
agent independently confirmed a decoded Present item and cargo award through
the C# client using the guaranteed-drop fixture, without wire changes.

Original-client player selection was missing native target verbs: the server
accepted group packets but sent an empty0x5c action list for a selected peer.
Live public peer contacts now advertise Group/Invite (verb4) for living ships
and Follow (verb13) within its native1500m surface range. Incapacity disables
those actions; cloaked/departed contacts lose them. Eligibility is rebuilt from
current coordinator snapshots rather than saved peer health. Two scoped verb
tests and Clippy pass; the multiplayer packet smoke now requires the enabled
Group verb before exercising invitation/acceptance. Build3c76b2f is deployed;
the extended two-client packet smoke passes Group visibility, native invitation
and acceptance, follow, formation, group cloak, replacement and leave cleanup.
The original client resumed, but actual graphical invitation clicks remain
separate from that packet verification.

Native Jumpstart now advertises verb12 and routes action26 through ability65,
including self-incap recovery and visible nongroup rescue. Completed recovery
rechecks a fresh peer snapshot; other support remains group-only. All 258 Rust
tests and Clippy pass. The disposable native-packet test uses real NPC damage,
self recovery, warp travel and nongroup PW recovery, and passes. Catalog
cost/timing and missing recipient confirmation remain documented in
NATIVE_JUMPSTART.md. Build b4a7363 is deployed locally; the active native toon
session resumed without relogging.

Restart tooling now compiles in a separate cached target while the current
server remains playable, validates ownership again, then installs the complete
binary after graceful shutdown. Compile failures leave the old server running;
live readiness timeouts never launch a duplicate. All 55 Python tool tests and
scoped type checks pass. See SERVER_RESTARTS.md.
The first live staged handoff built from the warm cache in 0.09 seconds and the
native session's measured outage was 3035ms, excluding the earlier compilation
that completed while the old server stayed online.

All 12 local sandbox inventories were checked for the user's full-weapons request:
every class-allowed hardpoint is filled (JE 3, PW 6, the other classes 4 or 5). No
gear replacement or revision reset was needed; existing gear/cargo was preserved.
