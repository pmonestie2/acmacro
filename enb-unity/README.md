# 42 and Beyond: Unity Client

A desktop space client for the existing local Rust server, with a solid 3D ship,
procedural asteroids, stations, gates, a distant ringed planet, and a sector chart.
Nearby players appear as solid replacement ships with their toon names and interpolated
movement. The server controls their visibility, including range and cloaking; ships
are removed when they leave visibility or log out. Mobs have distinct procedural models,
server names, interpolated movement, hostility markers, shields and hull readouts.
Positions and travel come from the server. Models are original procedural
placeholders; original game art and Net7Proxy are not required.

## Open and run

On the development Mac, Unity 6.3.13f1, Unity Hub, and Rosetta 2 are installed,
and the standalone application has been built. Double-click **Play 42 and Beyond.command**
to connect to the Windows VM server. The launcher installs the current build in
`~/Applications/42 and Beyond.app` and writes its game log under
`~/Library/Logs/42 and Beyond/`, so the game does not request access to the
project's Documents folder. Quit the game before installing a newly built version.
The window can be resized; the HUD scales at small sizes.

Double-click **Play Local.command** in this directory to start the game and its own server together. It automatically
connects the demo character, keeps its state in ignored `.local-state/`, and stops
the owned server when the game closes. It never stops an unrelated server.
Launching it again brings the existing game forward instead of starting another
server. A project-local operating-system lock prevents simultaneous launches
from racing; it releases automatically even if a launcher exits unexpectedly.
Close the running game before using `--rebuild` or changing maps. Port conflicts
with another server are reported without a Python traceback, and failed startup
attempts preserve earlier server log output.

From a terminal, the equivalent is:

```sh
python3 enb-unity/Tools/play_local.py
```

Use `--rebuild` after changing client code, or `--sandbox-map` for the small test
sector. Normal play does not require opening the editor. The generated Mac app
is `Builds/42 and Beyond.app` and supports both Apple Silicon and Intel Macs.

For a fresh machine or editor development:

1. Install Unity Hub and a Unity **6.3 LTS** editor for your platform. The project
   records `6000.3.13f1`; a newer 6.3 LTS patch can upgrade it. Choose the Apple
   Silicon editor on an ARM Mac. Unity also requires Rosetta 2 for supporting
   tools, even when the editor itself runs natively on Apple Silicon.
2. In Unity Hub, **Add project from disk** and choose this `enb-unity` directory.
3. Open `Assets/Enb/Space.unity`. The runtime bootstrap creates the scene's
   camera, geometry, and connection panel when you press **Play**.
4. Start the Rust server in a separate terminal, from the repository root:

   ```sh
   cargo run --locked --manifest-path enb-server/Cargo.toml -- --bind 127.0.0.1
   ```

5. Press Play, enter `127.0.0.1`, and connect. A fresh server's demo account is
   `local` / `local`. Use a space character that is not already logged in elsewhere.
   Passwords are not saved in project settings or PlayerPrefs.

To keep prototype play separate from existing local characters, add
`--state-dir /tmp/enb-unity-play` to the server command. Use a persistent directory
instead if you want that prototype character to survive temporary-file cleanup.
The default map is Asteroid Belt Alpha. For the four-object test map, also add
`--content enb-server/data/sector.json` when running from the repository root.

To keep the server in the Windows VM, start it bound to its private LAN IPv4
address and enter that address in the connection panel. The existing TCP/UDP
ports must be reachable from the Mac. No server changes are required. This client
uses the local plaintext account bridge, so it accepts only explicit private or
loopback IPv4 addresses; it does not connect to the public game service.

## Controls

| Input | Action |
| --- | --- |
| W / S | Set forward / reverse thrust |
| Space | Brake, or cancel warp |
| A / D | Turn while held |
| Up / Down | Pitch while held |
| RMB / wheel / C | Orbit / zoom / reset camera |
| Tab | Toggle chase camera / top-down sector chart |
| E | Open ship loadout |
| X | Fire on the selected MOB, or stop active weapons |
| Enter | Open chat and server command line |
| Click a target-list entry or world label | Select a destination |
| Click a ship in space or Contacts | Select that ship |
| Click a wreck in space or Wrecks | Select it, then open or tractor its loot |
| F / Warp to target | Ask the server to warp to the target |
| G / Activate gate | Activate a nearby selected gate |

The VM launcher uses the running server at `192.168.47.128`. The client reads that account's available toons and lets you
choose one before entering space. The current Windows VM has narrow inbound
firewall rules for TCP 3807 and UDP 3501, 3806, and 3808 from the Mac's VMware
host interface. If VMware assigns it a different address, edit the host in the
shortcut or type the new address in the login panel.

Enter in flight opens the server chat command line. Type `/d_jump BBW` or
`/d_spawn 46 3` and press Enter to use the server's developer commands.

Warp to a gate first, then travel through it once stopped nearby. The server's
gate sequence takes approximately 15 seconds. Loss of window focus sends brake
and neutral steering. Disconnect and leaving Play mode cancel and join the
network worker before its session is released.

## Players, groups and mobs

The left **Contacts** list shows visible players and mobs. Type an online toon
name to invite somebody anywhere, or select a nearby player and choose **Invite
to group**. **Who is online** prints the available names in the message history.
The invited client can accept or decline; the group
panel reflects the server roster and leader role. Members can leave; leaders can
kick members or disband. Invitations expire after the server's 30-second window.
The roster lists the other members, with your own role in its heading. The leader
can choose Slot Back, Block or Pipe formation and release it later. Members can
form up within 5 km of the leader, or leave formation without leaving the group.
The leader can share the selected target with the group.

Press Enter for chat. The checkbox chooses group chat or sector chat. A short
message history remains visible. Slash commands still work, including `/invite
NAME`, `/accept`, `/decline`, `/leave`, `/disband`, `/kick NAME`, `/d_spawn
Achilles Drone 1` and `/d_jump`. Typing in chat suspends flight-key input.

Select a mob in Contacts or click its ship in space for shield and hull readings.
Hostile contacts have red markers; other NPCs have gold markers. Press X or use
**Fire weapons** to target and fire; press X again or use **Stop weapons** to stop.
The server controls firing, hits, damage, movement and death. The client shows
weapon streaks and hit numbers. The ship loadout panel lists cargo, equipment,
weapon and device mounts; select cargo and a mount to equip, or select installed
gear to remove. Ammo can be loaded through the same server move command. Defeated
mobs leave server-owned wrecks. Select one within 2 km, open it, and tractor any
listed item into cargo. The radar shows nearby contacts and navigation targets.
Models are placeholders rather than the original NPC assets. Station interiors
and the full original combat and equipment UI remain unfinished.

## Implementation boundaries

- `Assets/Enb/Protocol`: engine-independent .NET Standard 2.1-compatible C#.
  Local TCP account bridge, character selection, UDP registration/handoff,
  ordering, bounded fragmented-frame reassembly, resend requests, keepalives,
  navigation decoding, player/MOB lifecycle and resource decoding, group AUX state, chat, motion commands,
  warp, and correlated gate activation.
- `Assets/Enb/Client`: Unity main-thread input, shared solid meshes, camera and immediate
  mode prototype HUD. One Unity unit is 100 server meters; the view is centered
  on the player. Server +X maps to Unity +Z, and server +Z maps to Unity +Y.
- `ClientWorker`: one session owner; immutable display snapshots and a bounded
  input queue cross the thread boundary. Socket polling is cancellation-aware.
- Built-in rendering with lit surfaces, procedural sky and planet shaders, shared
  asteroid meshes, and interpolated server poses. No asset-store dependencies.
- Rendering is capped at 60 FPS in the foreground and 15 FPS in the background.
  Unchanged static scenery is retained; navigation sorting is refreshed at 4 Hz.
  Normal play uses the standalone app, without the Editor's extra overhead.
- The combat and loadout panels cover selection, weapon toggling, health, cargo,
  hardpoints, equip/unequip, and wreck loot. Station interiors and docking remain unfinished.
  The account bridge supplies the toon names and selected server slot.

Physical scenery uses server positions and scale with approximate replacement
models. HUD markers stay readable independently of physical model sizes. Labels
are capped and separated to avoid overlap. The distant planet is decorative.
The code does not change the server, launch the original client, or start/stop
the Windows VM.

## Verification

Run pure protocol checks with a .NET 8 SDK; there are no NuGet dependencies:

```sh
dotnet run --project enb-unity/Tests/ProtocolChecks.csproj
```

Run the disposable local integration test from the repository root:

```sh
python3 enb-unity/Tests/run_local_smoke.py
```

On Windows, use `C:\PYTHON\python.exe` instead of `python3`. The harness builds
Rust and C#, starts an owned loopback server with two temporary accounts, checks
mutual player visibility, movement, logout removal, reentry, group invitation/decline/accept,
roster updates, group chat, kick/leave/disband, formation actions, inventory moves,
server MOB state, weapon effects, damage, wreck loot, and stops it
in a cleanup block. It fails if the ports are occupied rather than stopping any
existing server. It must not be pointed at a live character session.

Verified on macOS: C# protocol checks; actual local bridge login, character
selection, UDP entry, map and position delivery, impulse, warp, gate activation
and destination entry, explicit logout/re-login, worker cancellation and join.
Unity builds and graphical checks are run separately from protocol checks.
Unity's editor can log an internal SearchDatabase indexing exception even when
compilation and Play mode succeed. Keyboard feel still needs human feedback.

Reproduce the build and graphical check with:

```sh
python3 enb-unity/Tools/verify_unity.py
```

It owns a disposable server, runs the editor, and saves `chase.png`, `chart.png`,
`asteroid.png`, `station.png`, `gate.png`, and logs under ignored `Builds/verification/`. Inspect the resulting images;
file existence alone does not establish that a layout looks correct.

Launcher regression checks (macOS, no game clients are driven):

```sh
python3 -m unittest discover -s enb-unity/Tests -p test_launcher.py -v
```
