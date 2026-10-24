# Ordinary NPC roaming and combat movement

`mob_roaming.rs` implements the active ordinary-spawn behavior from
`MOBSpawnClass.cpp` and `MOBClass.cpp`. It does not interpret the public
`mob_base.ai` editor notes as a program or invent waypoint routes.

The spawn importer retains each group's original center and radius, the NPC's
numeric faction and aggression, and its existing deterministic starting point.
Aggression 3 and above is eligible for native attack-on-sight acquisition;
faction policy is applied separately. Positive-radius ordinary spawn groups
use DRIFT/CURIOUS movement. A zero-radius region stays stationary, preserving
the existing public actors that the old C++ spawn constructor would omit.
Definitions without region metadata keep the earlier custom-scenario movement
until explicitly migrated. Static turrets are not ordinary spawn-group exports.

DRIFT uses the native speed distribution of 130–229 metres per second and turn
intensities from -0.05 through 0.05, with zero replaced by +0.02. Every ten active
simulation seconds, it draws the sum of two integers from 0 through 49. Outside
the group's radius it faces the real center and selects new speed/turn values.
Inside, a sum above 75 can select an actual detectable nearby player and enter
CURIOUS; a sum below 5 changes the native speed/turn. There is no synthetic route.

CURIOUS faces its selected player, uses the archived five-second approach
horizon and returns to DRIFT when the player disappears, changes authentication
epoch or is reached. The implementation preserves the source's ordering of its
distance tests. Acquiring a combat target interrupts curiosity; losing that
target resumes the saved drift defaults.

Pursuit moves independently of firing eligibility and reload. Ordinary spawned
NPCs continue approaching while firing, hold inside the native 400–750 metre
band and reverse at 50 metres per second below 400 metres. While reversing they
still face the enemy. Native motion packets therefore carry negative forward
velocity; the existing scalar `Mob.speed` remains an absolute speed for following.
Legacy definitions retain their previous movement rules, while zero-radius
actors do not acquire an invented travel speed.

`RoamState` persists its own deterministic random stream, default speed/turn,
decision-clock remainder and optional curious target identity. This reproduces
the native distributions, not the old process-wide C++ random sequence. The
independent stream cannot reroll combat dispersion or committed projectiles.
Short server replacement preserves its state; process downtime is not simulated
as roaming catch-up. Frames of five seconds or more use the archived movement
hibernation guard. World launch rollback includes these state changes when a
moving NPC enters firing range during the same tick.

Resource-field guardian patrols need actual field/node associations and are
separate from ordinary DRIFT. The dormant route/cluster behavior enum values,
scripted AI, delayed spawn activation and group-attack policy are not inferred
from notes or unused database fields. Unit and importer verification are
separate from native graphical playtesting.
