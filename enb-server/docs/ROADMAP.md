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
  persist; projectile firing/travel and shared hit recovery remain unfinished.
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
2. Complete active item effects, launcher/ammunition combat, damage-type rules,
   hull progression and remaining imported stat effects. Native re-equipping
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
