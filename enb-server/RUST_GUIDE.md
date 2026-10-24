# Reading this server's Rust

The code is organized so that protocol work can be followed without learning an
asynchronous framework first. `main.rs` handles configuration, `server.rs` owns
sockets/threads, `game.rs` coordinates players and shared sectors, and
`packets.rs` encodes client messages. `player_session.rs` owns one player's
transport, motion, skills and visibility; `sector_combat.rs` owns shared MOBs.

| Rust notation | Meaning here |
|---|---|
| `struct PlayerSession` | A record with named fields, similar to a C++ class's data. |
| `impl PlayerSession` | The methods belonging to that struct. |
| `&PlayerSession` / `&self` | Borrow read-only access without transferring ownership. |
| `&mut PlayerSession` / `&mut self` | Borrow exclusive access so a method can modify it. |
| `Vec<u8>` | An owned byte buffer. Its length is separate from its capacity. |
| `&[u8]` | A borrowed, bounds-checked view of bytes. |
| `[f32; 3]` | Exactly three floats: X, Y, Z in metres. |
| `Option<T>` | Either `Some(value)` or `None`; no nullable pointer required. |
| `io::Result<T>` | Either `Ok(value)` or an I/O/protocol error. |
| `operation()?` | Return the error to the caller if the operation failed. |
| `match` | A checked switch over an opcode, state, or result. |
| `Arc<Mutex<GameState>>` | Shared ownership across threads, with a lock for mutations. |
| `move` on a closure | Transfer its captured values into that worker thread. |
| `to_le_bytes()` | Encode a number explicitly as little-endian wire bytes. |
| `to_be_bytes()` | Encode explicitly in network/big-endian order. |
| `#[derive(Serialize, Deserialize)]` | Generate JSON conversion for a data record. |

Start at `server::run`. It binds the endpoints, starts TCP acceptor workers, and
runs the UDP/tick loop. `dispatch` handles account/global TCP messages.
`GameState::handle_udp` routes to the authenticated player. `PlayerSession`
checks endpoint ownership and dispatches gameplay opcodes. `GameState::advance`
moves players, delivers group support, advances each sector's MOBs exactly once,
then publishes per-client views. Session `flush` creates sequenced batches.

`motion.rs` contains ordinary `Motion` and `Bounds` structs. `thrust` selects the
requested speed; `steer` changes yaw or pitch input; `advance` integrates speed,
orientation and position for one tick. `PlayerSession` retains position ownership and
passes it by mutable reference. Packet encoding converts meters/second into the
legacy client's meters/millisecond; the game protocol never receives Rust's
in-memory struct layout.

`following.rs` holds the C++ follow speed tiers and facing/movement calculation.
`follow_service.rs` owns the session's follow lifecycle. The coordinator refreshes
only the selected visible peer contact each tick; MOB contacts come from shared
combat. Following is checkpointed, but peer coordinates are refreshed after a
restart. Predicted client motion cannot overwrite server follow movement.

`sandbox_loadout.rs` reads an optional config from the world state directory.
It prepares an inventory copy, validates class/skill restrictions, preserves old
equipment in the vault and atomically saves the grant with its revision. The
revision prevents login/restart from repeatedly resetting money or equipment.

`classes.rs` centralizes the archived race/profession ordering, mount counts and
ship assets. The account CLI accepts `--class`; `SkillCatalog::for_profile` loads
that class's SQL skill ceilings. Equipment validation and AUX skill publication
use the same catalog, so another class cannot silently inherit JE skills.

`ammunition.rs` keeps loaded stacks separate from equipped launchers, indexed by
the same mount. A native cargo-to-equipment move recognizes ammo and loads that
stack rather than replacing the gun. Weapon removal returns its ammo to cargo
as part of the same inventory candidate; insufficient space rejects everything.

`mob_stats.rs` holds the archived DPS/durability curves and computes MOB combat
parameters. The importer retains species, SQL multipliers and the first resolved
weapon. `mob.rs` owns mutable health, AI, reload and random state; its checkpoint
restore uses current definitions and preserves damage fractions instead of
reinstalling obsolete caps. `combat_override` is an explicit custom-scenario
option used by the small deterministic fixture, not by public world imports.

`packets::aux_record` is the most unusual encoder. The client expects two groups
of bit flags: fields with explicit values and fields reset to defaults. Nested
records reset with an extra `0x05` marker. This follows the C++ AuxClasses rather
than serializing Rust structs directly. Rust's in-memory layout is never sent to
the client.

The child modules `session_tick`, `session_peers`, `session_checkpoint`,
`ability_actions` and `sector_transfer` implement named parts of `PlayerSession`.
They share its private state through `impl PlayerSession`, rather than wrapping
the session in another controller. `game_actions` implements group/chat and loot
delivery on `GameState`. `GroupManager` contains socket-independent membership
rules, which lets tests exercise invitations and permissions directly.

Public map/skill/MOB JSON is compiled into the executable. Account and runtime
JSON lives in ignored state directories. `serde` converts those explicit records;
it never decides the network packet format. Python importers and client-launch
helpers run separately from the portable Rust server.

`inventory.rs` distinguishes an `ItemInstance` owned by one player from the
shared `ItemTemplate` describing its model, name and base statistics. An
`ItemTransfer` is a parsed request, not permission to mutate state. `moved`
validates it and returns an owned candidate inventory. The session writes that
candidate to a temporary file and replaces the durable inventory before assigning
it to `self.inventory` and sending the client snapshot. Returning `Err` before
that assignment preserves the old authoritative inventory.

`equipment_stats.rs` calculates equipment/quality/passive parameters without
touching sockets or files. `session_equipment.rs` applies those parameters to
movement/resources and controls beam firing. `ShipInitialization` groups the
inputs for the first native ship snapshot; `ShipResources` names the three
current resource values so packet calls cannot silently swap shield and energy.
The session recomputes derived stats before committing an inventory transaction.

`npcs.rs` owns immutable public dialogue and vendor policies. `npc_services.rs`
owns each player's current conversation: NPC ID, node and whether trade is open.
Opening a dialogue does not itself authorize purchases. A selection must match
a branch just offered to that player, and the NPC must belong to their station.
The client supplies vendor/cargo slots and quantities; the server resolves item
IDs and prices. `Inventory::purchased` and `sold` return candidate copies, then
`PlayerSession::commit_inventory` saves both credits and cargo in one replacement.
Prices displayed on owned slots are transient quotes, not persisted item fields.

`StationPose` stores interior coordinates independently of the ship's position.
`GameState` groups public station-avatar snapshots by station and room once per
tick. `station_peers.rs` compares that room's snapshots with each recipient's
last sent poses: new arrivals receive appearance/entry/pose, changes receive
only pose, and departures receive a room-leave packet. The cache survives a
short restart so an unchanged native scene does not get duplicate spawns.
`station_actions.rs` drops an old pose on room change and waits for coordinates
from the new room, preventing the previous room's coordinates from being used.

`ResourceField` is the shared asteroid stock for one sector. Its `Mutex` means
only one operation edits that stock at a time; it does not create a background
simulation thread. `MiningClaim` reserves a node for one character. `MiningCast`
is the character's timed prospect/tractor operation, saved with their session.
Successful mining saves cargo and a `HarvestReceipt` together in inventory.
The receipt records cumulative quantities removed from the original ore slots;
replaying all players' receipts rebuilds shared depletion without a second
transaction that could lose track of awarded ore. `HarvestMutation` prepares
the shared change before the inventory write and installs it afterward.
Respawn generations have their own world file, saved before new ore is exposed.
See `docs/IMPLEMENTATION.md` for persistence limits and verification boundaries.

`refining.rs` resolves ore/product recipes and calculates a complete inventory
candidate: consume repeated input quantities, subtract the quoted cost, then
admit the output and update trade XP. Nothing mutates if any step fails.
`production_service.rs` owns the native terminal's selection/filter/display state.
The pending `RefineJob` lives in durable inventory, separately from that window,
and completion clears the job in the same replacement that awards the product.
The saved request epoch/sequence rejects a replay even when the inventory file
is newer than the session checkpoint. A fresh authenticated transport gets a new
epoch; a short server replacement preserves the old one. This distinction lets
real new requests work after relogging without replaying old completed jobs.


`crafting.rs` contains ordinary recipe/job records and pure transactions for
analysis and manufacturing. `CraftRecipe::resolve` validates SQL components and
costs; `check_skill` enforces the class build skill. `analyze` consumes an owned
sample from `Inventory.production_target` and can add a durable recipe ID to
`known_formulas`. `manufacture` requires that knowledge and consumes repeated
components, credits and cargo capacity before returning its candidate.

`production_service.rs` routes terminal, category, recipe and action packets.
A `CraftJob` freezes its operation, recipe, sample and analysis outcome before
the three-second timer starts. Completion clears the job and commits its whole
transaction through the same inventory write used by trade/refining. Failures
leave materials and money intact; an actual failed analysis consumes its sample
and fee. `production.rs` holds shared window presentation and replay identities,
so these responsibilities no longer live under refinery-only names. Serde
aliases keep old refinery checkpoints and inventory request tags readable.

The archived manufacture/analyzer handler is a stub. Normal-quality guaranteed
builds, the analyzer fee and its difficulty/skill chance are documented prototype
policies, not recovered original algorithms; see `docs/IMPLEMENTATION.md`.


`ItemInstance.builder_name` maps to the archived native `AuxItem.BuilderName`
(field 9, including equipped/vault/terminal records). `stacks_with` checks maker,
quality and structure as well as template ID. `Inventory::received_instance`
preserves those attributes; `received` is the stock-item convenience method.
Manufacturing stores the authenticated character name in its durable job and
created item. Analysis rejects any player-made sample. Old item JSON defaults
to an empty stock builder; old pending builds without a maker cancel uncharged.

Dismantling is `CraftOperation::Dismantle`, native terminal mode 3. Its saved job
contains the exact repeated component IDs recovered by independent prototype
rolls. `check_recovery` proves that the plan is a multiset subset of the recipe;
completion compares the full owned target and admits every recovered component
on a copy before committing sample removal, recovery and fee together. No trade
XP or learned recipe is awarded. The window's recovery receipt is presentation
only; inventory and the durable job remain authoritative.


`gating.rs` stores a `GateTransit` with the authorized source gate/destination,
wall-clock start, camera-sent flag and client-completion flag. This replaces the
old process-only `Instant` and loose destination fields. `gating_service.rs`
validates activation, opens render state 1, delays camera state 4 by the archived
5,800 ms, and retains an early completion until that interval elapses. It never
discards a legitimate native completion merely because it arrived quickly.

Session checkpoints preserve the gate state across replacement. Camera updates
are sent once; the ordinary handoff path then checkpoints JoinData for replay.
Manual movement/warp, new casts, position prediction and formation changes cannot
move the ship out from under its gate animation. Timeout/incap cancels the
transition and resets its camera; completion without authorization cannot travel.


`sector_transition.rs` owns a write-ahead `TransitionRecord`: destination player
location/home station and its exact destination transport checkpoint. Its journal
rename commits the move before `sector_transfer.rs` clears the old in-memory
scene. `finish_transition` installs both existing state files and removes the
journal before queuing JoinData. If a mirror write fails, the character remains
paused with a committed journal; `finish_tick` retries at most four times/second.
Other sessions continue. Startup replays the journal before choosing a sector
or loading its checkpoint, so an old or half-updated mirror pair cannot win.

`transition_checkpoint` builds the destination snapshot without taking the
old combat mutex: a wormhole ability may already hold that mutex. Normal live
checkpoints still capture the current combat actors. This ownership distinction
is necessary; taking the old lock again would deadlock group wormholes.

Docking and towing share `enter_station`: health, skills and registered station
roll back if the journal cannot commit. Launch spawn position is calculated in
the prepared transfer, rather than saved in a second post-handoff adjustment.
`GameState::save` attempts every character and group before returning an error;
one unwritable character must not make another player's checkpoint stale.

`weapon_launch.rs` prepares and recovers an owned ammunition/resource journal.
`projectiles.rs` freezes target identity and flight deadlines; `combat_storage.rs`
persists shared impacts and accepted sequences using `durable_json.rs` writes.
`combat_damage.rs` resolves typed player damage, while `mob_aggro.rs` maintains
bounded native-style hate lists. `weapon_accuracy.rs` currently provides tested
roll/facing helpers; firing does not yet apply those helpers.
