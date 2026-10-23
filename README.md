# Earth & Beyond macros

Uses Python 3.13 at C:\PYTHON\python.exe.

## Move game windows with Alt-drag

Double-click `enb_drag.exe` and accept elevation if requested. Hold **Alt** and
**left-drag anywhere inside an E&B window** to move it. The helper stays in the
system tray without a console; right-click its icon to disable dragging or exit.
Normal clicks, injected macro input, and other applications are left alone.
Ctrl+Alt dragging is excluded so AltGr is not treated as the move modifier.
Window sizes are preserved, and enough title bar is kept on-screen to recover
the window. No macro positioning code is changed.

This is a standalone 64-bit native C executable using Windows system DLLs.
It has single-instance protection and no background timer while idle. Mouse
movement is applied on a 16 ms timer only during a drag; asynchronous window
positioning avoids waiting for an unresponsive game in the mouse hook.

Source: `native/enb_drag.c`. Rebuild and run the noninteractive self-tests with
`powershell -ExecutionPolicy Bypass -File .\native\build_enb_drag.ps1`.
The build uses the local [TinyCC](https://bellard.org/tcc/) compiler and its
full Windows API headers under `diagnostics/native_tools`; alternate locations
can be passed with `-Compiler` and `-WinApiInclude`. The hook follows the
[Windows low-level mouse hook API](https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelmouseproc).

## Re-layout currently open games

Run `.\relayout.cmd` from the game desktop session. It chooses a grid for the
current window count and monitor work area: two games can sit side by side,
four use two rows, and six normally use three columns. Window sizes and game
resolution are preserved; when they cannot all fit, windows overlap evenly.
The plain `ENB` window is renamed `ENB_a`; additional unnamed windows receive
unused letter names. Existing character names are preserved. Minimized games
are restored. Use `.\relayout.cmd --list` to preview positions and names.
This command chooses an adaptive layout; the macros' fixed character layout
still applies when they start or repair their own window positions.

## Explore sector gates

`explore_gates.py` selects gates with opcode `0x17`, activates them with `0x2c`
action 18, and records an edge only when the server reports arrival in another
sector. It waits for the arriving map catalog, including gates advertised as
navigation type 1 or 2. A breadth-first search routes to remaining untraversed
gates. Confirmed crossings imply a reverse connection for planning; reverse gate
records carry `inferred_reverse=true` until actually traversed. Gate IDs remain
scoped to their sector. Only packet objects of stargate type 11 are candidates;
ordinary navigation markers named "Gateway" are excluded. The map gate list must
settle for 350 ms before choosing, so its first return gate cannot hide later exits.

Run one process and one passive `protocol/capture_jobs.py --all-packets` recorder
per character. Supply the pinned client/proxy PIDs, character name, capture path,
and a JSON seed packet list (an empty `[]` is valid). Parallel explorers merge
progress into the shared graph under a file lock. `--initial-sector` may bootstrap from saved navigation
data after independently verifying the current sector in that client; otherwise
the capture must contain its sector/map packets. `--max-jumps 0` explores all
observed reachable gates. This records an undirected planning graph with explicit
crossing evidence, not a complete galaxy census. Pass `--peer-graph PATH` to read
another explorer's completed work. A shared process-identity-pinned branch claim
prevents concurrent explorers from choosing the same unexplored destination.

Pause with `enb pause macboyje` / `enb resume macboyje`. Creating
`explore_macboyje.stop` stops that explorer; remove it before a later run.
All characters share `navigation/sector_graph.json`, with events in each
character's macro log. An uncertain selection or transition stops without resubmitting it.
When a server target-cancellation message is followed by closure of that same
attempted gate, the explorer marks it unavailable and continues elsewhere.
Peer explorers inherit that status. A closure for another gate or a stale
cancellation does not authorize continuing an uncertain attempt.

For a fixed trip, `--route FILE` reads a JSON list of `source`, `gate_name`
(an unambiguous name substring), and `destination` records. It checks every
arrival and stops at the final destination. Startup drains the existing capture
and finishes any already-submitted transition before issuing another command.

## List, deposit and withdraw vault items

From an administrator terminal in the game desktop session, run
`.\vault.cmd .\config_player_macboyje.txt --grep "white dragon"`. Deposits select
items by lenient name matching or exact numeric item ID. Names ignore case,
punctuation and minor spelling mistakes. Unknown names can be selected by ID.
A missing or unmatched filter never falls back to depositing everything. Use
`--all` explicitly for all eligible hull cargo. Add `--dry-run` to preview deposits without
transferring anything. Add `--list` to print the vault's slot numbers, item IDs,
quantities and recorded item names. Missing names are marked unknown; their IDs
and quantities are still shown. The character must already be logged in; stop its other
macro first so only this command owns the character. It also works in space.

```cmd
enb vault macboyje --list
enb vault macboyje --list --grep "unicorn"
enb vault macboyje --grep "white dragon" --dry-run
enb vault macboyje --grep "white dragon"
enb vault macboyje --grep 775
enb vault macboyje --withdraw 3302
enb vault macboyje --withdraw 3302 --quantity 2
enb vault macboyje --withdraw 3302 --quantity 2 --dry-run
enb vault macboyje --withdraw 2838 --stack
enb vault macboyje --all --dry-run
```

Withdrawal defaults to one unit. `--stack` moves the entire first matching vault
stack into one empty hull slot; `--quantity N` moves N units using one packet per
whole or partial source stack. These options cannot be combined. The command
checks the requested quantity and enough empty hull slots for the selected
source stacks before starting. It does not merge with existing hull stacks.
Before a deposit or withdrawal, the macro locally replays the item's cached
definition from the owned Net7Proxy to its client, once per item ID per run.
This receive-side `0x2025` handler emits a full `0x25` item definition so moved
items can display their names and stats without entering a station. It does not
request the item from the server. A missing cache entry or uncertain replay
stops before the inventory transfer; listing and previews do not replay anything.
The vault source and hull destination are checked before each submission and
both inventory changes must confirm before continuing. Single-unit withdrawal
in space was previously verified live; bulk withdrawals are unit-tested and
have not yet been verified live. Listing never submits packets or opens a packet capture.

The command moves whole cargo stacks into verified empty vault slots. It checks
source item ID/quantity and destination again immediately before each submission,
then waits for both inventory changes before continuing. Known route trade goods
are skipped; a definitive server rejection of other trade goods is logged and
skipped. A full vault leaves remaining cargo in the hull. Uncertain results stop
without retry. Existing vault items are preserved; stacks are not merged into
occupied slots. Pause signals are honored. Progress and the final counts go to
the console and `macro_<character>.log`, with the character name on every line.

## Run Pluto

Both Pluto and trade wait 4 seconds at startup before touching game windows,
including in singleton mode. The pause is logged so you can launch both macros.

To immediately stop all registered macros and all configured E&B clients/Net-7
proxies, run `.\kill_games.cmd` (accept UAC if requested). Macros stop first so
their recovery loops cannot relaunch games. `.\kill_games.cmd --list` only lists
matching processes. Unrelated Python programs are left running.
To stop only one character, run `.\kill_games.cmd --player macboyje`, or
`.\kill_player.cmd macboyje`. Both commands also accept a player config file:
`.\kill_player.cmd .\config_player_macboytt.txt` or
`.\kill_games.cmd --player .\config_player_macboytt.txt`. The character name
comes from the file. Add `--list` to preview the selection. Names are
exact and case-insensitive. The helper stops that character's macros first,
then every client window named `ENB_<character>` and its verified Net-7 parent.
Other characters using the same installation are preserved. Run from the game
desktop session: unnamed clients and windows on another desktop/session cannot
be attributed to a character by this helper.
The helper also scans Python command lines for this project's Pluto/trade scripts
and the elevated Pluto console wrapper, so it catches startup processes and
wrappers waiting for Enter even after their focus registration has disappeared.

1. Pluto launches Earth & Beyond and logs in automatically if it is closed.
   If already logged in, it uses the existing game window.
2. The macro's coordinates assume **1280 x 768**, but resolution is not enforced.
   Window borders are excluded and Windows DPI scaling is handled separately.
3. Edit **config_player.txt**, then run **run_pluto.cmd** or
   `C:\PYTHON\python.exe .\pluto.py .\config_player.txt`.
   Accept the Windows UAC prompt. Pluto automatically opens an administrator
   window because VSS needs elevation to read the locked chat log.

Stop with Ctrl+C or PyAutoGUI's mouse-corner failsafe.

To rename and arrange the two running game windows without starting a macro:

```powershell
C:\PYTHON\python.exe C:\MACROS\acmacro\arrange_windows.py
```

Accept the UAC prompt if requested. The original installation is named
`ENB_Lightbulb` and placed at the top-left; `EA GAMES\EnB-client-2` is named
`ENB_Lightje` and placed at the bottom-right of the primary display's work area.
Both games must already be running. Window sizes are preserved. To arrange a
different toon or installation, specify it explicitly (coordinates are physical pixels):

```powershell
C:\PYTHON\python.exe .\arrange_windows.py --toon Lightje --client "C:\Program Files (x86)\EA GAMES\EnB-client-2\release\client.exe" --x 1500 --y 100
```

The layout helper joins the same focus queue when macros are running, so it
cannot move a window halfway through their input or screenshots.

Named characters use a shared fixed layout in all macros: JE top left, TS
top center, PW top right; TT bottom left, macboyapw bottom center, TE bottom
right. Center positions use the primary monitor's work area and the actual
window width. Existing macros must reload the code to adopt layout changes.

All editable player settings are in config_player.txt. The original inventory
selling/discarding and memory writes are preserved.

Check installation without running the macro:

```powershell
C:\PYTHON\python.exe C:\MACROS\acmacro\pluto.py C:\MACROS\acmacro\config_player.txt --check
```

## Player configuration

`pluto.py` requires a player configuration filename, including with `--check`.
Double-clicking `run_pluto.cmd` passes the local `config_player.txt` automatically.
To choose another file, use `run_pluto.cmd config_player_other.txt` or pass that
file directly to Python. Relative paths passed to Python are resolved before
administrator elevation, so the same file is used in the elevated process.

Edit the `[player]` section in `config_player.txt`:

| Setting | Meaning |
| --- | --- |
| `name` | Character name, also used for the self-whisper readiness marker |
| `player_type` | Two-letter class code such as `je` or `te`, or `new` for the trade-only route |
| `character_slot` | Character-selection slot, 0 through 4, top to bottom |
| `username` | Game account login; entered at game coordinates `(300, 190)` |
| `password` | Game-login password; entered at game coordinates `(300, 245)`; values are literal, without quotes |
| `mission_count` | Number of missions to collect, 1 through 9 |
| `low_job` | `true` for low-level jobs, otherwise `false` |
| `avoid_pit` | `true` to skip pit jobs, otherwise `false` |
| `job_pickup` | `packet` (default) for packet pickup only; `ui` for the original picker |
| `job_destinations` | `auto` (default), or comma-separated `uranus`, `charon`, `monster_pit`; also determines delivery stops |
| `job_min_level`, `job_max_level` | Optional packet candidate level bounds; `0` means no bound |

With `job_destinations = auto`, `low_job = true` selects Ariel/Puck in Uranus;
otherwise it selects Charon and Monster Pit. `avoid_pit` excludes Monster Pit in
either mode. For example, set `job_destinations = charon` to collect only Charon
jobs, or `job_destinations = charon, monster_pit` with `avoid_pit = false` for both.
Packet pickup confirms each acceptance with its matching server reply. If a sent
acceptance has an uncertain result, the macro stops rather than retrying it.
Packet failures stop pickup without switching to UI. The original picker runs
only when explicitly configured with `job_pickup = ui`.

At Earth station, packet mode starts a background picker after the initial mission
count. It looks up jobs while the character walks through the station, pauses
terminal commands during vendor interaction, and accepts jobs after selling is
finished. It skips the job-room walk and joins the worker before undocking.
The worker uses no mouse, keyboard or route updates. Start, each confirmed
acceptance, and stop (including count, target and failure/cancellation reason) are
written to `macro_<character>.log`. Pickup still honors `mission_count` and the
configured destinations. UI mode keeps the original station route.
Packet pickup has a 450-second overall budget. If that expires before the target
is reached, it closes the terminal and returns the confirmed count so the route
can continue; the log records `reason=time_budget_exhausted`. A catalog that fails
to arrive within its 20-second read window is closed and retried within that same
overall budget. An unconfirmed acceptance still stops the macro without retry.

The existing player values were migrated into the local config. Use
`config_player.example.txt` as a template for another player. Local
`config_player.txt` and `config_player_*.txt` files are excluded from Git.
The macro validates the file before login/elevation and does not send its
password or other account settings to Codex. File edits take effect on the next run.
`--low-job` and `--avoid-pit` remain optional command-line overrides.
For the trade-only variant, set `player_type = new` (template:
`config_player.new.example.txt`). It buys the existing goods at Charon station,
travels to Earth station, sells, exits without entering the job rooms, and returns
to Charon. It skips mission handling, inventory disposal, and the Pluto
satellite/monster detour. The job settings remain in the config but are ignored.
In cooperative mode, `new` characters do not reserve or wait for Earth station;
the normal same-station wait still applies at Charon.
Run it with `run_pluto.cmd config_player_new.txt`; `--singleton` is supported.
Current location, destination, progress counters, and timestamps are runtime state
in player.py; the macro determines those while running.

The trade entry point also takes a player file: `python trade.py config_player_other.txt`.

## Multiple games

Run up to two Python processes under the same Windows account, as administrator,
with a separate player config each. Client paths and screen coordinates are chosen
automatically; player configs contain character/account/job settings only.
Slot 1 uses the original installation at the desktop's top-left; slot 2 uses
`EnB-client-2` at bottom-right. Actual window borders and the monitor work area
determine placement, keeping both windows on screen. Startup popups stay centered.
`GAME_CLIENTS` in config.py lists these two machine-wide installations.

Claims are atomic under the coordination state mutex. A macro selects an available
slot on startup and re-evaluates availability after closing its client for restart.
An already-running `ENB_<name>` is preferred; another named toon's window is not
taken over. Dead macro claims are cleaned automatically. A third client is rejected.
The selected installation always supplies its own `release\chat.log`, including
when a restart changes slots. Slot choices are logged.

The local `config_player_pw.txt` selects Lightbulb;
`config_player_je.txt` selects Lightje. Run each command in its
own terminal:

```powershell
.\run_pluto.cmd config_player_pw.txt
.\run_pluto.cmd config_player_je.txt
```

For another player, copy `config_player.second.example.txt` and enter the correct
account, class, character slot and job settings.

Old explicit installation/position fields remain readable for compatibility but
have been removed from local configs and examples. Net-7 launches the automatically
selected executable with `/CLIENT:`; discovery and recovery use the same path.
Windows display/compatibility settings are still external to the macro; each
client should have a 1280 x 768 game client area.

Scripts exchange focus requests and commands over HTTP on `127.0.0.1`. Each macro
hosts its own server in a background thread on an automatically assigned port;
there is no third background process or new dependency. Endpoints are discovered
through the existing named shared-memory instance registry. A Windows named mutex
still guards input and screenshots; its FIFO queue handles simultaneous requests
and disconnected/crashed peers. HTTP handlers only queue requests, never input.
A script keeps focus while nobody is waiting, then lends it at a safe boundary.
Handoffs to a different process include a 0.2-second idle pause before ownership
is granted. Reacquiring in the same process and singleton mode skip this pause.
After activating a game window, allow another 0.2 seconds for it to settle before
input or screenshots. An already focused window does not repeat this delay.
Focus turns are limited to 20 seconds, with early handoffs before longer actions.
Focus diagnostics append to `focus_<toon>.jsonl`, without console chatter. Events
record requests, ownership acquisition/release, actual foreground activation and
failures, previous owner, wait/hold duration, release reason, and procedure/line
context. Once per minute, a rolling 60s summary reports acquisition/handoff counts,
requests, total waiting time and average hold duration. Same-process reacquisition
is distinguished from a cross-process handoff. Input text/credentials are never
recorded. Named groups also record `section_started`/`section_finished`, their
duration (excluding explicit user pauses), and a `section` label on ownership events.
Use these labels to compare handoffs before and after grouping.

Use `with controller.with_focus("description"):` around a short sequence. Nested
input helpers keep the same ownership rather than handing off after each click,
key or screenshot. A queued peer gets its turn when the group ends; without a
waiting peer the current macro continues. The normal 20s limit still applies at
safe pauses, including inside a group. This context is **not** the 45s exclusive
Charon lock. Keep long loading/travel waits outside it; do not wrap an entire
route, state-polling loop, or job batch.

The 2026-10-08 20-minute observation recorded 401 cross-process handoffs, median
holds of about 0.48s, and 214s/235s of focus waiting for lightje/lightt. The grouped
sections now cover gate map selection, each polling screenshot plus immediate
response, station walking segments, vendor approach/dismissal, inventory setup
and four completed drags per turn, job sorting/page navigation, and up to four job rows.
The baseline includes the 32s exclusive deployment and a 120s same-station wait;
those remain intentional exceptions when comparing future runs.
Vendor selling retains focus for one inventory pass; repeated purchase clicks
retain focus for their short burst. This avoids a handoff and activation delay
after each click while retaining the normal 20s budget and safe page/pause checks.
Held keys, drags, command entry and focused screenshots cannot be interrupted.
Station movement lends focus after each short walking segment. Each job row keeps
selection, the 2s description load, recognition and acceptance (plus its 1s delay)
in one turn. Up to four rows share a turn (about 9-13s), then allow the peer to run.
The normal 20s cap still applies. Job and pit checks
reuse one screenshot. Long passive waits release ownership;
background PM generation and chat polling continue. General state polling defaults
to 2s, and trade/job-panel checks to 1.5s. Startup and chat readiness keep their timing.
Ordinary gate/station and satellite target/panel polling uses 8s in cooperative
mode. Singleton retains its existing 3s gate and 2s satellite intervals. The full
Charon monster/pit sequence, including its initial satellite approach, keeps fast
polling. Slow waits cap their final sleep to the remaining timeout and check once
at the deadline (e.g. a 12s wait checks at 0s, 8s and 12s).
Each gate phase allows 240 seconds. The sector progress watchdog allows 600
seconds (10 minutes), covering both sequential phases plus two minutes for UI
and sector transitions. Changing the gate budget also adjusts this watchdog.
After entering a gate or undocking, arrival is confirmed by recognizing the
expected sector/station on the map within 60 seconds. Chat pixel patterns and
class-dependent inventory icons are not transition evidence. Cooperative
transition retries yield for 8 seconds; singleton retries use 3 seconds.
Pluto's default `[player]` setting `gate_navigation = packet` uses the same
destination engine as `enb.cmd navigate`: live client sector detection, shared
sector-scoped gate IDs, server-confirmed selection and arrival, and packet
docking at Earth Station and Charon Cloister. Each travel plan and stop reason
is written to the player's macro log. An unavailable route or uncertain packet
submission stops without a click fallback or automatic retry.

Pluto keeps its existing route milestones: Uranus deliveries finish before
Charon, and Pluto satellite/Monster Pit handling finishes before docking.
Login, undocking, station rooms, vendors and job delivery interactions retain
their existing behavior. The packet engine borrows Pluto's pause, chat and focus
controller; its capture closes before relogin or client shutdown. It does not
start another macro, job worker or focus owner.

Use `gate_navigation = click` to retain the legacy map approach and docking
behavior described above. Restart Pluto after changing this setting or updating
the code. Packet mode requires the fingerprinted client/proxy build and process
access permissions.
On this ARM64 VM, reused x86 executable stub addresses produced stale execution
despite matching byte readback and an instruction-cache flush. The injector now
uses a new address for each submission, with allocation state tied to proxy PID
and creation time so macro restarts cannot reuse an address. Completed pages are
decommitted to release RAM; their addresses stay reserved until the proxy exits.
A per-proxy mutex serializes submissions. Detailed native-stage and sequence
reports are saved in `diagnostics/navigation_attempts.jsonl`.
Focus queue waiting counts toward state polling, pickup and progress watchdog
timeouts; regaining focus does not extend or restart their budgets. Explicit user
pauses still freeze those timers. Dead owners or
waiters are removed using PID plus process creation time. An unavailable focus
window causes an error before sending input.

For one macro, use `--singleton` with either Python entry point, for example:

```powershell
.\run_pluto.cmd config_player_pw.txt --singleton
```

Singleton mode retains ownership through waits and skips queue checks and focus
handoffs. It verifies foreground focus before input/screenshots and keeps necessary
game delays. It rejects another concurrent game macro; HTTP messaging remains
available to temporary senders.
The per-toon macro log explicitly records `Focus mode: SINGLETON (--singleton)`
or `Focus mode: COOPERATIVE` at startup for both Pluto and trade.

### Same-station spacing

Pluto disables the same-station lock by default at every station, so both toons
may work at the same station at once. To enable station spacing, use
`run_pluto.cmd config_player.txt --station-lock` on both macros. Keyboard
and mouse focus coordination remains active in either mode. Startup logs show
whether the same-station lock is enabled. The older `--earth-station-lock` flag
remains an alias and now applies to all stations too.

When a position is acquired or updated, cooperative macros announce their station
in the shared instance registry (also reported by HTTP `/status`). The first macro
to report a station proceeds; another in the same station releases focus, waits
60 seconds, then rechecks. If still occupied it waits another 60 seconds. The
arrival order is assigned atomically under the state mutex, so simultaneous reports
cannot make both macros wait. Departures, shutdowns and dead-process cleanup free
the station. Earth job-picking substates remain part of `earthS`.

Intentional station waits are excluded from watchdog time; HTTP/PM handling remains
available. Singleton mode bypasses station reporting and waiting. This applies to
`earthS` (including job substates), `charonS`, `somerS` and `prasadS` when
`--station-lock` is enabled in Pluto.

### Charon satellite deployment: exclusive focus exception

In `PlutoRunner.pluto_dump`, the block from `wait for sat obj` through the final
`q` (original lines 263–275) must run uninterrupted. It uses
`controller.exclusive_focus("Charon satellite deployment", timeout=45)`.

This pins the **same shared Windows input mutex**, rather than adding another
mutex. Thus two macros cannot enter this section simultaneously, and ordinary
input/screenshot operations in another macro also wait. One lock avoids lock-order
deadlocks. A contender joins the normal FIFO; its 45-second timer starts only
after acquiring focus, not while waiting in the queue.

During this section, passive waits retain focus and the normal 20-second handoff
is suspended. HTTP focus/logoff requests queue until the section exits. The full
section has a 45-second budget: satellite detection receives approximately 30s,
reserving 15s for seven deployment clicks and the final `s`, `q`, 7s wait, `q`.
If detection fails or the deadline expires, the section raises `GameUnresponsive`
for the existing recovery path instead of continuing deployment blindly.

Deadlines are checked before input and during waits. On completion, timeout, or
exception, cleanup restores normal coordination and releases ownership; existing
held-input cleanup and Windows abandoned-mutex handling still apply. Startup logs
record the section's entry/budget and release. Singleton mode bypasses the special
lock and deadline entirely and retains its original 45s satellite-detection wait.

To send logoff from another running macro:

```python
controller.send_logoff("lightje")
```

Or send one command from an administrator terminal:

```powershell
C:\PYTHON\python.exe .\send_logoff.py lightje
```

The acknowledgement means queued, not completed. At the next safe boundary,
the receiver closes only its owned game/client proxy and stops its macro without
restarting. It can also stop while waiting for another game's focus.
When a toon receives a PM decision `STOP` (for example from a GM), it sends HTTP
`logoff` to every other running game macro before stopping locally. Each peer
delivery is logged and retried once with the same request ID if needed. A failed
peer delivery does not prevent local shutdown. Received HTTP logoffs never
rebroadcast, so shutdown cannot loop; protected actions finish at their safe
boundary before the receiver stops.

Requests
use IDs to suppress duplicates, short HTTP timeouts, and a per-process token from
the local registry. `/focus/request` accepts focus handoffs, `/message` accepts
`logoff`, and `/status` reports the toon and focus mode.

Recovery and PM STOP terminate only the owned client and its proxy, using checked
process identities. They do not kill other games by executable name. Ctrl+C and
the mouse-corner failsafe stop the script and release its coordination claim.
VSS chat snapshot creation is separately serialized across processes.

### Pause and resume from SSH

Restart each macro once to load this support. From PowerShell, cmd, or an SSH
session on this machine, run:

```powershell
C:\MACROS\acmacro\pause_macros.cmd
C:\MACROS\acmacro\resume_macros.cmd
C:\MACROS\acmacro\macro_control.cmd status
```

Omitting character names controls all macros (JE and PW together). To control
only one, use `pause_macros.cmd macboyje` or `resume_macros.cmd macboyje`.
The equivalent Python commands are `C:\PYTHON\python.exe macro_control.py pause
--all` and `... resume --all`. Commands wait up to 30 seconds for acknowledgments;
`--wait 0` sends without waiting. `PAUSED` confirms the receiver has paused;
`signal pending` means it has not yet reached a safe boundary. Nonzero exit means
some selected receivers are missing or have not acknowledged yet.

Signals and acknowledgments use atomic files under `diagnostics/macro_control`,
so an SSH session does not need the desktop session's named mapping. Requests
persist across macro restarts. A command for all clears individual overrides;
later per-character commands override the all setting for those characters.
The receiver finishes its current protected input section, releases focus and
withdraws from the focus queue, then waits without sending gameplay input.
Pause/resume is logged in `macro_<toon>.log`. Resume continues the same operation;
paused time does not consume watchdog, pickup or readiness timeouts. The game
stays logged in and any warp already underway can finish. Remote logoff and PM
STOP still work while paused. The pause signal does not stop existing macros
until they have been restarted with this version.

## Configuration and chat

**macro_<toon>.log** records important events with local date/time: macro start/stop,
game launch/close, login and relogin attempts/results, recovery and retry limits,
incoming private messages, sent replies and STOP actions, and chat/Codex errors.
It also records mission counts with their source, pickup targets and batch count
changes, job-scan matches/accept clicks, elapsed time, pickup completion/timeouts,
and route paths. Accept clicks are attempts; the subsequent count verifies them.
It appends across runs, flushes each event, and is safe for background-thread
logging. Routine position/window diagnostics stay in the console. Passwords are
not logged. The separate chat_interactions_<toon>.log retains detailed interaction records.

Mission counting prefers the owned character's passive `0x001b` player-AUX
mission list. It counts occupied named mission slots, including promotion/story
missions; an empty slot's ID does not count. Capture starts before login in both
focus modes. A complete snapshot is required: starting midway through an existing
session may initially use the screen estimate. Missing packets, unsupported AUX
layouts, stale capture, or a changed proxy/session invalidate the packet count.
Fallback counts log the first-three-row forfeit checks and recognized mission
index so counting problems can be diagnosed. The fallback remains a pixel-based
estimate. No packet commands or automatic relogins are used to obtain the count.

Pluto saves a timestamped UI screenshot in `diagnostics/` on runtime errors and
before recovering from a lost or unresponsive game. The path and error appear in
`macro_<toon>.log`. Capture filenames include the toon. Captures use native game-client pixels, or the desktop if the game
window is unavailable; visible chat or login details may appear. Existing captures
are kept and excluded from Git. Normal stops and the mouse failsafe do not capture.

config.py contains the server IP, chat-log path and Codex reply settings.
NET7_PROXY, ENB_CLIENT and ENB_CHAT_LOG environment variables can override executable/log
paths. Net-7 is discovered in standard installation directories.

chatproc.py reads the entire log as text directly from a fresh shadow snapshot,
then deletes the snapshot. No separate log copy is saved. At macro startup,
existing log history is skipped. New private messages are queued with their
sender, including during relogin. The self-whisper marker only confirms that
the chat UI is ready; it does not discard messages. Each queued message gets
an individually generated Codex reply via `/t sender reply`. With action `OK`, the macro continues. Replies
wait until login completes and the game can be focused. The queue lasts for
the current Pluto process; messages never delivered while offline cannot be
recovered.

### Edit the reply prompt

Open **[chat_prompt.txt](chat_prompt.txt)** in Notepad and edit the instructions:

```powershell
notepad .\chat_prompt.txt
```

Save the file. Every new generation reads it again; Pluto
does not need to restart. An already generated/queued reply keeps its existing text.
Use this file to change tone, language, reply style, and what the bot should say.

The prompt's action/response pair is transported as structured JSON:
`{"action":"OK","response":"Hi there!"}` or
`{"action":"STOP","response":"Understood."}`. Only the response text is typed
into chat. Keep editing the rules in chat_prompt.txt as usual; its reference to
a tuple means this action/response pair. Malformed results and unsupported actions
produce a STOP decision. The word STOP appearing only in the response text does not stop Pluto.

When the action is STOP, Pluto stops its route and timers, then attempts the
response if game chat is ready. After pressing Enter it gives the client one
second to transmit before broadcasting peer logoff and closing its game/proxy,
without relogging. If sending fails or chat is unavailable, it still broadcasts
and closes without the transmission delay.
STOP is handled even during relogin and does not depend on successfully focusing
the game, sending a reply, or writing the interaction log. An empty/unusable
response paired with a valid STOP still stops the macro.

GM badges in incoming chat are passed separately as `sender_is_gm`, while the
reply is addressed to the first player name after the timestamp. GM status is
retained in that sender's in-memory history and recorded in the interaction log.
The reply prompt checks for a confused or unreliable exchange before composing
small talk: repeated GM greetings after a reply, reports of missing replies,
automation investigations, unsupported GM instructions, malformed recipients,
or a GM conversation with two replies already sent call for STOP. A first
ordinary greeting can still receive OK. STOP broadcasts to the other macros;
supervision must never restart a macro intentionally stopped by GM/PM STOP.

Completed replies and STOP actions are appended to **chat_interactions_<toon>.log** in
the project directory. Each line is a JSON object with UTC timestamp, player,
sender, incoming message, response, action, and `response_sent`. STOP records also
include `game_closed` (whether the game window disappeared) and any error.
The log accumulates across runs; conversation history remains in memory for the
current run. Replies attempted while focus is unavailable are retained, not
recorded as successfully sent. A normal log-write failure stops the macro after
acknowledging the sent reply, avoiding duplicate sends.

Codex receives the current message and only that sender's last 10 messages and
the replies actually sent by Pluto. Sender names are matched without case sensitivity.
History survives game relogins and clears when Pluto restarts. It starts with
messages received during this run, not old chat-log history. Manual replies typed
outside Pluto are not added to this history.

### Codex connection

The installed Codex CLI uses its saved login, including a ChatGPT login; the
macro never reads or copies authentication tokens. No separate API key is needed
with the existing ChatGPT login. Each reply uses your Codex account's usage limits.
See [Codex non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode).
If authentication expires, run `codex login` under the Windows account running Pluto.

Generation runs in a background thread with a hidden console, an empty temporary
working directory, and a read-only Codex sandbox. Shell tools, apps, plugins,
browser/computer use, and multi-agent tools are disabled. Game messages and the
sender's conversation context are passed to Codex as chat data over standard input.
Replies are limited to 180 ASCII characters and one line for the game's keyboard input.

The existing 20-second chat polling schedules ready replies. Requests are processed
in arrival order. A generation can take up to 90 seconds. A timeout, unavailable
Codex, invalid output, or any other generation failure is logged and produces
`STOP` with an empty response. The next chat check uses the normal STOP path:
stop the route, broadcast peer logoff in cooperative mode, and close the game/proxy
without relogging or retrying generation. Focus failures and relogins retain successfully generated text without another
Codex request. Stopping Pluto cancels the active generation.

`ENB_CODEX_EXE` can override the Codex executable path. `ENB_CHAT_MODEL` can select
a model available to your Codex account; otherwise Codex uses its default model.
History length, reply length, and timeout are in `config.py` and take
effect after restarting Pluto. No extra Python package is required for this connection.

Pluto allows at most three relogin attempts since the last detected progress,
and at most three in any rolling 10-minute window. Either limit stops it.
Failed attempts count too. A route position/checkpoint change resets the
progress counter, but does not clear the 10-minute history. Simply recognizing
the current location after login does not count as progress.
The first cold-start login is separate; subsequent attempts count toward the
limit. Before a fourth relogin, Pluto closes the game/proxy and stops all its
timers. Login success does not reset either counter. Stuck detection is suspended
during login, and its progress clock restarts when login finishes.

If the game window disappears during the route, a wait, or a geometry check,
Pluto interrupts that operation and relogs using the same controller, retry
counters, player config, and queued chat history. Crashes during cold-start
login consume the cold-start exemption once; further attempts count toward
the same relogin limits. An existing window that cannot be focused remains
a focus error, rather than repeatedly restarting the game.

A missing chat-startup marker after 60 seconds also triggers kill/relogin, even
when the frozen game's window still exists. Repeated marker timeouts count toward
the same three-relogin limits; they do not reset chat history or retry counters.

On shutdown, new chat polls are stopped and the active poll is allowed to finish
before Python exits. WMI class caches are cleared and remaining COM references
are collected on the thread that read the log before that thread uninitializes COM.

From an administrator Python session in this folder:

```python
from chatproc import read_chat_log
text = read_chat_log()
```

## Setup and tests

Gate entry in cooperative mode takes exclusive focus when fresh player packet
coordinates put the toon within **10 km** of its destination gate. The existing
global focus mutex is held for at most **20 seconds**, or until the entry button
is clicked, whichever comes first. Inside that turn the panel is checked once
per second. Budget expiry releases focus and continues the gate wait; it does
not trigger relogin. Each expired turn offers the other macro a handoff before
requesting another turn. Queue waiting is excluded from the ownership budget.

A position-only background thread in each macro observes its own proxy's UDP
traffic, using recorded sector-specific gate coordinates. It performs no input,
hostile detection, warp submission or disk packet recording. Stale positions,
unknown coordinates, another sector or a restarted proxy disable the special
lock and retain normal polling. Singleton retains its original behavior and
does not start the position thread. No separate packet recorder is required.

```powershell
cd C:\MACROS\acmacro
C:\PYTHON\python.exe -m pip install -r requirements.txt
C:\PYTHON\python.exe -m unittest discover -s tests -v
```

Open acmacro.code-workspace in VS Code to use the configured interpreter.
Startup dialogs are named `EBPopup` and retain their centered position. The macro
presses Space to advance, follows the same client process to its main window,
and only then applies the `ENB_<toon>` title and configured position. A popup
that remains open after three Space attempts triggers recovery.

Gate departures reuse a verified small map instead of repeating zoom/pan setup.
A confirmed sector arrival prepares the next route before releasing the shared
focus mutex. Target selection and warp engagement share one exclusive turn,
bounded to 20 seconds; movement packets confirm warp motion, while missing
confirmation returns to the ordinary gate wait without forcing a relogin.
The next travel step consumes the prepared target without resending navigation
or pressing q again. Singleton mode bypasses the shared lock, and the Charon
satellite deployment retains its separate 45-second budget.

Gate selection waits accept both inactive and already-active panels, including
when a focus budget expires while travelling. If the selected panel disappears,
the wait checks every 16 seconds and reselects the same map target; it never
resends packet navigation or presses q as part of this repair. Foreground
acquisition gets one bounded retry before reporting a failure.

Station collision checks discovered during map recognition are deferred until
the outer input action ends. Any 60-second station wait runs without the input
mutex or an exclusive-focus deadline; later position updates replace an earlier
pending station check. Singleton mode still bypasses station coordination.

The `new` trade-only mode neither reserves stations nor waits for another toon
at the same station, including Charon. Shared input focus still coordinates
clicks and screenshots.
## Fast vendor macro

NPC names can be collected without screenshots with `extract_npc_names.py`.
It reads captured station rosters and space-NPC avatar packets, and can also read
cached avatar names and mission references from a client pinned with
`--client-pid` and `--client-born`. Its default output is
`protocol/npc_name_catalog.json`. It sends no packets or game input. Names found
only in memory have no invented NPC ID; sector-local space IDs and historical
source-roster IDs remain separate from current station NPC IDs. A client only
contains data it has loaded, so this is not a complete live-server NPC database.

`vendor.py` runs **one character per process**, using the same `[player]` config
as `pluto.py`. Run up to four copies with different character configs; they
share the game installation. Login uses the existing coordinated input focus.
Each copy discovers only its named character window or the client launched by
its own proxy. All macros use fixed character positions on the primary monitor:
**macboyje upper left, macboypw upper right, macboytt lower left, macboyte lower
right**. These positions are independent of launch order, installation slot, and
`window_x` / `window_y`. Other character names retain configurable placement.
Windows may overlap when the desktop cannot fit them. `arrange_windows.py` with
no arguments applies the same positions to the named running clients, including
clients sharing an installation.

```powershell
C:\PYTHON\python.exe vendor.py config_player_je.txt
# In another terminal:
C:\PYTHON\python.exe vendor.py config_player_pw.txt
```

The wrapper accepts the same arguments: `vendor.cmd config_player_je.txt`
(or `vendor.cmd config_player_je.txt --check`). Use `--slots 18` to set the
maximum purchase size per round, overriding `vendor_quantity` in the config.
The default is 22. With no arguments it uses
`config_player.txt` beside the script. Run the trading macro from an administrator
terminal, as with Pluto.

The macro launches and logs in when its named client is absent. Each config needs
a different character name and the correct account and character slot. It uses
the configured `client_exe` or `game_dir` when present, otherwise the default
installation, without allocating Pluto's separate installation copies. Login
expects the usual **1280 x 768** game resolution. An unrelated unnamed running
client is left alone. Packet trading does not need foreground focus or station
travel, and does not start Pluto's job or shared-file chat polling routines.

Add these optional values to the existing `[player]` section:

```ini
vendor_route = raw-data
vendor_cycles = 0
vendor_quantity = 22
```

Routes: `raw-data` buys Raw Data at NPC 126 and sells at 636; `chocolate` buys
Aromatic Chocolate at 636 and sells at 126; `charon-earth` buys Castor Creature
Research Crates at 536 and sells at 31. `vendor_cycles = 0` runs continuously;
a positive number runs that many rounds. Quantity accepts `1` through `40`.

The sale rule is the configured route's item ID: Raw Data **6549**, Aromatic
Chocolate **6548**, or Castor Creature Research Crates **5258**. The macro reads
all current cargo slots from its pinned client's inventory cache. Existing
matching cargo is sold first, without buying anything. When no matching cargo
remains, the next round buys once and sells matching items. Other cargo is left
alone regardless of its slot or when it was acquired.
The configured purchase quantity is a maximum. Each purchase is capped by the
character's usable cargo capacity and verified empty slots, and free space is
checked again immediately before submission. For example, an empty 18-slot hold
buys 18 items even with `vendor_quantity = 22`. Unknown or locked slots do not
count as free. A full hold stops before buying; capacity and quantity are logged.

Inventory reads verify the client executable fingerprint, PID/birth, ship GameID,
array and item structures, and two consistent snapshots. Before each sale the
slot's current item ID and quantity are checked again, including after native
preflight. A sale's decrement/clear must appear in the client inventory before
continuing. If two inventory reads differ, only the reads are repeated for up to
one second (excluding explicit pauses). Persistent instability, invalid client
identity/structures or uncertain transfers stop the macro without retrying a
transfer. Logs include existing matching cargo count,
allowed item ID, selected slots, sale quantity and stop reason.
Vendor console and file log lines include the character name after the timestamp,
for example `[20:33:26] [macboyje] Vendor trading stopped: ...`. This also labels
shared controller diagnostics and background event logs in that vendor process.
A fresh server cargo reply containing the purchased item and requested quantity
can confirm a purchase while the client's inventory cache lags. Earlier replies
are discarded before buying. This permits opening the seller; it does not
authorize a sale. If the client still reports no matching cargo there, the macro
waits up to ten seconds and then stops without another purchase or sale.
Any incoming private message on this character's proxy stops trading before
the next submission, requests logoff from all other registered macro instances,
then closes this character's owned client/proxy and exits `vendor.py`.
Peer delivery outcomes, sender and stop reason are logged. This uses live
chat packets, so a shared-install chat log cannot confuse character ownership.
No chat reply is generated. Peer logoff uses the existing HTTP STOP mechanism;
receiving peers stop their own clients without rebroadcasting. Peer-delivery
failure still closes the local game and is recorded in the macro log.
Every round is followed by a random
**0.3–0.8 second** delay before the next round. Native failures and vendor-opening
timeouts stop the macro without retrying a purchase or restarting the client.
Each packet also checks that its owned game window still exists and answers a
focus-free `WM_NULL` probe (250 ms per attempt). A probe timeout holds submissions
and checks the same owned window for up to five seconds, including focus-wait
time and excluding explicit user pauses. PM detection and stop checks continue
during this grace period. Recovery resumes normal checks; persistent timeout,
a missing window or a changed client identity stops the macro and logs the reason.

`--check` validates a config without launching or sending packets. Events go to
`macro_<name>.log`. The existing `macro_control.py pause/resume <name>` commands
also apply to this macro. Ctrl+C stops it; from SSH, creating
`C:\MACROS\acmacro\vendor_<name>.stop` stops further submissions. Remove that
file before restarting. Stopping leaves the game open. Explicit pauses do not
consume vendor reply deadlines.

### Find an NPC and open trade

Search local NPC names and captured item names with `npc_trade.cmd --grep "orb wever"`
or `npc_trade.cmd --grep "julie bristol"`. Matching ignores case, punctuation,
apostrophes and accents, and tolerates minor spelling mistakes. Results include
the station NPC ID, available name, and matching items. Historical lounge roster
IDs and sector-local space-object IDs are excluded. Names from the local server
SQL are labeled separately from recorded interaction names; missing names remain
unknown. Search requires neither an administrator nor a running game.

Run `npc_trade.cmd ENB_macboytt --id 32` from an administrator terminal to open
the Orb Weaver vendor on that exact window. The script needs no player config or
credentials; it attaches only to the named existing client and its verified proxy,
and leaves trade open after the server acknowledgement. It does not buy or sell.
Use `npc_trade.cmd ENB_macboytt --id 28 --dialogue` for dialogue only. Without a
recorded trade choice, only an explicit trade offer in the live greeting is
selected; otherwise the script leaves the greeting open. A changed recorded reply,
timeout, or session failure stops without retry. Acknowledgement does not prove
that the game rendered the trade window. Run in the desktop session containing
the game; a competing macro for that character must be stopped first. The script
does not launch, relog, or close a game. It honors macro pause/stop controls and
writes opening outcomes to the character's macro log.

### Navigate to a recorded sector

`enb.cmd navigate macboytt "Blackbeard's Wake"` attaches to that logged-in
character's existing `ENB_macboytt` window. An exact window title or player config
path is also accepted; a name/window needs no login credentials. Stop another
macro controlling the same character first. Run from the game's administrator
desktop session. This command neither starts a game nor restarts a failed one.

Add `--dock` to dock at the first recorded station in the destination sector:
`enb.cmd navigate macboyje "Antares Frontier" --dock`. If multiple stations are
recorded, the lowest local station ID is selected. A station name can also be
used directly, for example `enb.cmd navigate macboyje "Earth Station"`.
Stations are stored with their sector and packet evidence in
`navigation/stations.json`; navigation records newly observed station objects
automatically. Docking selects the sector's own station ID, waits for selection
acknowledgment, submits one action-7 completion, and confirms a fresh station
arrival from the server. It stops without retry on an uncertain or different
arrival. No recorded station produces a clear error before travel begins.

The command uses the shared `navigation/sector_graph.json` and finds the shortest route
through confirmed crossings and inferred return gates. IDs remain scoped to the
source sector and can be reused by another character without rediscovery. It
also pairs a uniquely observed named return gate, such as `Vishao's Gate`, with
a confirmed incoming crossing bearing the same name; the return uses its own
locally observed ID and remains marked as inferred until crossed. It
selects each recorded gate through the native proxy handler, correlates its
outgoing selection with the server's target acknowledgment, sends one gate
activation, and waits for the server's actual sector
arrival before continuing. Access restrictions still apply. Unknown routes,
rejected gates, changed identities and uncertain submissions stop without retry;
the command does not explore missing connections automatically.
The client target cache may retain its previous value for an undiscovered gate;
the fresh server acknowledgment permits that case. A newer target selection,
changed client target, sector or owned ship identity still cancels submission.
If an identical selection receives no target echo, one read-only verb request
can establish the server's current selected object from a fresh matching reply.
Selection and gate activation are never retried.

Current location is read directly from the owned, fingerprinted game client's
active player state; no separate recorder is required. The command starts its
own packet capture before detecting the sector and planning the route.
`--from-sector Inverness` overrides detection with a manually verified location.
`--seed PATH` supplies optional current-session packet history; it does not
override the live client's current sector.
`--plan` prints the route without sending commands; `--graph PATH` can be
repeated to select other graph inputs. Planet landing is available only when a
verified action-8 entry exists in `navigation/sector_transfers.json`; gate graph
labels alone never establish a planet transfer.
Destination names accept punctuation differences, unique partial names and clear
typos (for example `niffle cloud` or `magnesi`). Similar matches are listed as
ambiguous. The printed route uses the resolved full sector names.
For a recorded planet transfer, navigation sends one native approach to that
planet ID, requests its live verb availability and waits for current land verb 2
to be enabled before submitting one landing action. The default disabled verb
group is distinct from the current group; it does not establish a range failure.
Changed or stale availability cancels landing at native preflight. Arrival must
still be confirmed by an actual server sector transition.

Capture runs on the command's main thread, uses its own sockets and closes them
on exit. Logs go to `macro_<name>.log`, packet records to
`diagnostics/navigate_<name>_<timestamp>.jsonl`, and confirmed progress to
`navigation/sector_graph.json`. `enb.cmd pause/resume <name>` also
controls navigation; explicit pause time is excluded from deadlines. To stop
from SSH, create `navigate_<name>.stop` in the repository, and remove it before
starting again. Incoming private messages stop navigation before further
submissions and leave the game running.

### Stop one character's game and proxy

Run `kill_player.cmd macboytt` (or `kill_player.cmd ENB_macboytt`) to stop that
character's macros first, then all matching named game windows and their owned
Net-7 proxies. `kill_player.cmd macboytt --list` previews the targets. A player
config path is also accepted. The helper requests administrator access when
needed. It verifies process identities, preserves proxies shared with other
characters, and never uses a shared game installation alone to assign ownership.
Unnamed clients and orphan proxies without a matching client cannot be assigned
to a character by this command. Run it in the game's desktop session.

### Unified game utilities

Use `enb.cmd` for the utility commands. Pluto and continuous vendor trading keep
their existing entry points. `enb.cmd --help` lists the utilities; each subcommand
also accepts `--help`.

```cmd
enb start ENB_a
enb kill ENB_a
enb kill ENB_macboytt --list
enb find "orb wever"
enb find "julie bristol"
enb trade ENB_a 32
enb trade ENB_macboytt 28 --dialogue
enb windows
enb layout
enb layout --fixed
enb drag
enb drag stop
enb vault macboyje --list
enb vault macboyje --grep "white dragon"
enb manufacture ENB_macboyje
enb pause macboyje
enb resume macboyje
enb status
```

`enb start ENB_a` launches a fresh Net-7 proxy and client from the shared game
installation, names only that proxy's new game window, and returns at the login
screen. Log in manually. No player config, credentials, or macro are used.
`start_game.cmd ENB_a` is the standalone equivalent.
Player config paths also work: `enb start .\mbboytt.conf` reads the window name
from `[player] name` and honors `client_exe` or `game_dir` from the config.
Relative client paths are resolved against the config directory. `--client`
overrides the config. Credentials are not passed to the proxy or printed; login
remains manual. `.conf`, `.ini` and `.txt` files use the existing player-config
format. Add `--check` to validate the resolved target without launching or UAC:
`enb start .\mbboytt.conf --check`. Relative config paths are preserved when
the unified command changes its working directory or elevation starts a helper.

Existing names are rejected,
and concurrent launches with the same name are serialized. `--client`, `--proxy`,
and `--timeout` override the executable paths and startup deadline. The helper
requests administrator access when required. It records verified process IDs
and birth identities under `diagnostics/named_games/`. A startup timeout does not
retry or terminate the new proxy; the error identifies its PID for inspection.

`find` (alias `grep`) searches NPC and item names. `trade` accepts an exact window
title, including `ENB_a`, without needing a player config. Vault unloading still
requires the named character's config and accepts that config path directly.
`layout` uses the adaptive grid; `--fixed` selects the normal fixed character
positions. `drag` launches the native helper; `drag stop` closes it. Pause/resume
without names applies to all macros. `enb kill-all` explicitly stops all games and
macros; add `--list` to preview. Game/window commands run in the game's interactive
desktop session. These utilities never log in or start a farming macro.

`enb manufacture ENB_macboyje` (standalone: `manufacture.cmd ENB_macboyje`) runs
one full-hull Callipus M2 batch, then sells only item 3199 to NPC 36. The character
must already be logged in at its manufacturing terminal with the recipe learned
and the three components in its hull. Its matching player config is required.
Use an administrator terminal. This entry point neither logs in nor travels.
`--batches 5` requests five batches; `--batches 0` repeats until stopped or a build
cannot be confirmed. Repeating batches after reopening the vendor has unit
coverage but has not yet been live-verified. The original one-batch sequence has
been verified live on JE: 18 manufactured and 18 sold after filling a 24-slot hull.

Use `enb manufacture ENB_macboyje --parallel 8` to choose up to eight builds in
flight together. Any positive integer is accepted and capped by the currently
free hull slots. The default is one. Two overlapping builds have
been verified live on JE. Selling uses `max(1, parallel // 2)` in-flight sales:
for example, `--parallel 8` manufactures up to eight at once and sells up to four
at once after the hull fills. Each sale is confirmed separately; only one sale
from a given cargo slot is in flight at a time, including for stacks. Parallel
selling has unit coverage; the live-verified sale sequence used one at a time.
The recipe is selected once per batch. Creation is
confirmed only by the finished item quantity increasing; no component IDs or
consumption checks are used. Sale begins only with no free hull slots and
includes all matching Callipus M2s already held before the script started.
Each sale verifies the item ID before submission. Unknown results, session changes and timeouts
stop without retry or selling a partial batch. Pause/resume and the
`vendor_<character>.stop` file apply. Routine events go to the character's macro
log; batch state goes to `diagnostics/manufacture_<character>.json`. Ctrl+C stops
the command and leaves the game open. This implementation supports the observed
Callipus M2 recipe and NPC 36 only.

## Internal web client

The separate [web application](enb-web/README.md) and
[shared game protocol library](enb-protocol/README.md) live in this repository.
Install with `C:\PYTHON\python.exe -m pip install -r requirements.txt`, then run
`web.cmd` and open `http://192.168.47.128:8765`. The browser asks for account
credentials and character selection. It displays the live sector map and ship
position and offers warp/gate controls for selected map objects. See the web
README for legacy certificate pinning and verification limits.
