# Local server developer commands

Type these in the regular game chat. Every authenticated account on this private
development server can use them. They are server commands, not class abilities;
they do not require a skill, charge energy or enforce ordinary travel range.

| Command | Result |
|---|---|
| `/d_spawn` | Spawn one public Achilles Drone (template 46), 1,000 metres ahead. |
| `/d_spawn The Controller` | Spawn the chosen public MOB ahead of the ship. |
| `/d_spawn 46 3` | Spawn three instances of template 46 in a separated row ahead. |
| `/d_jump Blackbeard's Wake` | Transfer to the named hosted sector. |
| `/d_jump BBW` | Sector initials also work when unambiguous. |
| `/d_jump 3605` | Transfer using the native sector ID. |

Names ignore case, spaces and punctuation. Exact names are preferred, then
initials, prefixes and substrings. Ambiguous matches list candidate IDs rather
than choosing one at random. MOB IDs are public **template IDs**, not the
temporary object ID shown when an individual enemy is targeted.

Spawning requires being in space outside warp and gate transitions. Counts are
1 through 32 per command, with a total sector limit of 256 actors. A batch that
would cross a sector boundary or exceed capacity is rejected completely. The
spawned enemy retains its public level, combat parameters, aggression, loot and
respawn timer. Its unique development-spawn identity and state are saved in the
shared sector combat file; the imported public world catalog is not edited.

Jumps support hosted space sectors and station interiors. They use the normal
recoverable sector handoff, so finish the current handoff before another jump.
Pending weapon transactions and production escrow still have to be reconciled;
developer travel does not discard owned cargo or an analyzer sample. Jumping
does not heal the ship or change its registered home station.

`/help` includes both commands. Native graphical playtesting is separate from
the unit/protocol checks recorded in the implementation roadmap.
