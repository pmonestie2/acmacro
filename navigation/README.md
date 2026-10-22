One-time cooperative navigation
===============================

Run from an elevated interactive Windows session:

    C:\PYTHON\python.exe navigate.py config_player_macboypw.txt orsini

`orsini` means Orsini Mining Platform in Carpenter. `carp` stops in Carpenter
sector. The route from Earth is Earth -> Asteroid Belt Alpha -> Saturn -> Glenn
-> Carpenter. Existing controller destinations remain accepted.

Navigation shares the normal window focus mutex and HTTP peer logoff handling.
It polls the configured client's private messages and uses the same reply/STOP
pipeline as Pluto. Chat history is initialized after automatic client-slot
selection, so old messages from another installation are not replayed. A real
GM/PM STOP terminates navigation and broadcasts peer logout; it is never retried.

`--restart-game` explicitly restarts only the configured character's game.
Stuck loading/transition checks use the normal recovery budget, with a 120-second
wall-clock ceiling in addition to the 60-second gameplay timeout. A rejected
unsafe endpoint or uncertain native submission is not automatically retried.
`--resume-gate SECTOR` finishes an already submitted gate approach without
sending another navigation command. Use it only for the actual pending gate.

The read-only packet recorder must be running. Captured worlds are matched to
the owned proxy, and stale captures are rejected. Map coordinates come from
captured map objects; IDs stay scoped to their sectors. New gate selection uses
the normalized small-map coordinates and existing native navigation submission.
When a destination is not discovered, the navigator can approach an already
observed intermediate nav toward its last-known coordinates. It continues
promptly after arriving, rather than waiting there.

Endpoints near recently observed ships/NPCs are rejected conservatively because
the packet hostility field has not been decoded. Known dangerous Carpenter
stops are excluded. The client's visibility range limits enemy observations;
absence of a recorded mob is not proof that a distant stop is safe.
V'rix observed positions remain persistent sector danger zones in navpoints.json,
including across visibility loss, death, restart and sector visits. They are
approximate hazard areas, not decoded spawn points. After a gate transition,
current or remembered V'rix danger near the ship triggers
an immediate departure toward a safe discovered intermediate nav. This does not
guarantee protection from enemies appearing after the observation.

`nav_catalog.py` refreshes `navpoints.json` and `navpoints.csv` from all recorded
sector epochs. These contain navpoint names, IDs, physical XYZ, map XYZ (physical
units / 1000), timestamp and proxy-session provenance. Asteroids and moving
ships are excluded. `map_calibrations.jsonl` records the map-to-screen conversion
used for discovered points. `orsini_arrival.json` records server landing-message
and station-map evidence after verified docking.

References consulted:

- http://www.enbmaps.de/?breite=1614&hoehe=1194 (menu loads; sector data unavailable during this run)
- https://net7wiki.bmsite.net/index.php?title=Carpenter (dangerous locations)
- https://net7wiki.bmsite.net/index.php?title=Glenn (gate connections and navpoint hazards)
- https://net7wiki.bmsite.net/index.php?title=Orsini_Mining_Platform (station at map XYZ 26.58, 89.03, 0)

Published coordinates are discovery hints, never authority for a submitted ID.
Logs and screenshots are saved under `diagnostics/navigate_<toon>*`.

Supervised navigation controls confirmed by the user: `]` enables continuous
forward movement; `[` enables continuous reverse movement. These movements
continue after focus is released. Activate the equipped Kraken before warp.
In station rooms, use `/starbase reset` and record the reset orientation and
verified destination door separately for each station and room.

Live PW observation on 2026-10-11: direct Saturn-gate to Mars-gate travel in
Asteroid Belt Gamma entered a gravity well near map XYZ (235.31, -95.92, 0).
The game reported leaving it while moving east near (240.06, -95.80, 0).
These are observed boundary crossings, not a measured well center or radius.
Use the captured Saturn Gamma Navway points and Saturn Beta Junction to route
around that area instead of submitting the direct gate-to-gate segment.
