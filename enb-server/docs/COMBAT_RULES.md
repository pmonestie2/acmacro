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
owned-player hate. No faction standings, group hate or new scan parameters are
invented; attack-on-sight acquisition continues to use the imported aggressive
flag and current scan range.
