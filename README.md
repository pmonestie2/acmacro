# Earth & Beyond macros

Uses Python 3.13 at C:\PYTHON\python.exe.

## Run Pluto

1. Pluto launches Earth & Beyond and logs in automatically if it is closed.
   If already logged in, it uses the existing game window.
2. The macro's coordinates assume **1280 x 768**, but resolution is not enforced.
   Window borders are excluded and Windows DPI scaling is handled separately.
3. Edit **config_player.txt**, then run **run_pluto.cmd** or
   `C:\PYTHON\python.exe .\pluto.py .\config_player.txt`.
   Accept the Windows UAC prompt. Pluto automatically opens an administrator
   window because VSS needs elevation to read the locked chat log.

Stop with Ctrl+C or PyAutoGUI's mouse-corner failsafe.
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
| `player_type` | Two-letter class code used by this macro, such as `je` or `te` |
| `character_slot` | Character-selection slot, 0 through 4, top to bottom |
| `username` | Game account login; entered at game coordinates `(300, 190)` |
| `password` | Game-login password; entered at game coordinates `(300, 245)`; values are literal, without quotes |
| `mission_count` | Number of missions to collect, 1 through 7 |
| `low_job` | `true` for low-level jobs, otherwise `false` |
| `avoid_pit` | `true` to skip pit jobs, otherwise `false` |

The existing player values were migrated into the local config. Use
`config_player.example.txt` as a template for another player. Local
`config_player.txt` and `config_player_*.txt` files are excluded from Git.
The macro validates the file before login/elevation and does not send its
password or other account settings to Codex. File edits take effect on the next run.
`--low-job` and `--avoid-pit` remain optional command-line overrides.
Current location, destination, progress counters, and timestamps are runtime state
in player.py; the macro determines those while running.

The trade entry point also takes a player file: `python trade.py config_player_other.txt`.

## Configuration and chat

**macro.log** records important events with local date/time: macro start/stop,
game launch/close, login and relogin attempts/results, recovery and retry limits,
incoming private messages, sent replies and STOP actions, and chat/Codex errors.
It appends across runs, flushes each event, and is safe for background-thread
logging. Routine position/window diagnostics stay in the console. Passwords are
not logged. The separate chat_interactions.log retains detailed interaction records.

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

Save the file. Every new generation (including retries) reads it again; Pluto
does not need to restart. An already generated/queued reply keeps its existing text.
Use this file to change tone, language, reply style, and what the bot should say.

The prompt's action/response pair is transported as structured JSON:
`{"action":"OK","response":"Hi there!"}` or
`{"action":"STOP","response":"Understood."}`. Only the response text is typed
into chat. Keep editing the rules in chat_prompt.txt as usual; its reference to
a tuple means this action/response pair. Malformed results and unsupported actions
stay queued for retry. The word STOP appearing only in the response text does not stop Pluto.

When the action is STOP, Pluto attempts the response if the game chat is ready,
closes the game/proxy, and stops the macro and its timers without relogging.
STOP is handled even during relogin and does not depend on successfully focusing
the game, sending a reply, or writing the interaction log. An empty/unusable
response paired with a valid STOP still stops the macro.

Completed replies and STOP actions are appended to **chat_interactions.log** in
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
in arrival order. A generation can take up to 90 seconds; failures leave the message
queued and retry after 60 seconds while the route continues. Later messages wait for
that reply. Focus failures and relogins retain the generated text without another
Codex request. Stopping Pluto cancels the active generation.

`ENB_CODEX_EXE` can override the Codex executable path. `ENB_CHAT_MODEL` can select
a model available to your Codex account; otherwise Codex uses its default model.
History length, reply length, timeout, and retry delay are in `config.py` and take
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

```powershell
cd C:\MACROS\acmacro
C:\PYTHON\python.exe -m pip install -r requirements.txt
C:\PYTHON\python.exe -m unittest discover -s tests -v
```

Open acmacro.code-workspace in VS Code to use the configured interpreter.
