# Playable server development

The server is independent of the automation macros. Rust code runs on ordinary
TCP/UDP sockets and is intended for Windows, Linux and macOS. Windows client
launching and supervised UI diagnostics belong in the Python tools.

## Work order

1. Session continuity through server restarts, and reliable warp cancellation.
2. Asteroid Belt Alpha MOB spawns, combat, death and respawn.
3. Jenquai Explorer skills and active abilities with visible effects.
4. Multiple authenticated players, shared sector simulation and grouping.
5. Remaining playable-server gaps, including sector transitions and station services.

This document records implemented behavior and limitations as each stage lands.
Tests against disposable servers and checks in the graphical client are distinct.

## Space-session continuity

`GameState` coordinates accounts, groups and shared `SectorCombat` instances.
Each `PlayerSession` owns its authenticated transport and private character state.
Its child module `session_checkpoint.rs` writes
`session.local.json` in the account's state directory every 250ms in space, and on
graceful shutdown. This ignored operational file preserves the exact UDP peer,
game clock, sequence and replay queue, motion, warp lifecycle, and resources.
Replacement processes restore only the same bind/account/sector within 120s.
Docked sessions and authorized pending handoffs are checkpointed too. Docking
clearance, source/destination, nonce and transport state survive short outages.
An ordinary logout deletes the checkpoint. Expired or incompatible checkpoints
require normal login. Account passwords and pending login tickets are excluded.

Simulation pauses during an outage; the wire clock advances by the outage so
client timestamps remain monotonic. Packet resends cover datagrams constructed
just before shutdown. This preserves an established **game UDP session**; it
does not migrate a TCP login exchange that was halfway through authentication.

`state/stop.request` requests graceful shutdown without platform-specific signals.
The listener registry checks shutdown under its mutex, preventing an accepted
socket from being registered after shutdown has closed the existing sockets.

## Warp controls

`WarpAvailable=2` describes an installed usable drive, including during travel.
Setting it to zero while warping disables the client's Q cancellation control.
Charge/travel/recovery use `PrivateWarpState`, `GlobalWarpState`, and trigger time.
A second warp request cancels immediately; an explicit impulse brake also cancels.
Selection sends both SetTarget and ShipIndex.TargetGameID, matching the C++ path.
Final arrival stops 1500m from a gate or 4500m from a station instead of flying
inside its model. Intermediate waypoints are passed through normally.

Run `.\restart_local_server.cmd` to build and replace the owned local server.
The helper checks executable/PID birth, asks for a graceful stop, and keeps a
fallback executable if compilation fails. It never closes the game or proxy.
`tools/smoke_restart.py` verifies replacement with an unchanged disposable client.

## MOBs and combat

`tools/import_spawns.py` resolves public `sector_objects_mob` and
`mob_spawn_group` entries to `mob_base` templates. Alpha has 40 deterministic
instances. Zero-count groups stay disabled. IDs are stable per spawn slot.
Templates retain names, levels, models, scale, HSV and source identities.
The same index supplies 6,650 MOB instances across 95 of the 130 space maps.
Fourteen referenced templates have no model in the public dump; per-sector
`missing_mob_models` metadata records those omissions. Mirrored models retain
their signed scale. Sector simulation is created only when a player visits it.

MOBs use Create.Type=0, matching `MOB::MOB`; Type=1 is a player actor and produces
incorrect Default labels without an avatar association. A player receives
dynamic actors within 20km, with removal when they leave range. Shields absorb
damage before hull, death removes the actor, and its original spawn timer
recreates it. Beam effects and current health are sent to the client. Cloak and
contemplation suppress aggression; damage breaks contemplation.

Damage, shield/hull scaling and recharge are currently prototype balance, not a
complete reproduction of equipment, faction hate, environmental damage or loot.
The JE Beam Weapon passive applies the C++ 25%-per-rank damage increment.

## Skills and abilities

`data/je_skills.json` contains 18 max-rank JE skills and 27 eligible ability IDs.
`tools/import_skills.py` joins public skill-level mappings to installed client
descriptions/costs. In particular ability 0 is Advanced Cloak, and JE Fold Space
stops at rank 5. The mostly empty skill_id fields in skill_abilities are not used
to guess ownership. Player RPG levels are 50/50/50 for this development character.

`skills.rs` defines the catalog and serializable runtime state.
`ability_actions.rs` owns target/range validation, energy charging, casts,
cooldowns and effects. Rejected activation spends no energy; an accepted cast
spends once. It implements cloak/drain/uncloak bonus, contemplation, environment
mitigation/reflection/energy conversion, self/enemy fold and shield-only leech.
Group cloak, shields, energy sharing, friend fold, jumpstart and wormhole support
are typed support actions delivered by `GameState`. Membership, sector, distance
and target state are rechecked when a cast completes. Leaving a group or moving
outside the caster's cloak field removes inherited cloak.
Supported passive equipment, crafting and mining permissions now feed runtime
services. A max skill grant alone does not implement other missing effects.

Cast/cooldown/buff state survives server restart. The native cast bar and skill
recycle fields are updated with one-bit AUX diffs so inventory/other skills are
preserved. Exact durations/drain amounts for incomplete archived handlers remain
prototype parameters, distinct from the imported client description.

## Sector handoff

`sectors.rs` caches 130 validated public space maps. `sector_transfer.rs` handles
the JoinData/server-handoff flow for gates and wormholes and persists the new
sector. Gate exits use the return gate when available; wormholes use the sector
origin. New sector login resets the packet stream after the proxy's master join.
Three maps with missing skyboxes explicitly use fallback asset 220; the accented
Kastör label is exported as Kastor with original_name retained. Planet landing behavior remains incomplete. Mining, loot and manufacturing
now have runtime handlers described below. Inventory/vault and
vendor wire operations are described below; native service UI remains partial.

## Docking and interiors

`import_stations.py` joins public starbases, exterior objects, rooms and terminals
into 42 station layouts. Room client indices come from room type, not SQL IDs.
`content.rs` validates stations separately from space maps. A station exterior
offers Dock, checks the selected target, live hull, impulse state and 5000m range,
then sends camera state 5. Client action 7 completes the authorized handoff.

`MasterJoin.FromSectorID` and source names must describe the departing sector.
Leaving them zero passed the original loop-gate smoke but hung a real native
cross-sector transition. The corrected packet was verified by docking into ECS
Dauntless, launching into Alpha, then docking again without restarting the client.

Station login sends native StarbaseSet/LoungeNPC room, terminal and NPC layouts,
avatar/ship initialization and Start. `/launch` is a convenience command using
the same station departure handler as native action 1. Ship motion and abilities
are disabled indoors. Avatar movement has independent validated pose state;
walking cannot overwrite the ship's saved sector position. Restart while docked
preserves the live client. The public layouts include 305 NPC placements with
their original 241-byte AvatarData appearance, names, room, location and booth.
The importer follows StationLoader's field order and preserves the split first/
last names. NPC dialogue/vendor handlers are described below; other services
remain unfinished. Interior-avatar wire visibility is implemented below, with
native multi-avatar rendering still unverified. NPC appearance/placement is unit-validated against the packed
layout; visiting every NPC in the graphical client remains unverified.

## Multiplayer and formations

`AccountDirectory` assigns unique durable player IDs and separate character state
directories. `GameState` authenticates accounts and pins each selected session to
its registered endpoints. Password hashing happens outside the simulation mutex.
`SectorCombat` advances once per sector, regardless of player count; connected
players see one shared set of MOB health, AI decisions and respawn timers.

`GroupManager` implements six-player invitations, acceptance, decline, leader
permissions, leave, kick, disband and persistence. Native group HUD AUX describes
each member separately. Formation selection does not move unwilling members:
each uses Form Up within 5000m of the leader. Opcode 0x41 carries leader-relative
offsets; the server rotates the same offsets for authoritative combat positions.
Manual steering/thrust detaches; leader warp, handoff, logout or incapacitation
releases followers. Group warp is not implemented. Formations survive a short
server replacement along with their sessions.

Peer snapshots contain public ship state, never another player's private AUX or
inventory. Chat supports local/sector/group and selected-player private messages.
`/help` lists the local social and ability commands. Guilds are not implemented.

## Current verification

70 Rust tests pass, including restart continuity, native ability IDs, cast/target
validation, cloak/leech effects, fold distance and wormhole destination persistence.
Disposable login/movement/warp/gate and process-replacement smoke tests pass.
The two-client smoke checks distinct IDs, invitations, group cloak, formation
following, both UDP streams across replacement, manual formation detachment,
and revocation of inherited cloak after leaving.
Python tool tests and the shared headless protocol tests pass. Scoped mypy checks
cover the modified session and server importer/restart/smoke modules.

In the graphical client: selection and movement work, Q cancellation was observed
during Traveling and logged as Cancelled, and multiple server replacements kept
the same game/proxy processes in space. The native skills panel shows max JE
skills; activating Cloak makes the ship transparent. A separate authenticated
Buddy character joined the native group HUD, cast Group Cloak on the graphical
player, and survived a server replacement alongside it. Native station entry,
walking, launch, re-entry and restart while docked were also observed. Full native
MOB combat, every ability, formations and other sector routes need broader playtesting.
Linux/macOS portability is an implementation target, not a completed OS test.

## Resources, recovery and inventory

`import_resources.py` expands public field definitions into deterministic resource
positions and models. `session_resources` streams only nodes within 20km, tracks
known IDs through restart, and removes nodes leaving visibility. Resource AUX is
the C++ NameResource layout, not ShipIndex. Alpha has 616 nodes; ABB has 210.
Ore-frequency metadata is retained in exports but prospecting, contents, mining,
depletion and respawn are not yet implemented. Reproduce both the full catalog
and Alpha with `import_universe.py DUMP --default-sector-output
enb-server/data/asteroid_belt_alpha.json`.

`recovery.rs` handles native incap dialogue/tow choices, home-station recovery,
and slash convenience commands. Self-targeted Jumpstart can cast while dead;
`/jumpstart` was confirmed in the native client. The native skill button still
needs investigation for its local incap restriction.

`inventory.rs` owns cargo/equipment instances separately from public templates.
Opcode 0x27 parses six big-endian words, checks player identity, state, bounds,
quantity and both mount directions before constructing a candidate. The session
replaces `inventory.json` before changing authoritative memory or sending AUX.
Restart reconciles inventory even when disk state is newer than the transport
checkpoint. Inventory AUX updates preserve mounts and other unrelated fields.
Native dragging verified equipment-to-cargo and cargo-to-cargo transfers, their
visible AUX updates, and persisted state. Cargo uses ten-field AuxItem records;
equipment uses fourteen-field AuxEquipItem records. A packed fixture test checks
their flag bytes and distinct structure/quality ordering. Native re-equipping
has not yet been verified: the client has refused to submit that drag in space.
`items.rs` now loads 4,568 public templates, with explicit effect packets,
restrictions, stack limits, flags, ammo types and recipe/refine references.
`import_items.py` joins those public SQL tables without reading account data.
Some content has category/subcategory -1 or visual effect -1: those signed
sentinels are retained, rather than rejecting the whole database. Loadout slots
live separately in `je_loadout.json`; templates have no slot field.

Inventory supports partial stack moves, capped merges and whole-stack swaps.
Merging preserves quality/structure/builder identity. Vault transfers use container 3
and PlayerIndex.SecureInv (96 ten-field items). No-store/trade items cannot be
deposited, including through swaps. Credits share the atomic inventory file.
Old inventory files default to an empty vault and the original 10,000 credits.
Cargo-to-vault, vault-to-cargo and vault rearrangement are implemented;
vault-to-equipment is rejected. Native vendor UI interaction and active item
effects remain unfinished; manufacturing transactions are described below.
The disposable wire smoke exercises equipment-to-cargo, vault deposit/withdrawal
and re-equipping with durable state checks. Gameplay denials return a message and
authoritative slot refresh; malformed/foreign-owner requests remain protocol
errors. Native re-equipping remains a separate unresolved UI verification item.

The Windows restart helper pins/query-checks process identity and waits on a
signaled process handle when image queries race with exit. Access denied alone
is not treated as termination. A previously observed image-query exit failure
is covered by mocked lifecycle tests; a subsequent live replacement resumed
both native sessions in under one second without replacing their game/proxies.

## Equipment-driven simulation

`equipment_stats.rs` derives cached parameters from owned equipped instances,
public templates, quality and JE passive skills. Reactor/shield capacities and
recharge, impulse, warp, acceleration, scan, signature, steering, deflects and
supported passive item modifiers feed simulation and AUX from the same record.
AuxPercent shield/energy fields encode normalized fractions, while simulation
and hull values use raw points. The previous raw-point encoding could pin bars
at full charge; player/peer/MOB encoders now use capacities consistently.
Duplicate equipped modifiers take the strongest value per stat/operation, matching
the archived ITEM_EFFECTS grouping. Engine-top-speed/turn-rate aliases are mapped
to their runtime stats; energy reductions and warp recovery use the proper
additive/multiplicative operations. Unsupported imported modifiers remain data.

`ItemTemplate::number_at_quality` implements the piecewise quality curve and
integer truncation. AUX InstanceInfo contains those same values. A starter JE
has 142 energy, 45 shield, 192 impulse and 2700 warp with Navigate 7. Removing a
reactor stops energy recharge; removing an engine stops impulse/warp. Equipping
larger capacity preserves charge rather than refilling it. Shield recharge runs
while alive, including indoors. Free warp drains energy and ends at depletion or
the sector boundary; the old arbitrary 50-second cutoff is gone.

Equipped beams have independent energy cost, range, damage and reload timers.
Action 9 toggles weapons; native 0x5d activates individual beam slots. ReadyTime,
TargetRange and ItemState are updated in AUX. Toggling cannot bypass reload;
restart preserves firing/cooldowns. Dead targets stop auto-fire. Imported launcher
templates currently report no ammunition and do not fire; launcher/ammunition
handling and active item buffs remain pending. MOB beams still use prototype
energy damage and AI balance; complete damage-type combat is not claimed.

Peer parameter deltas update changed capacities/speeds without private inventory
or energy updates. A cache avoids resending unchanged stat records. Known peer
actor IDs survive restart to avoid duplicate Create packets; a real new sector
login clears that visibility state. `/equip CARGO_SLOT MOUNT_SLOT` and `/unequip
MOUNT_SLOT` use the same bounded, class-checked inventory transaction as 0x27.

In the native client, removing the test JE's engine displayed zero impulse/warp;
`/equip 0 2` restored the icon and displayed 192/2700, with the accepted move
persisted. Both native sessions remained connected across replacement. Native
in-space drag-to-equip still submitted no packet in the supervised test and
remains unresolved. The updated disposable smokes pass inventory round trips,
impulse/free warp and multiplayer/formation/cloak/restart behavior. High-rank
group tests explicitly equip public level-nine reactors in their temp accounts.

## Public NPC dialogue and vendor transactions

`import_npcs.py` reads only public NPC/vendor tables, joining placed station IDs
and known item templates. `data/npcs.json` contains 305 NPCs, 261 parsed XML talk
trees, 269 vendor groups and 8,497 offer rows. Five malformed trees use a basic
greeting; 68 missing item references and 43 duplicate rows are excluded with
diagnostic counts. The importer preserves trade markers, branch IDs, names,
prices and the original stock-availability values. Mission flags above trade
are rejected rather than treated as mission completion.
Three placed NPCs advertise trade without a public vendor group (446, 447, 467);
their conversation explains that inventory is unavailable and grants no trade
authority. No stock is invented for these missing content links.

`npcs.rs` caches immutable content. `npc_services.rs` validates station placement,
owner, current branch and vendor state. Native request 0x4e/action 4 opens a
conversation; 0x55 selects an offered branch. Trade sends item templates (0x25),
PlayerIndex.VendorInv (128 ten-field items), contextual owned-item prices, then
the original 0x56 actions 5/3, dialogue 0x54 and action 6. Close 0/230, a new NPC,
launch and logout revoke the vendor context. A short replacement restores the
pinned NPC/node/vendor from the session checkpoint and republishes its catalog.

0x27 retains its six big-endian u32 fields: owner, source container/slot,
destination container/slot and quantity. Vendor container 4 to cargo 1 purchases
the advertised slot; cargo 1 to vendor 4 sells its actual item. The client cannot
supply a price or arbitrary item template. Purchases merge identical undamaged
stock then fill free slots, rejecting insufficient space or credits for the whole
request. Sales accept pre-existing cargo as well as newly purchased items.
`commit_inventory` derives stats/quotes before writing and replaces one JSON
file containing both credits and inventory before changing memory or publishing
success. Duplicate UDP sequences are ignored, including after checkpoint restore.

Positive row prices are explicit overrides, -1 forbids a direction, and zero
uses the declared group base-price multiplier (rounded down). BuyOnlyList is
honored; unlisted trade goods have no buyer. These declared policies deliberately
replace inconsistent legacy defaults which ignored group multipliers and bought
unlisted trade goods at full base cost. Quotes are transient, cleared on closing,
and never written into owned instances. Vendor trade XP/acquisition history, reputation prices,
finite shared stock and full mission/crafting services remain future work. As in
the archived NPCTradeItems path, nonzero stock currently means reusable stock.
Disk replacement is not a multi-account/world SQL transaction; abrupt-crash
exactly-once receipts beyond the transport checkpoint are not implemented.

`smoke_vendor.py` uses a disposable account and real native dialogue/trade wire
formats. It verifies the greeting/token replacement, purchase, correct durable
credits, open-vendor server replacement on the same UDP socket, sale and closed
vendor rejection. Shared `SpaceSession.enter(..., allow_station=True)` explicitly
permits this station test and retains raw 0x52 layout separately from its map;
the web client's default space-only entry remains unchanged.

Native local Third warped to ECS Dauntless and completed docking without a
relog. Through its pinned local proxy the NPC/trade requests reached the new
handler and opened the cargo panel, but the NPC side did not render from this
remote initiation in the hangar. Native booth clicking, full conversation/vendor
panel behavior and UI buy/sell therefore remain unverified. The successful
disposable protocol tests do not establish those graphical behaviors.

## Room-scoped station multiplayer

`station_actions.rs` accepts only the authenticated player's native 0x9d pose
and 0x9f room reports. `StationPose` has its own room/orientation/position/action;
it never changes the ship's space coordinates. On room change, the old pose is
discarded until new coordinates arrive. New scene action 0x41 requests a fresh
list, including stationary peers. Bounded finite pose data and valid room IDs
are checked on input and checkpoint restore.

`GameState::advance` groups public station snapshots by (station, room) once per
tick. `station_peers.rs` sends each recipient only that room, excluding self.
Entering peers receive AvatarDescription 0x61, room change 0xa0 and pose 0x9e;
changed poses are limited to five updates/second. Idle poses produce no repeated
appearance or motion packets. Departing peers receive 0xa0 with NewRoom=-1.
No ship Create, equipment, resources or private inventory is sent for avatars.
The C++ incoming pose has room second; its S2C response has room last, and the
explicit encoder preserves this difference.

The last delivered per-recipient poses survive short server replacement.
New sector login/handoff clears that cache; unchanged restored scenes retain
it. A scene-resync request still forces appearance/pose publication. `/who`
uses all logged-in sessions, including docked players, rather than space
snapshots. Formation/space abilities remain space-only.

`smoke_station.py` authenticates two disposable clients inside ECS Dauntless and
checks appearance/room/pose delivery, idle silence, movement-only updates,
departure removal, cross-room isolation, new-room discovery of idle avatars,
both UDP streams/visibility across process replacement, subsequent movement,
logout removal and `/who` while docked. Rust tests cover exact wire field order,
change detection, checkpoint cache restoration and room-change pose clearing.
The smoke uses the real shared protocol client, not graphical input. Native
multi-avatar rendering and load tests with many characters remain outstanding.

The regular local Third client walked from the hangar through the common room
to the commerce room. Genia Portman's booth, name and animated appearance
rendered. A temporary starter-beam deposit via the guarded local proxy appeared
with its correct icon in Personal Vault. Dragging that beam from the native
vault panel into cargo sent container 3 to 1, and the persisted inventory matched.
`/equip 0 3` restored the original beam mount; no test cargo remains in the vault.
Native drag-to-equip and NPC conversation clicks remain separate open issues.

## Shared resources and prospecting

`import_resources.py` joins public sector base ores and field additions with
`asteroid_content_selection`. Candidates must match the resource category and
field level or one below. Zero frequency is the legacy default weight (all
17,247 base-ore rows use zero); negative entries are excluded. Ancient-device
restrictions and level hues follow C++. Of 21,199 generated nodes, 18,971 have
eligible contents; 2,228 without eligible definitions remain hidden.

`resources.rs` generates reproducible slot contents from node ID and generation.
`GameState` shares one field per sector, registers harvest counters from every
loaded account, and advances respawn once per field. A claim reserves a node for
one miner. `mining.rs` validates explorer skill, ship state, range, reactor and
cargo capacity, then advances prospect/drain/tractor phases. Movement, target
changes, cloak, warp, logout or incap cancel the claim without awarding ore.
Beam/tractor events are broadcast to nearby viewers in the originating sector.

Cargo, cumulative per-slot harvest counters and exploration XP are committed in
the same inventory replacement. Shared depletion is installed only afterward.
Restart reconstructs remaining stock from those counters, so selling or dropping
ore cannot replenish an asteroid. A stale transport checkpoint detects an
already committed harvest and discards its cast. Respawn generation is written
before exposing new stock: ordinary nodes wait 15 minutes after full depletion;
hulks stay depleted. This is process-crash protection using JSON replacement,
not a SQL transaction or a power-loss durability guarantee. Changing ore pools
or deleting generation files requires migration of existing harvest receipts.

Native action 17 opens/closes the prospect panel; inventory source 18 starts
mining. As in C++, only its source slot selects ore: destination and quantity
fields are ignored, and actual collection always resolves available cargo space.
Resource AUX uses the 40-slot harvest inventory and the native empty
sentinel -2. Prospect and tractor packets use proxy formats 0x2012/0x2013.
`target_verbs.rs` distinguishes UI verb IDs (Gate 10, Dock 8, Prospect 9) from
action numbers (18, 28, 17), with enabled attribute 0 and out-of-range attribute
2. Availability updates as the ship moves, without resending unchanged lists.

`smoke_mining.py` authenticates two disposable clients, opens native-format
prospecting interfaces, verifies shared beam effects and exclusive claims,
replaces the server mid-cast on the same sockets, then verifies shared tractor,
matching cargo/XP/receipt and no duplicate reward after another replacement.
Unit tests additionally cover cancellation, failed inventory writes, stale
checkpoints, shared depletion reconstruction and persisted respawn. These
protocol checks alone do not establish native graphical mining behavior. Mining XP is
stored; development exploration level remains 50 until progression is added.

Native Third launched from ECS Dauntless, selected the station, and clicked the
HUD Dock icon after the verb correction. The client sent its docking sequence
and entered ECS Dauntless again. Gate-click playtesting remains outstanding.

Native Third then warped near a level-two asteroid in Alpha, selected it and
clicked Prospect. The panel rendered Magnetite Ore icons, names, levels and
five-item stacks. Clicking the first stack completed mining: inventory contains
five item 2845, its harvest receipt records five removed from original slot zero,
and the server recorded 21 exploration XP. Other native mining cancellation and
respawn scenarios remain covered by protocol/unit tests rather than UI evidence.
A subsequent click collected the remaining five Zinc Ore (2866) from original
slot one, confirming the client's compacted display still sends the correct
underlying slot. The exhausted node left visibility and its panel closed.
Resource warp arrival now stops 900m from the center instead of 100m, avoiding
the observed camera placement inside the model while staying in prospect range.

## MOB loot content, corpses and native collection

`tools/import_loot.py` reads only public `mob_base` and `mob_items`, resolving
item IDs against the compiled item catalog. `data/loot.json` contains 2,042 MOB
templates and 8,506 drop rows. Another 77 rows are retained with rejection reasons,
including missing items/MOBs and unsupported quantities. One source MOB has type
-1; the catalog keeps its type unspecified rather than inventing one.

`loot.rs` validates immutable drop definitions and generates reproducible stock.
The archived six-slot attempt-count distribution, quantity cap/merging rules,
husk models and structural/cybernetic credit formulas are preserved. SQL chance
percentages are interpreted literally with uniform percentage rolls, preserving
fractions. This intentionally fixes the archived loader's float-to-byte cast and
comparison of percent values against a four-roll value below one. `usage_chance`
and item type are retained as metadata; MOB equipment use is not implemented.
Three usage-chance values exceed 100 (110, 500, 1000); those raw values are kept
and do not invalidate their independently valid loot probabilities.
`world_random.rs` supplies the same fixed SplitMix64 that resource generation
previously owned; resource seeds and sequences are unchanged.

`loot_world.rs` stores corpse births and spawn/death generation identities in one
atomic world-file replacement before exposing stock. Repeating the same death
does not generate another corpse. IDs are sector-local, monotonically allocated
in a separate range; receipts combine sector and corpse ID into a nonaliasing
key. Corpses expire after five minutes; the credited killer/group has a 150s
private window, released by opening. The caller must resolve group membership
from authoritative game state. Exclusive claims exclude concurrent looters.

`Inventory::collected_loot` creates one candidate containing received cargo,
credits and a cumulative collection receipt. Duplicate awards and receipt
regression are rejected; full cargo leaves stock unchanged. A prepared
`LootCollection` holds an exclusive mutable borrow of the world until commit or
drop, preventing another award from preparing against the same stock while the
inventory write is in flight. The session must durably commit that inventory
before calling the collection's infallible `commit`. On restart all accounts'
receipts must rebuild shared depletion before publication. This protects against
process interruption between cargo and shared-stock mutation; it does not promise
filesystem power-loss durability or runtime SQL transactions.
Receipts beyond the world's allocation cursor reject a missing/rolled-back birth
history, preventing silently recycled corpse IDs from aliasing earlier awards.

`Mob::damage_by` attributes lethal beam/reflect damage and advances the spawn's
death generation. The coordinator captures deaths before MOB respawn advancement
and after reflected damage. Births persist their death timestamp with the corpse;
recovery reconciles fresh or stale MOB checkpoints against this durable history.
A recently killed MOB stays dead; an elapsed respawn timer does not start over.
One shared loot world attaches to each space sector, and all accounts' receipts
are registered before publication, including accounts currently offline or away.

`looting.rs` streams nearby native type-25 corpse actors, with the archived Husk
AUX envelope (distinct from AuxHarvestable) and empty 20-slot/loot 40-slot nested
inventories. View changes are limited to five updates per second; unchanged
selected contents are cached. Target verb 3 enables Loot within 6000m. Native
0x51 permission requests receive 0x8c with a network-order corpse ID; opening
awards credits once and releases the private lock. Native action 1 also toggles
the loot window. Container 6 or 18 selects the original corpse slot for pickup.
Requests are deferred through `game_actions.rs` so group membership is refreshed
after preceding membership events, rather than trusting an input-time cache.

Collection requires a living ship outside warp and within 2000m. One shared claim
reserves a corpse while its timed 0x2014 tractor effect is visible to nearby
players. Cargo is preflighted and checked again at collection; the inventory and
receipt commit before stock changes. Target changes, leaving range, incap,
warp, logout, window close or handoff cancel without awarding/removing stock.
Cast state and known corpse IDs survive short replacement; receipts discard an
already completed stale cast. `harvest_visuals.rs` shares mining/loot effects and
preserves their originating sector during handoff cleanup.

`smoke_loot.py` uses a real public Ishuan Revenant Young and a real level-nine
beam/reactor in a disposable reduced map. Two authenticated clients observe one
kill/corpse, enforce initial private ownership, open through 0x51/0x8c, share the
timed tractor, reject a competing claim, survive replacement on unchanged UDP
sockets and award only the collecting character. Depletion removes the corpse
for both viewers; another replacement neither awards again nor resurrects the
killed MOB. `smoke_support.py` owns bounded startup and failed-start cleanup for
the loot and mining fixtures. These checks do not drive graphical clients.

Native Third killed a Saam Lo Elite Red Dragon with temporary test equipment.
The wreck model, corpse name and Loot icon rendered. Clicking native Loot while
cloaked collected 244 credits once and removed the empty wreck. This particular
kill generated no items, so native item/tractor collection remains unverified;
its full server/transport lifecycle is covered by the disposable smoke above.
Native test gear is restored after testing. Combat XP/level progression, faction
rewards, advanced group-loot policies and floating loot on cancelled tractors
remain outstanding; cancellation currently retains stock in the corpse.

No production or local game was driven by loot unit tests. Behavioral tests cover
catalog validation, reproducibility, fractional chances, birth write failure,
ownership/public timeout, exclusive claims, duplicate receipts, reload recovery,
conflicting recipients, full cargo, corpse expiration, native permission/tractor
flow, cast restart/stale-award detection, cancellation and failed inventory writes.

## Refinery recipes and terminal service

The public `item_refine` table is empty. The archived C++ `SetRefineInfo` extracts
"Refines to" product names from descriptions, then supplies two input components.
The importer now restores those links but retains explicit SQL recipes rather
than overwriting their quantities: Magnetite needs five Magnetite Ore, for
example. Only products without a recipe use the archived two-input fallback.
It also accepts "Refines into" descriptions, resolves unique names, and refuses
conflicting/nonhomogeneous recipes. There are 207 usable links; four unresolved
entries are recorded in `data/items.refining.json`. Manufacturing recipes remain
distinct from these automatic refinery links.

`refining.rs` loads those validated recipes, counts repeated components correctly
and prepares one atomic inventory candidate with consumed ore, debited credits,
admitted products and trade XP (50 times product level per iteration). Capacity
is checked after consumption, so refining can free its own output slots. Stack
quantity calculation considers discontinuous capacity changes as input stacks
empty; it is tested against brute-force enumeration. Prices currently preserve
the archived prototype base quote 200 / negotiated cost 150. Normal-quality
products are deterministic; full pricing and level progression are unfinished.

`production_service.rs` implements native station lab setup: manufacture ID is the
player ID OR MANU_TAG (bit 31), set by 0x7f, and actor type is CREATE_MANU_LAB 0x27.
ManufacturingIndex AUX has 21 fields, preview inventories and a one-bit 500-entry
KnownFormulas list. Only currently carried refinery inputs populate that list;
level filters, removed rows and completed-state validity are refreshed without
idle repeats. Item metadata is deduplicated when previews repeat an input.
Native requests 0x79/0x7a/0x7c/0x7e/0x80 validate ownership, length, station,
recipe and filter bounds. Manufacturing/analysis modes explicitly report pending
implementation rather than creating products without materials or knowledge.

A three-second pending job is persisted in `inventory.json`. Components and
credits remain available during that interval, then are rechecked and committed
with output/XP and job removal in one file replacement. Closing the terminal
cancels without charge. Undocking/handoff is blocked until completion or cancel;
logging out preserves the job for the next login. If materials/space/credits or
the recipe change before completion, the job stops without consumption. Disk
write failure preserves both the job and the unawarded stock for recovery.
Each job snapshots its input/output IDs and input quantity. Completion compares
that snapshot with the current recipe, so a content update cannot silently
change its component contract. Older development jobs without a snapshot load
but cancel without charge, allowing a fresh request under the current recipe.

An inventory-side last-request record stores a server-created transport epoch
and client sequence. It rejects equal/stale requests, including wraparound,
even if a completed inventory write is newer than the transport checkpoint.
The epoch survives short replacement and changes for fresh selection/handoff.
Window selections and whether the native lab actor already exists are separately
checkpointed; recovery does not create duplicate lab actors in an intact scene.
Jobs use a wall-clock deadline, so an elapsed job can finish once the station
session resumes. Trade XP is stored; development levels remain fixed at 50.

`smoke_refinery.py` uses a disposable station account with the actual Magnetite
recipe. Native-format packets cancel without consumption, complete a single
job across replacement on the same UDP socket, refine the remaining stack,
charge exact costs, update trade XP, reject missing materials, and retain exactly
one award after another replacement and replayed old datagram. Rust tests cover
state/transaction recovery, failed writes, changed recipes, ownership/space/filter
rejection, request sequencing, wire field order and capacity boundaries. The
regular Third client received the lab setup while docked without crashing;
graphical terminal interaction and full refinery panel operation remain unverified.


## Manufacture and analysis

`import_items.py` now retains SQL `man_cost`, `man_cost_base`, `man_cost_mod`,
`man_dif`, `ana_dif`, `dis_dif` and recipe `difficulty` under each template's
`crafting` record. Missing/zero costs, the 9,999,999 base-cost sentinel, self-input
recipes, absent components and `no_manu` items cannot produce build quotes.
These metadata do not change the legacy ItemBase packet format.

`crafting.rs` resolves recipes and checks the appropriate Build skill:
Components 4, Devices 5, Engines 6, Reactors 8, Shields 9 and Weapons/Ammo 10.
Public `skill_levels` descriptions map ranks 1?5 to matching item levels,
rank 6 to level 7, and rank 7 to level 9. Development JE characters retain their
real class skills: no extra component/weapon build skill is granted.

Native terminal mode 1 selects manufacturing; mode 2 selects analysis. The
private lab actor and 0x007F identity are shared with refinery mode 4.
0x007B selects an already-learned recipe; 0x007A selects a category/subcategory;
0x0080 filters levels; 0x007E action 2 starts one operation. Primary categories,
subcategories and KnownFormulas are encoded using archived AUX layouts.
Refinery's stack action remains supported; crafting stack/parallel requests
are not implemented.

The analyzer takes one owned item through 0x0027 cargo/container-12 transfers.
`production_target` is persistent owned escrow, included in validation and item
metadata. Inserting a target requires an empty analyzer and an applicable skill;
returning it requires an empty cargo slot. Moves lock during a pending job. An owned target must be returned before
switching to manufacturing or refining, so it cannot be hidden behind a preview.
Cancel/close keeps the sample owned and unconsumed; it can be returned from the
closed analyzer while docked. Departure is blocked until pending production is
finished/cancelled and the analyzer sample is returned. No fake template-ID
request can substitute for an owned sample.

Analysis requires a valid manufacturable recipe, sufficient build skill, one
non-destroyed sample at normal quality or better, an unknown recipe and credits.
A completed attempt consumes its sample and fee; success adds the product ID to
`known_formulas` (maximum 500, matching the native list). Manufacturing requires
that knowledge, sufficient skill, each repeated SQL component, sufficient
credits and output capacity; unique products cannot duplicate an owned item.
Consumption happens before capacity admission, so freeing component slots can
make a full hull buildable. Product creation, component removal, fee and trade
XP are one inventory replacement; rejected operations do not mutate the original.

**Explicit prototype balance:** the archived build/analyzer handler is a stub,
not an original implementation to reproduce. One manufacture creates one item
at quality/structure 1.0 with guaranteed success and awards `level * 50` trade XP.
Analysis currently uses `man_cost` as its fee because no separate analysis fee
is supplied. Its success percentage is
`clamp(100 - clamp(ana_dif, 0, 100) + 10 * (skill_ceiling - item_level), 5, 100)`.
The sample and fee are consumed on actual failure too. Manufacture difficulty,
quality curves, build experience, overrides, ammunition batch sizes and original
dismantling balance still need implementation.
These rules must not be mistaken for original server balance.

A durable `CraftJob` freezes the recipe/cost, sample and analysis outcome before
a three-second timer starts. Sample and money remain owned until completion;
restart cannot reroll an analysis. Completion rechecks recipe/skill/input state
and clears the job in the same commit as its result. Changed inputs or a failed
write cannot partially consume or award. Refining and crafting share
`ProductionRequest` epoch/sequence replay checks and cannot run simultaneously.
`production_service.rs` owns window/lifecycle behavior, `production.rs` owns
shared presentation/request records, and `refining.rs` keeps refinery-only logic.
Old `refinery`, `refinery_epoch`, `refinery_lab_ready` and
`last_refine_request` JSON keys load through aliases; old refinery windows default
to mode 4. The running native Third client resumed after that migration.

`smoke_manufacturing.py` uses a disposable docked JE with the real Dragon's Horde
recipe, one sample and its exact components. It checks unknown-recipe denial,
owned target movement, cancellation, analysis and build across two replacements
on the same UDP socket, learned knowledge, exact fees/components/XP, old-datagram
replay and missing-component rejection. Rust tests additionally cover repeated
components in a full hull, failed writes, changed recipes/samples and old schema
loading. These are native-format protocol tests; graphical terminal interaction
and analyzer/build panels remain unverified. Refinery, station multiplayer and
vendor protocol regressions also pass.


## Crafted provenance and dismantling

The archived `AuxItem` and `AuxEquipItem` both carry `BuilderName` at field 9;
this is an existing wire field, not a new opcode or template flag. Item instances
now persist `builder_name`, validated as printable ASCII of at most 63 bytes.
All newly manufactured items receive their authenticated character's name, saved
in the job before the timer. Stock/loot/refined items retain an empty builder.
Cargo, vault, equipped and terminal AUX encoders include the name when present.
Stock purchases cannot merge into crafted stacks; maker/quality/structure all
participate in identity for stack moves and instance admission. Split/swap/vault
operations preserve the name. Analysis rejects player-made samples with native
validity 11 rather than allowing a crafted item to teach its blueprint.

Existing item files without a builder field load as stock; their historical
origin cannot be inferred safely. Old pending manufacture jobs without a builder
can load but cancel without charge instead of producing an unmarked new item.
Analysis/refinery job recovery remains compatible. Inventory commits now enqueue
metadata for newly owned template IDs before the inventory AUX update; this also
covers outputs awarded by a restored job without its old selection window.

Native manufacturing mode 3 now uses the owned target container for dismantling.
Like analysis it requires a valid public recipe, sufficient build skill, credits
and one non-destroyed owned item. It does not require knowing the blueprint and
does not teach it. Crafted items may be dismantled. The job freezes a recovery
plan containing at most the recipe's repeated component quantities. Completion
rechecks the full sample/recipe, removes the sample, charges its fee and admits
all recovered components in one inventory candidate. If they cannot fit, or a
write/input check fails, no partial recovery/destruction/charge is published.
Cancel retains the owned target. Replacement uses the saved plan; it cannot
reroll or apply it twice. The terminal displays prospective components while
working and actual recovered components in its completion receipt.

**Prototype dismantling balance:** no implementation survives in this archive
beyond the mode enum and target-container handling. The fee currently equals
`man_cost`. Each recipe component independently succeeds with percentage
`clamp(100 - clamp(dis_dif, 0, 100) + 10 * (skill_ceiling - item_level), 5, 100)`.
All recovered components have normal quality/structure and empty stock builders;
original input-component instance history is unavailable. An attempt recovering
nothing still consumes the sample and fee. Dismantling awards neither trade XP
nor recipe knowledge. These policies remain subject to balance/fidelity work.

Rust tests cover stock/crafted/mixed-maker stacking, invalid names, native field-9
bytes for cargo/equipment, repeated/partial/empty/forged recoveries, full cargo,
changed samples, failed writes and replacement without duplicate awards.
`smoke_manufacturing.py` now also manufactures a named item and dismantles it
across a third replacement on the same UDP socket, checking exact components,
fee, empty target and no extra XP. Refinery and two-client combat/loot regression
smokes pass after the shared admission change. Graphical crafting panels remain
unverified; packet tests are not evidence that those panels work.


## Native gate camera and saved transitions

A native check selected the Saturn gate from the sector map, used the central
Warp button, and confirmed the Gate verb changed from disabled at 184 km to
enabled in range. Activating it entered Loading but stayed in the old server
sector. The old Rust handler sent camera state 4 immediately and discarded
completion action 19 for six seconds. With corrected sequencing, the native
client completed roughly 450 ms after receiving that camera, so the old timing
lost its one completion request.

The archived `SectorManager::Gate` instead opens the gate model with render state
1, then schedules camera state 4 after 5,800 ms. `gating_service.rs` now follows
that order. `GateTransit` binds source gate, destination, start time, camera flag
and completion flag; it is saved with the authenticated session. An early
completion is retained until the opening interval elapses, not dropped. A
completion after the camera starts hands off immediately. Repeated activation of
the same pending gate does not reset its timer, and a completion naming another
object cannot redirect the transaction. Native completion may omit the hidden
target as 0/-1; authorization still comes solely from the saved gate.

Activation requires a living logged-in ship, the selected gate within 2,500 m,
a hosted space destination and no active warp/cast. It stops weapons and motion,
detaches formation and cancels mining/loot work. New impulse/warp/abilities and
proxy position prediction cannot interfere during the interval. Incap, an invalid
source or a two-minute timeout cancels it and resets render/camera state.
Logout, fresh authentication and sector transfer clear authorization; short
replacement preserves it. Already-sent camera state is not sent again on resume.
The player-location and transport-checkpoint files remain separate, but sector
transitions now commit a journal containing both before installing either one;
see the transition recovery section below.

For the client already stranded by the old code, a one-time local checkpoint
bridge restored the observed Saturn transition without closing its game/proxy;
that recovery is recorded under ignored diagnostics, not a gameplay bypass.
The next Saturn -> Alpha transition used ordinary native map selection, impulse
approach and the Gate button with the new server: camera started after 5.8 s,
completion arrived at 6,249 ms, and the native client loaded Alpha successfully.
Third was then returned to ECS Dauntless and docked through its native button.
No production server or unrelated character was controlled.

`smoke_gating.py` starts two disposable accounts at the real Alpha/Saturn gate.
An early completion survives replacement and changes only that character's
sector; the second character receives its camera, replaces the server again,
and completes without a repeated camera. Wrong-target completion and an old
completion datagram cannot redirect or repeat either handoff. Rust tests also
cover deadline staging, repeated activation, incap/range/owner context, ignored
prediction, formation exclusion and expiry cleanup. All other gate routes,
cinematic approach/contrails/sounds and native self-jumpstart remain unverified.

The native control file also clarified the default PSW keys: A moves forward,
left/right steer, Q toggles warp, '[' stops, F fires and Space requests follow
(impulse type 12). Type 12 is not implemented yet; the archived FollowTarget
accepts MOB/player targets and clears following for static scenery. This remains
an implementation task, not evidence of a working follow command.


## Journaled sector handoffs and storage failure

`sector_transfer.rs` prepares the destination map, position and JoinData before
changing the character's live location. `prepare_transition` writes
`sector-transition.local.json` in that character's state directory. This record
contains the destination `SavedPlayer` and its destination `Checkpoint`, bound to
the account and server bind address. Data is flushed and the journal is renamed
atomically; that rename is the transition commit point. An existing unresolved
journal cannot be overwritten by a new transition.

After commitment, the scene can switch in memory, but `finish_transition` must
install both `player.json` and `session.local.json` and remove the journal before
JoinData is queued. Temporary files use `.next`, separate from ordinary live
checkpoint `.tmp` files. An error at either mirror write or journal removal keeps
the committed record and withholds handoff output. New input/selection cannot
advance that character while it is pending. The main tick retries this work at
250 ms intervals without failing the shared simulation. No new transition is
submitted; this only materializes the already-committed destination.

On startup, journal recovery runs before reading the ordinary location file or
choosing/loading the session's sector. It checks account, bind address, map and
position, home station and the handoff's player/destination binding before
installing the pair. A foreign or invalid record fails without overwriting the
existing character files. A configured standalone map uses its actual bounds,
not the public catalog's different map with the same ID. Recovery is idempotent
if either mirror was still old. The existing 120-second transport resume policy
still applies; an older checkpoint can require normal login at the recovered
destination. Corrupt/unwritable state can still prevent that account loading.

Before commitment, a journal-write failure leaves the source location and
transport unchanged. `enter_station` also restores original health, skills and
home station when a docking/towing preparation fails, retaining docking
clearance. After commitment the destination is authoritative and recovery rolls
forward. Station launch now includes its exterior offset in the prepared
position; it no longer performs a second location save after queuing handoff.

Preparing a destination checkpoint must not lock the departing `SectorCombat`:
the wormhole ability handler can already hold it. The snapshot factory accepts
the destination actors directly; normal checkpoints capture current actors.
Rust group-wormhole tests caught and verified correction of this lock recursion.

Shutdown save/checkpoint loops attempt all characters and group state even after
one character's write fails, then report the error. The fault smoke initially
caught a stale camera on the second client because the previous save loop stopped
at the first error. Its regression test verifies both the other player's files
and group-file write are attempted.

`smoke_gating.py --fail-mirror` blocks only one disposable character's transition
checkpoint file. It confirms the location mirror changes while its transport
mirror stays in the source sector, no handoff reaches that client, and the other
client still receives target acknowledgments. It stops that server while the
journal remains, removes the test fault and starts a replacement. Recovery
installs the pair and resumes the same client UDP socket in Saturn; the other
client's already-sent camera does not repeat. Subsequent replacement, correct
completion, wrong-target completion and old-datagram replay checks also pass.

Rust fault tests cover failure before journal commit, blocked first/second
mirror writes, in-process retry, startup recovery, duplicate materialization,
foreign record rejection, retained docking/towing health/clearance, configured
map bounds and independent shutdown saves. Ordinary gate and station multiplayer
smokes pass too. This verifies process interruption and injected filesystem
errors, not power loss. File data uses `sync_all`; directory/volume power-failure
semantics have not been tested, and ordinary non-transition checkpoints retain
their existing write policy. Runtime SQL/database migration remains future work.
