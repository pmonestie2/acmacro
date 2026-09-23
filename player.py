"""Player configuration and runtime state, shared by macro entry points."""
import configparser
from dataclasses import dataclass, field
from pathlib import Path
import re
import time


@dataclass(frozen=True)
class PlayerSettings:
    name: str
    player_type: str
    character_slot: int
    password: str = field(repr=False)
    mission_count: int
    low_job: bool
    avoid_pit: bool
    username: str


def load_player_config(path):
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with Path(path).open(encoding="utf-8-sig") as stream:
            parser.read_file(stream)
    except configparser.Error:
        # Parser errors can contain source lines, including the password.
        raise ValueError("Invalid player config format; use a [player] section with key = value lines") from None
    if parser.sections() != ["player"] or parser.defaults():
        raise ValueError("Player config must contain exactly one [player] section and no DEFAULT section")
    section = parser["player"]
    required = set(PlayerSettings.__dataclass_fields__)
    missing, extra = required - set(section), set(section) - required
    if missing:
        raise ValueError("Missing player settings: " + ", ".join(sorted(missing)))
    if extra:
        raise ValueError("Unknown player settings: " + ", ".join(sorted(extra)))
    name = section["name"].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Player name must contain only letters, numbers, underscores or hyphens")
    player_type = section["player_type"].strip().lower()
    if not re.fullmatch(r"[a-z]{2}", player_type):
        raise ValueError("player_type must be a two-letter code, such as je or te")
    try:
        slot = section.getint("character_slot")
        missions = section.getint("mission_count")
        low_job = section.getboolean("low_job")
        avoid_pit = section.getboolean("avoid_pit")
    except ValueError:
        raise ValueError("character_slot and mission_count must be integers; low_job and avoid_pit must be true or false") from None
    if not 0 <= slot <= 4:
        raise ValueError("character_slot must be between 0 and 4")
    if not 1 <= missions <= 7:
        raise ValueError("mission_count must be between 1 and 7")
    password = section["password"]
    if not password or any(ord(c) < 32 or ord(c) > 126 for c in password):
        raise ValueError("password must be nonempty and contain printable ASCII characters")
    username = section["username"]
    if not username or any(ord(c) <= 32 or ord(c) > 126 for c in username):
        raise ValueError("username must be nonempty printable ASCII text without spaces")
    return PlayerSettings(name, player_type, slot, password, missions, low_job, avoid_pit, username)


class Player:
    def __init__(self):
        self.toon_name = ""
        self.type_ = ""
        self.toon_position = 0
        self.password = ""
        self.username = ""
        self.mission_count = 0
        self._reset_route()

    def _reset_route(self):
        # Runtime route state is detected by the macro, not supplied by the user.
        self.current_position = "earth"
        self.previous_position = "earthP"
        self.current_dest = "earth"
        self.relogin_time = self.set_time = time.time()
        self.progress_version = 0

    def configure(self, settings):
        self.toon_name = settings.name
        self.type_ = settings.player_type
        self.toon_position = settings.character_slot
        self.password = settings.password
        self.username = settings.username
        self.mission_count = settings.mission_count
        self._reset_route()

    def set_pos(self, pos, *, progress=True):
        if not pos:
            return
        self.previous_position = self.current_position
        self.current_position = pos
        if self.previous_position != pos:
            self.set_time = time.time()
            if progress:
                self.progress_version += 1


PLAYER = Player()
