# Native object radii and combat distance

`tools/import_asset_radii.py` exports only BASE IDs and numeric `Radius` values
from the installed client's `Data/client/ini/cbasset.ini`. The checked-in
`data/asset_radii.json` records the input filename and SHA-256; it contains 3186
radii. Duplicate sections with identical radii are accepted because the installed
file contains them; conflicting values fail. Missing radii are not invented.
`RadiusAdjust`, mounts, textures and other asset data are not exported.

The archived `Net7/CBAssetParser.cpp` reads `Radius` from `cbasset.xml`. That XML
was not found in the archived reference, so equality with the installed INI is
unverified. The active parser ignores `RadiusAdjust`. `ObjectClass.cpp`'s large
manual planet/gate radius switch is disabled and is not reproduced here.

`PublicSpawns` loads this catalog and exports an explicit unscaled `radius` for
each public MOB. All 6650 actors, using 139 asset types, have known radii. The
content migration changes only this field; navs, spawn positions and all other
metadata stay intact. Custom scenario definitions retain radius zero unless
they supply geometry explicitly. New public imports fail on unknown asset IDs.

The native `Object::RangeFrom(point, false)` subtracts the receiver's unscaled
radius from center distance, clamping overlap to zero. `SetScale` changes visual
scale independently. NPC sensing (including the player's view of the NPC),
proximity warnings, pursuit/curiosity standoff, firing range and NPC projectile
flight use that NPC surface distance. Heading and firing cones aim toward the
center; actual displacement, home position and explicit absolute-distance leash
checks remain center-based. Spawn-marker geometry is separate and still pending;
roaming boundaries currently use the marker center and configured region radius.

Player weapon admission follows the additional archived
`Equipable::CheckRange` size contribution: target surface distance must be at
most weapon range plus target radius times visual scale. At scale one the two
radius contributions are both present in the original code. Projectile flight
uses the surface distance without adding this extra reach. Existing frozen
projectiles preserve their committed deadlines when content geometry changes.

Native follow also measures target surface distance. MOB contacts carry their
explicit radius; player contacts use the archived player radius of 245 metres.
The existing positive movement clamp stops ten metres from that surface instead
of letting a large tick pass inside it. Reverse movement still uses signed
velocity. Resource/husk radius overrides, static navigation geometry, arbitrary
SQL object-radius patches and exact collision/avoidance are separate work.

Regenerate the numeric catalog with:

```powershell
C:\PYTHON\python.exe enb-server/tools/import_asset_radii.py PATH/TO/cbasset.ini
```

Then ordinary `import_spawns.py` and full world imports use that catalog. No
installed client files are needed to run the Rust server after content export.

Behavioral coverage includes strict sensor boundaries, large NPC standoff,
warning distance, native player reach versus flight timing, follow stop distance,
legacy checkpoints, and failed launch persistence rollback. Native graphical
distance/collision playtesting remains separate from unit and packet checks.
