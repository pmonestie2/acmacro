# Rust development server

This is an independent local game server. Its default sector is **Asteroid Belt
Alpha** (1076), exported from the archived public content database: 473 static
objects, 37 visible navigation points, 40 MOBs, ECS Dauntless, and the gates to
Earth, Saturn and Asteroid Belt Beta. The catalog hosts 130 public space maps
and 42 station interiors.
Resource fields add 21,199 streamed nodes across the public maps (616 in Alpha).
Eligible nodes support shared prospecting, reactor drain, ore collection and
persistent depletion; native mining playtesting remains in progress.
MOB kills now create shared persistent corpses, with native loot permissions,
credit collection and timed item tractors. Native corpse/credit collection and
two-client protocol collection/restart are verified; native item pickup remains
an open playtest.
Public MOB durability and DPS now use the archived level interpolation tables,
species hull/shield split, first equipped weapon and SQL combat multipliers.
Organic/energy enemies have shields without a hull bar. Shield Leech can finish
these enemies and preserves kill attribution. Checkpoint recovery rebinds current
content/caps while preserving damage fractions. Four-slot damage hate is
persistent. NPC attacks now publish launch effects and apply their frozen typed
damage after the archived beam/launcher delay, with durable player receipts and
world acknowledgment. Faction hate and original boss AI still need work.
The refinery supports 207 ore/product links and single/stack jobs with atomic
cargo/credit/XP transactions and restart recovery. Native-format protocol tests
pass; graphical refinery use remains unverified. Analysis and manufacturing now
use owned analyzer samples, durable learned recipes, real components/costs,
build-skill checks and restart-safe jobs. Dismantling consumes an owned item and
returns a saved component recovery plan; crafted builder names persist in native
item records. Graphical crafting remains unverified.
Native Gate-button travel from Saturn to Asteroid Belt Alpha is verified. Gate
opening/camera/completion state now survives replacement, including early
completion messages that previously left the client loading. Sector handoffs
now journal location and transport together before publication, with recovery
checks for a failed checkpoint write while another client stays active.
See [the implementation roadmap](docs/ROADMAP.md) for current gaps and priorities.
Native impulse 12 (Space in the PSW controls) now follows a selected ship or
living MOB. It faces the target, matches speed nearby and uses full impulse
beyond 1.5 km. Manual steering/thrust, departure or loss of visibility stops it.
Two-client protocol follow/stop and checkpoint recovery tests pass; graphical
follow playtesting remains pending.
The public item catalog contains 4,568 definitions with effects, restrictions,
recipes and refine targets. Cargo/equipment/vault transfers and stacks persist;
reactor/shield/engine stats and beam firing now use equipped items and supported
passive effects. Public NPC dialogue and vendor purchases/sales have runtime
handlers with persistent credits/cargo. The native NPC panel still needs work;
active item effects, full trade progression and crafting balance remain unfinished.
Player missile/projectile launchers use owned ammunition, delayed impacts and
durable shot recovery. NPC delayed impacts also survive replacement; target
authentication epochs prevent old shots from striking a fresh login. See
[NPC launch and impact recovery](docs/NPC_PROJECTILES.md).
Rebuild the public catalog with `C:\PYTHON\python.exe
enb-server/tools/import_items.py PATH_TO_PUBLIC_SQL_DUMP`; runtime inventory
instances remain separate from these definitions.
The small **Rust Sandbox** map remains a test
fixture with four navpoints, a loop gate and a level-one Achilles Drone.

The server is portable Rust. It uses `std::net`, Rust threads, JSON files, and
Rust cryptography crates. The graphical game and Net7Proxy launcher are Windows
tools kept separately in `tools/local_windows.py`; the Rust server does not use
Windows APIs, SQL drivers, client memory, or Python at runtime.

## Run on this Windows VM

For concurrent clients use `local_game_buddy.cmd` and `local_game_third.cmd` from
the repository root. The local test accounts are `buddy` / `pass` and `third` /
`pass`; account selection is independent of the client profile used to launch.

The sandbox has one test account per class: `te`, `tt`, `ts`, `jd`, `js`, `je`,
`pw`, `pp`, `ps`; each uses password `pass`. Any independent client profile can
log into any account. These have native race/profession values, distinct ship
models, SQL class skill ceilings and class-specific weapon/device mount counts.
Implemented shared abilities work where the class owns them. Other class
abilities explicitly report that their gameplay handler is unfinished.

The current sandbox enables `state/sandbox_loadout.json`, copied from
`data/sandbox_loadout.json`: a one-time floor of 100 million credits and a
200%-quality elite class-legal loadout. The JE gets L9 beams/reactor and L8
shield/engine; other classes use their own tech ceilings and mount counts.
All presets currently use beams; owned compatible launchers and ammunition can
be equipped separately.
Replaced equipment is preserved in free vault slots; cargo is untouched.
It applies to existing and new accounts on load. The inventory records the grant
revision, so spending money or changing equipment survives restarts. Increase the
revision deliberately to grant again. Removing the config disables future grants
without removing owned items. A full vault rejects replacement without losing
items. `tools/import_classes.py` rebuilds the nine skill catalogs from public SQL;
`tools/make_class_loadouts.py` rebuilds legal test presets from the item catalog.
Create another account with `enb-server.exe --create-account NAME --character NAME
--class pw`; the password is read from stdin and stored only as a salted hash.

From the repository root:

```powershell
.\local_game.cmd
```

Run that command **as administrator**. It builds/starts the Rust server if needed,
then launches the regular game using Net7Proxy's `/LC` local-certificate mode.
The server listens on this VM's private LAN address (currently `192.168.47.128`);
the proxy uses `127.0.0.1` for its client-facing services. Net7Proxy binds its
outgoing UDP socket to the hostname's LAN interface; Windows rejects connecting
that socket to a loopback server address. Existing game/proxy
processes must be closed first; the launcher does not terminate them.

The wrapper temporarily maps only `local.net-7.org` to the local proxy in the
Windows hosts file. It also temporarily sets the client's `auth.ini`,
`network.ini`, and registration INI endpoints to that hostname; `/LC` alone
does not override those client files. All three originals are backed up and
restored on normal exit. Keep its command window open. A Windows job object owns the
new proxy/game processes; when they exit or you press Ctrl+C, the wrapper closes
that owned job and restores the prior hosts file. Backups and launcher logs are
under ignored `logs/`. Do not kill the wrapper process forcibly: normal exit is
needed to restore its hostname override.

The bundled proxy HTTPS certificate expired in 2007. The launcher keeps a
one-year RSA-2048/SHA-256 development certificate for `local.net-7.org`, with its DNS name in
the SAN extension and server-auth usage. Windows asks to trust it once for your
user; the same certificate and trust are reused across launches. It renews when
fewer than 30 days remain. Normal TLS/hostname validation remains enabled. The
launcher restores the proxy's original certificate/key files on exit; certificate
state and originals are under ignored `state/proxy-certificate/`.
Certificate generation needs OpenSSL (Git for Windows includes it).

For server-only operation, with Ctrl+C saving and stopping it:

```powershell
.\local_server.cmd
```

For simultaneous native clients, run `local_game_buddy.cmd` and
`local_game_third.cmd` from separate elevated consoles. Each profile has private
writable game/proxy files and its own localhost interfaces; immutable art/audio
assets use hard links. The profile name selects installation state, **not** the
login account: enter the desired local account credentials in each client.
Finish one startup dialog before starting the next. Existing game windows get
an `ENB_local_<PID>` title so a new proxy cannot attach to them.

For the fingerprint-checked installed client build, each private executable gets
a distinct `CreateMutexA` name to allow concurrent clients. Instruction bytes and
the installed executable remain unchanged. Private proxies get an in-memory
localhost address; TCP relays bridge legacy galaxy redirects to that interface.
Gameplay UDP goes directly to the Rust server. Closing a profile cleans up its
own relays, process job and DNS alias. Local wildcard certificate trust is reused.

Without a configured account the demo is `local / local`, character SandboxPilot.
Create your own account and Jenquai Explorer without putting its password on the
command line:

```powershell
C:\PYTHON\python.exe enb-server/tools/create_account.py myaccount --character MyPilot
```

The password is stored as a salted PBKDF2-SHA256 hash in ignored
`state/account.local.json`. An existing account is never overwritten. Player
position is saved in `state/player.json`. Additional accounts use their own
`state/accounts/NAME/` directories, character IDs and state files. Accounts can
be created while the server is running; authentication reloads the directory.
These operational files do not belong in Git.

To build and replace the owned server while leaving the graphical client and
proxy connected:

```powershell
.\restart_local_server.cmd
```

Established space/docked sessions and pending handoffs resume after short outages
(up to 120s). Normal
logout and expired checkpoints require login. This does not resume a TCP login
exchange interrupted halfway through authentication.

## Linux and macOS

With a current stable Rust toolchain:

```bash
cd enb-server
cargo build --locked
cargo run --locked -- --bind 127.0.0.1
```

To connect a Windows proxy from another machine, bind the Rust server to its
explicit private LAN IPv4 address and run the Windows launcher with
`--server THAT_ADDRESS` after starting the remote server. The prototype rejects
public bind addresses. Local macOS loopback aliases may require OS configuration
if you choose `127.0.0.2` instead of `127.0.0.1`.

No production authentication service is contacted. The regular game gets its
legacy TLS interface from Net7Proxy `/LC`; the Rust backend provides the proxy's
plaintext protocol bridge, not an HTTPS server. Connecting the existing web
client's HTTPS account form directly needs a local authentication adapter, which
is not part of this milestone.

## Implemented

- RSA/RC4 global login; local account tickets and character selection.
- Proxy TCP account bridge on 3807 (Net7Proxy 2.46), compatibility/sector bridges
  on 3809/3810, UDP registration on 3806, handoff on 3808,
  and sector transport on 3501. Direct global TCP is 3805.
- Explicit packed packet encoding, bounded lengths, fragmented AUX frames,
  sequence/resend cache, keepalives, endpoint checks, and one-use login tickets.
- Player/ship AUX initialization, navigation maps, selection, impulse, routed/free
  warp with Q cancellation, gates and wormholes between hosted space sectors.
- Station docking camera/clearance, room/terminal layouts and 305 public NPC
  placements with imported appearances, walking,
  launch back to the exterior, and restart continuity while docked. `/launch`
  uses the same departure handler as the native station launch action.
- Impulse forward/reverse thrust, braking, yaw/pitch, normalized orientation,
  acceleration and boundary stops. The proxy's fixed-port position/heading feed
  is correlated with the selected character and used to synchronize prediction.
- A configured skybox (`backdrop_asset`), assembled Jenquai hull/engines/wings,
  ship colors, and client clock replies for original-client rendering.
- Shared MOB idle/chase/attack/death/respawn simulation, typed player damage,
  player launcher flight and native NPC beam/launcher delays. Durable shots and
  player receipts prevent duplicate impacts across acknowledgment failures.
- Max-rank development JE skills and 27 active ability variants: cloak, environment
  shields, contemplation, leech, fold, jumpstart and wormholes, including group
  support with membership/range validation.
- Multiple local accounts, native peer ships, six-player groups, invitations,
  leader permissions, native group HUD, impulse formations and chat. `/help`
  lists social commands; `/ability NUMBER` invokes a granted ability.
- Station avatars use room-scoped appearance, entry/departure and pose messages.
  Idle peers send no repeat data; moving peers update at most five times per
  second. Room privacy and visibility survive short server replacements.
- Position persistence and 250ms session checkpoints preserving UDP identities,
  clocks, replay windows, movement, skills, resources and groups across restarts.
- Source comments and [implementation notes](docs/IMPLEMENTATION.md) describe the
  wire layouts, ownership, lifecycle, verification and deliberate simplifications.

## Verification and current limits

The shared Python client passed local end-to-end proxy authentication, RSA/RC4
character selection, sector entry, map decoding, warp, loop gating/re-entry, and
position persistence. Rust unit tests cover framing, AUX flags, cryptography,
ticket ownership, endpoint checks, account hashing, and the MOB lifecycle.
The original graphical client is being tested manually; a successful headless
test does not establish that every PDA or graphical state field is complete.

Further station services, missions, trade progression and full loot policies
remain unfinished. Native NPC/vendor panel interaction remains
partially verified; disposable vendor login/buy/restart/sell tests pass.
Crafting quality/overrides and full XP/level progression remain pending;
refining now consumes recipe quantities and stores trade XP. MOB health/DPS/ranges
use archived curves and SQL modifiers; some ability timings and boss behaviors
remain simplified. Names/levels/models come from archived public content. Networking
and local state are bounded, but this legacy protocol prototype is not intended
for public deployment.

The universe importer includes deterministic public MOB spawns. Resource fields
are streamed with eligible ore contents and shared mining/depletion. Missing MOB models are recorded in each sector's
`missing_mob_models` metadata and excluded from rendering. Gate destination IDs
are preserved; the loop gate exists only in the sandbox fixture. Flight speeds,
capacities, recharge and beam parameters follow equipment/quality/passive stats.
Sandbox hull capacity uses each class's archived upgrade-six value before modifiers.
Full hull-upgrade missions and NPC ammunition AI remain unfinished. Formation
following supports impulse flight; group warp remains unfinished.

The NPC delayed-impact integration passed 202 Rust tests and Clippy with warnings
denied. Tests cover launch deadlines, player receipt/world acknowledgment
recovery, reflection rollback, target departure and authentication lifetime.
Native graphical NPC projectile playtesting remains pending.

Run checks from the repository root:

```powershell
C:\PYTHON\python.exe enb-server/tools/build.py --test
C:\PYTHON\python.exe enb-server/tools/smoke_test.py --gate
C:\PYTHON\python.exe enb-server/tools/smoke_test.py --real-sector
C:\PYTHON\python.exe enb-server/tools/smoke_restart.py
C:\PYTHON\python.exe enb-server/tools/smoke_multiplayer.py
```

The smoke test starts a disposable local server with temporary state and refuses
an address already in use. Use `--server 127.0.0.3` while your sandbox is running.
It never launches a graphical game or reads real player configs.

`--real-sector` checks all static-object delivery plus impulse and braking.
`--movement --gate` checks impulse, warp and sandbox gate re-entry together.
To reproduce the static sector export from your archived dump:

```powershell
C:\PYTHON\python.exe enb-server/tools/import_sector.py PATH_TO_NET7_DUMP.sql
C:\PYTHON\python.exe enb-server/tools/import_universe.py PATH_TO_NET7_DUMP.sql
C:\PYTHON\python.exe enb-server/tools/import_stations.py PATH_TO_NET7_DUMP.sql
```

`import_universe.py` exports all space maps and their MOB populations;
`import_spawns.py` updates one map's MOBs. Importers read public content tables
without executing the SQL or reading player/account tables. Runtime
object IDs are `100000 + source_id`; the JSON retains each database `source_id`.

## C++ reference and reading the Rust

See [the implementation notes](reference/NOTES.md) for the C++ call paths and
intentional simplifications, and [the Rust reading guide](RUST_GUIDE.md).
The exact upstream revision/file hashes are tracked in `reference/manifest.json`.
To restore the source on another machine:

```powershell
C:\PYTHON\python.exe enb-server/tools/fetch_reference.py
```

Downloaded upstream files go under ignored `reference/upstream/`; the manifest
and hash-verifying downloader provide a reproducible checkout without committing
captures, credentials, generated toolchains, or an entire duplicate repository.

Developer commands include `/d_spawn` and `/d_jump`; see
[developer command usage](docs/DEV_COMMANDS.md). Damage rules, aggro and remaining
accuracy behavior are described in [combat rules](docs/COMBAT_RULES.md).
