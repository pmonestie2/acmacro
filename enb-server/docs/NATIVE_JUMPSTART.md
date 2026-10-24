# Native Jumpstart controls and recovery

The original target button (verb 12) and ActionPacket action 26 use the same
Jumpstart ability 65 / trained skill 31 cast lifecycle as `/ability 65` and
`/jumpstart`. PS, JE and TS explorers must have that skill trained. An
incapacitated explorer can target itself; a living explorer can help any visible
incapacitated player, including a player outside its group. Other group support
abilities keep their group restriction.

The packet's target is frozen in the cast without changing the current selected
target. Energy, an existing cast, cooldown, station, warp and gate checks still
apply. Target departure, revival, leaving range or entering warp cancels the
cast without producing a support action. The shared coordinator checks a fresh
target snapshot before delivering a completed Jumpstart.

`Player::OnTargeted` advertises a button range of
`1000 + (trained level - 1) * 250` metres; the archived ability's range is
`1000 + trained level * 250`. At the current trained level 7 those are 2500 and 2750
metres. Both measure from the player's 245 m surface radius.

The authoritative call path is `PlayerConnection.cpp` action 26, through
`AJumpStart::CanUse`, `Use`, `Execute`, then `Update`. New casts use these active
rules, rather than the unused charge-time calculation or stale 7% comment:

- For target hull upgrade `H` and trained skill `S`, reject `H > S + 2`.
- Energy is `20 * H`, multiplied by `H - S` when `H > S`.
- `Execute` schedules its callback after 5 seconds; cooldown is zero.
- `Update` restores `min(10 * S / 100, 1)` of maximum hull, with shield zero.
- Self and peer casts send native effects 411 and 414 respectively.

All current sandbox profiles advertise hull upgrade 6; recovery uses that same
constant until individual upgrades become saved character state. A trained
level 7 explorer therefore pays 120 energy, casts for 5 seconds, and repairs
70% hull. Lower skill levels obey the source hull restriction and surcharge.

A typed `JumpstartPlan` freezes the paid cost, skill, hull upgrade, range,
deadline parameters and repair fraction in the cast checkpoint. Recipient
delivery uses the frozen range and repair fraction. Resuming a process does not
charge again or derive a new repair value from a changed skill catalog.

Old in-flight checkpoints without a plan keep their already-paid energy,
remaining time, legacy 49% hull repair and catalog cooldown until completion.
Every newly activated Jumpstart has a native plan. Corrupt plans or plans bound
to another ability are rejected during checkpoint restoration.

Recipient confirmation remains unimplemented. Target eligibility and the final
coordinator check still reject a missing, revived or out-of-range recipient.

Behavioral tests construct packets and sessions locally. They cover native self
recovery, an ungrouped target, frozen targeting, range boundaries, class/training
and energy denials, departure/revival/warp cancellation, and preserving the
group restriction on targeted Fold. Pure rules cover hull upgrades 0 through 6;
checkpoint tests cover native and legacy process replacement. They do not start
a live game client.

An earlier `tools/smoke_jumpstart.py` passed against an owned disposable server: real
NPC beams incapacitate a JE and a PW, native verb12 advertises self recovery,
action26 revives the JE, then a real warp brings the explorer within range to
recover the ungrouped PW. That run verified native controls with the previous
catalog timing and recovery. It is not verification of this later rules update
until rerun. Packet checks are separate from graphical button clicks and
confirmation dialogs.
