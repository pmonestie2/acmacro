# NPC weapon launches and durable impacts

The archived `MOBClass::FireWeapon` emits a weapon effect immediately and
schedules a later `B_SHIP_DAMAGE` call. The Rust shared sector now follows that
ordering instead of applying NPC damage and displaying a beam at impact.

Imported beam weapons and fallback structural, cybernetic and energy attacks
have a fixed 1,000 ms delay. Imported projectile/missile launchers and fallback
rock, organic and other attack types take the integer part of range in metres
times 3 milliseconds. This native NPC timing differs from player launcher
timing. The current imported NPC damage type remains authoritative; the archive
left `m_DamageType` at its constructor default despite selecting actual weapons.

Launcher item IDs/types/effects come from the public SQL. As in the active C++
code, NPC launchers retain table DPS with a two-second reload. No ammunition
template, stock or ammunition effect is guessed: the archive explicitly left
NPC ammunition FX as a TODO. NPC damage variance is rolled at launch, and the
result is frozen in the queued shot.

Each sector persists a monotonically increasing NPC shot sequence and pending
shots alongside actor state. Every shot records the target player ID and
authentication lifetime epoch, sector,
source actor identity/template/life, frozen damage/type/effect and absolute
emission/impact deadlines. The queue is bounded at 16,384 shots. Legacy sector
files default to an empty queue. Malformed or duplicate shot identities are
rejected on restore. A launched shot remains valid if its source dies or the
target cloaks; the coordinator discards a hit when its target has left the sector.
The player's `session_epoch` is frozen as the hit's `target_epoch`. It survives
a server replacement but changes on a real login, so a shot
cannot leak into a new authenticated lifetime after an expired transport
checkpoint. At queue capacity new NPC launches pause while due impacts still
reach acknowledgment; the queue can drain instead of permanently blocking.
Production request replay protection shares this authentication lifetime;
legacy checkpoint fields `production_epoch` and `refinery_epoch` are accepted
as aliases. A fresh login rejects an old target epoch even if its player ID and
sector match, while a replay of an already committed receipt returns its frozen
reflection for acknowledgment without applying damage twice.

Launch admission saves actor cooldown, variance RNG and the new queue entry
before exposing its visual. A failed save rolls back all these changes and
publishes no effect. Quiet movement/cooldown ticks do not clone the entire
world; rollback snapshots are reserved for save/event ticks and possible shots.

A due hit remains in the world queue until acknowledged. The player first
commits its new health and a receipt keyed by sector and sequence in one
checkpoint. Retried delivery finds that receipt and does not apply damage
again. The coordinator then acknowledges the world shot, atomically applying
any frozen Environment Shield reflection and removing the queued impact.
Reflection is guarded by source identity and death generation, so an old
projectile cannot reflect into a newly respawned NPC. Failed acknowledgments
restore both queue and reflected actor; repeated successful acknowledgments
have no effect. Receipt cleanup follows successful world acknowledgment.

`SectorCombat::advance_at` supplies a deterministic clock for behavioral tests;
production uses wall-clock milliseconds. Tests cover beam/launcher timing,
restart continuity, cloak/source death, repeated due delivery, malformed legacy
snapshots, admission failures, and atomic reflection/ack retry. Cosmetic launch
effects are transient; durable HP/receipt recovery is independent of effect
publication. The integration passed 202 Rust tests and Clippy with warnings
denied. Native graphical NPC projectile testing remains pending.

The disposable native-packet smoke is `tools/smoke_npc_projectiles.py`. It records
frames consumed during login as well as later polls, so the first launch is
observable. NPC actor publication precedes launch effects; player weapon effects
still precede target-death removals. The smoke passes cloak, in-flight replacement
and second-replacement no-repeat checks. This is separate from graphical
client playtesting.
