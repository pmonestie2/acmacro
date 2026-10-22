"""Machine-specific settings. Paths do not depend on the working directory."""
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
MACRO_LOG_PATH = PROJECT_DIR / "macro.log"
# sunrise.net-7.org, verified 2026-09-21.
SERVER_IP = "216.219.87.147"
WINDOW_TITLE = "Earth & Beyond"
GAME_CLIENT = Path(os.environ.get(
    "ENB_CLIENT",
    r"C:\Program Files (x86)\EA GAMES\Earth & Beyond\release\client.exe",
))
CHAT_LOG_PATH = Path(os.environ.get(
    "ENB_CHAT_LOG",
    str(Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        / "EA GAMES/Earth & Beyond/release/chat.log"),
))
GAME_CLIENTS = (GAME_CLIENT.resolve(),
    Path(r"C:\Program Files (x86)\EA GAMES\EnB-client-2\release\client.exe").resolve())
# Edit chat_prompt.txt while Pluto runs; it is reloaded for each generation.
CHAT_PROMPT_PATH = PROJECT_DIR / "chat_prompt.txt"
CHAT_INTERACTION_LOG = PROJECT_DIR / "chat_interactions.log"
CHAT_HISTORY_TURNS = 10
CHAT_MAX_REPLY_CHARS = 180
# Empty means Codex's default model. Override without changing the login.
CHAT_CODEX_MODEL = os.environ.get("ENB_CHAT_MODEL", "")
CHAT_CODEX_TIMEOUT = 90


def net7_proxy() -> Path:
    override = os.environ.get("NET7_PROXY")
    candidates = [Path(override)] if override else [
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Net-7/bin/net7proxy.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Net-7/bin/net7proxy.exe",
        Path(r"C:\NET7\bin\net7proxy.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("Cannot find net7proxy.exe. Set NET7_PROXY to its full path.")
