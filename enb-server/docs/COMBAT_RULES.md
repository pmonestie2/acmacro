# Combat rules traced to the archived server

`src/combat_damage.rs` isolates the active `PlayerCombat.cpp::ShipDamage`
ordering. Native impact, explosive, plasma, energy, EMP and chemical deflects
cap at 50 percent; negative deflects amplify damage. Plasma adds 20 percent in
the shield branch, chemical adds 20 percent to hull overflow, and EMP cannot
damage hull. Damage Control reduces hull overflow for TE/PW. The plasma bonus
is applied after choosing the branch, so its excess does not create a second
hull hit. This preserves the archive's ordering rather than changing balance.

The old `MOBClass.cpp::DamageMOB` deflect switch is commented out. Public MOBs
therefore retain the active flat shield-then-hull rules; no species immunities
are inferred from names. Player damage notifications use native opcode 0x64:
damage float, modifier float, type, inflicted flag, source ID and target ID.
Modifiers report absorbed damage as a negative value.

`src/weapon_accuracy.rs` contains pure deterministic combat helpers. The live
launcher now supplies a random roll for each prepared round and freezes the
resulting damage and native inflicted flag in the durable launch journal.
Recovery reuses that result and never rerolls. The helpers themselves do not
generate random numbers or consume ammo/energy.
`PlayerCombat.cpp::CalcDamage` uses one roll from 1 through 10,000, tests miss
first, then critical. At baseline the miss threshold is 500 and the critical
threshold is inclusively 9,500: exactly 500 misses, 8,999 ordinary hits and 501
critical hits. The inclusive comparison also permits a critical on roll 10,000
with a zero critical threshold. These boundaries are deliberate compatibility
behavior. Critical Targeting adds ten percentage points per rank to the
baseline five percent. Accuracy bonuses subtract percentage points from the
baseline five percent miss threshold.

A critical doubles already modified damage and uses native inflicted flag 3.
Both an ordinary hit and a miss use inflicted flag 0; a miss has damage zero.
In the native `Equipable::ShootAmmo` path, misses still consume ammunition and
energy and produce weapon effects and damage notifications. The helper must
not be used to skip admission of a missed shot. The frozen damage and outcome belong in the launch transaction to prevent a
recovery reroll; unpublished preparation uses the process random source.

`Equipable::CheckOrientation` restricts beam and projectile launchers to a
strict 40 degree cone about the ship's three-dimensional heading. Missile
launchers have no facing restriction. Player firing now checks this cone before
rolling or consuming ammo, energy, reload or effect IDs. Turning away pauses
the affected mounts without clearing auto-fire, so turning back permits the
next ready shot. Already committed volleys retain their frozen admission and
recovery behavior even if the ship subsequently turns. The pure helper rejects malformed
vectors, normalizes headings, and explicitly accepts a coincident target
instead of reproducing the native division by zero. Chance construction clamps
out-of-range bonuses rather than reproducing signed-short overflow or the
archive's erroneous reset to zero when critical chance exceeds 100 percent.

The helper tests cover every baseline roll, branch boundaries, vulnerability,
damage-type behavior, invalid numbers and three-dimensional firing arcs.
Session tests additionally check resource changes and native damage fields.
Live graphical combat verification is separate from these tests.

NPC damage now follows the archived delayed launch/impact path and the shared
world/player receipt acknowledgment protocol described in
[NPC_PROJECTILES.md](NPC_PROJECTILES.md).

`mob_motion.rs` now records native render motion from the existing authoritative
MOB displacement. Chasing and returning ships face their direction of travel;
stationary attackers face their target. Native 0x3e packets include orientation,
current/desired velocity in metres per millisecond and persistent movement IDs,
instead of resetting every NPC to an identity quaternion with no velocity.
Stopping, contemplation, death and respawn publish zero velocity. This adds no
second flight solver or new patrol behavior: current chase/home speeds and
combat/hate/projectile rules remain authoritative. Motion checkpoints validate
finite values and normalized orientation; old snapshots default to stationary
render motion until the next simulation tick establishes a direction.

`src/mob_aggro.rs` now preserves the archived four-slot hate table per MOB in
sector checkpoints. Owned weapon hits and Shield Leech add the integer part of
their damage to that attacker's hate. Positive maximum hate selects the target;
equal totals use native slot order. A fifth attacker replaces the weakest slot
only when its incoming hate is larger. Each MOB shot subtracts the integer part
of one tenth of its damage from that victim's hate. Arithmetic saturates rather
than overflowing native signed integers, and an existing player's slot is found
before an empty slot so vacated slots cannot create duplicate identities.

Target selection considers visible local ships. The existing simulation tick
enforces the 15 km leash, resets hate and moves the MOB toward its spawn.
A nearby bystander cannot steal a neutral enemy from its actual attacker.
Temporary absence and ordinary target loss retain stored hate; a present ship
that becomes undetectable loses its entry, as in native `LockTarget`.
`remove_player_hate` is the explicit native range-list/departure cleanup hook.
Contemplation retains its existing reset behavior, damage breaks its pause, and
death/respawn and the existing direct leash reset clear combat state. The
unowned `damage` API retains standalone scenario behavior separately from
owned-player hate. Group hate sharing is not implemented. Attack-on-sight now
uses the imported numeric aggression tier (3 and above), per-player faction
standing and the sensor rules below; legacy custom definitions without a numeric
tier retain their explicit aggressive flag.

`mob_targeting.rs` now applies the archived sensor boundaries. A player sees a
MOB within equipped scan range plus the native 3,000 metre MOB signature. A MOB
sees a player within 2,500 plus 150 times its combat level plus that player's
equipped signature. Entry is strictly inside the boundary; existing contacts
remain until more than 200 metres beyond it. NPC contacts persist with player
authentication epochs, so a fresh login cannot inherit an old sensor contact.

Cloak detection is evaluated independently for each NPC: its combat level times
three must be at least the player's stealth. The archived NPC Scan skill remains
zero; no additional skill value is inferred. Player stealth uses overall level
plus five times trained Cloak skill, rather than the selected ability rank.
Consequently a level 150 JE with Cloak 7 is hidden from CL61 but detectable by
CL62. Definitions without spawn-region metadata use the existing 100 metre/second
return-home fallback. Ordinary positive-radius spawn groups now use the native
DRIFT/CURIOUS and pursuit rules in [MOB_ROAMING.md](MOB_ROAMING.md); resource-field
patrols and scripted behaviors remain separate work. Shots already launched
retain their frozen impact and are unaffected by subsequent cloak changes.

`smoke_npc_projectiles.py --level 61` and `--level 62` check the trained-stealth boundary
using native packets, then replace the server while a shot is in flight.
Both pass; group cloak/follow/formation/restart checks also pass independently.

Public faction defaults come from `data/factions.json`: the archived loader uses
the matrix's `base_value`, not `current_value`. PDA factions start within native
bounds with 7500 for the character's own class; durable inventory overrides take
precedence. NPC acquisition requires a standing of zero or less unless the player
has provoked retaliation. Attitude colors have their separate native thresholds:
hostile at -2000 or below, friendly at 2000 or above, shun between them. An NPC's
current attack victim sees hostile attitude even if ordinarily friendly; other
observers retain their individual faction reaction. Unknown numeric faction 32
is preserved without inventing a PDA entry. Combat faction awards remain pending.

`tools/smoke_factions.py` passes against a disposable server with two real packet
clients: a same-faction JE observes a friendly drifting NPC, a PW is acquired by
numeric aggression 3, and simultaneous observer attitudes remain independent.
Immediate authenticated takeover survives the old transport's logout, and NPC
pursuit plus faction attitudes survive a server replacement. These are native
packet checks, not graphical PDA or combat playtesting.

Numeric aggression 1 through 4 also follows archived `CheckWarningShots`: a
previously observed ship strictly within 1000 metres with standing at zero or
below gets a warning roll every 500 active milliseconds. The exact probabilities
are 2/4/6/9 percent, adding 1/5/20/100 hate respectively. Normal cloak and target
eligibility checks still apply. Prior-pass contact identities, the active clock
and a separate deterministic random stream survive checkpoints; departure and
life reset clear contacts. Failed shot persistence rolls back these changes with
the actor. Suspension does not generate offline warning rolls. Six behavioral
tests cover boundaries, faction/cloak, epochs, persistence and launch rollback.

Follow now reads signed NPC motion velocity, as native `FollowTarget` reads
`Object::Velocity()`. Using the old distance-per-tick magnitude made followers
move forward while a nearby NPC reversed. Session coverage checks the signed
movement through the real follow command path, alongside ordinary follow and
restart behavior.

NPC surface-distance and player weapon size rules now use imported native
asset radii; see [ASSET_GEOMETRY.md](ASSET_GEOMETRY.md) for the source rules,
content provenance and remaining static/resource geometry work.
