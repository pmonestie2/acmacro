# Native Jumpstart controls

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

This restores native controls and target eligibility, rather than claiming full
archived recovery parity. Existing catalog energy 140, charge 7 seconds,
cooldown 10 seconds and existing recovery effects remain unchanged. The archived
`AJumpStart` also has a recipient confirmation dialog and different target-hull
energy, callback timing and repair rules; those remain separate work.

Behavioral tests construct packets and sessions locally. They cover native self
recovery, an ungrouped target, frozen targeting, range boundaries, class/training
and energy denials, departure/revival/warp cancellation, and preserving the
group restriction on targeted Fold. They do not start a live game client.

`tools/smoke_jumpstart.py` also passes against an owned disposable server: real
NPC beams incapacitate a JE and a PW, native verb12 advertises self recovery,
action26 revives the JE, then a real warp brings the explorer within range to
recover the ungrouped PW. This uses the catalog cast and cooldown rather than
changing player HP files or bypassing skill costs. It is packet-level
verification, separate from graphical button clicks and confirmation dialogs.
