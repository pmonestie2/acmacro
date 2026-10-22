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
    game_dir: str = ""
    window_x: int | None = None
    window_y: int | None = None
    client_exe: str = ""
    gate_navigation: str = "packet"
    job_pickup: str = "packet"
    job_destinations: str = "auto"
    job_min_level: int = 0
    job_max_level: int = 0
    vendor_route: str = "raw-data"
    vendor_cycles: int = 0
    vendor_quantity: int = 22


def load_player_config(path: str | Path) -> PlayerSettings:
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
    optional = {"game_dir", "window_x", "window_y", "client_exe", "gate_navigation",
                "job_pickup", "job_destinations", "job_min_level", "job_max_level",
                "vendor_route", "vendor_cycles", "vendor_quantity"}
    allowed = set(PlayerSettings.__dataclass_fields__)
    required = allowed - optional
    missing, extra = required - set(section), set(section) - allowed
    if missing:
        raise ValueError("Missing player settings: " + ", ".join(sorted(missing)))
    if extra:
        raise ValueError("Unknown player settings: " + ", ".join(sorted(extra)))
    name = section["name"].strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise ValueError("Player name must contain only letters, numbers, underscores or hyphens")
    player_type = section["player_type"].strip().lower()
    gate_navigation = section.get("gate_navigation", "packet").strip().lower()
    if gate_navigation not in {"click", "packet"}:
        raise ValueError("gate_navigation must be click or packet")
    job_pickup = section.get('job_pickup', 'packet').strip().lower()
    if job_pickup not in {'packet', 'ui'}:
        raise ValueError('job_pickup must be packet or ui')
    vendor_route = section.get('vendor_route', 'raw-data').strip().lower()
    if vendor_route not in {'raw-data', 'chocolate', 'charon-earth'}:
        raise ValueError('vendor_route must be raw-data, chocolate or charon-earth')
    try:
        vendor_cycles = section.getint('vendor_cycles', 0)
        vendor_quantity = section.getint('vendor_quantity', 22)
    except ValueError:
        raise ValueError('vendor_cycles and vendor_quantity must be integers') from None
    if vendor_cycles < 0:
        raise ValueError('vendor_cycles must be nonnegative; zero means continuous')
    if not 1 <= vendor_quantity <= 40:
        raise ValueError('vendor_quantity must be between 1 and 40')
    job_destinations = section.get('job_destinations', 'auto').strip().lower()
    destinations = {part.strip() for part in job_destinations.split(',')}
    if (not destinations <= {'auto', 'uranus', 'charon', 'monster_pit'}
            or ('auto' in destinations and len(destinations) != 1)):
        raise ValueError('job_destinations must be auto or a comma-separated list of uranus, charon, monster_pit')
    try:
        job_min_level = section.getint('job_min_level', 0)
        job_max_level = section.getint('job_max_level', 0)
    except ValueError:
        raise ValueError('job_min_level and job_max_level must be integers') from None
    if min(job_min_level, job_max_level) < 0 or (job_max_level and job_min_level > job_max_level):
        raise ValueError('Invalid job level range; zero maximum means no limit')
    if player_type != "new" and not re.fullmatch(r"[a-z]{2}", player_type):
        raise ValueError("player_type must be a two-letter code, such as je or te, or new for the trade-only route")
    try:
        slot = section.getint("character_slot", fallback=0)
        missions = section.getint("mission_count", fallback=0)
        low_job = section.getboolean("low_job", fallback=False)
        avoid_pit = section.getboolean("avoid_pit", fallback=False)
    except ValueError:
        raise ValueError("character_slot and mission_count must be integers; low_job and avoid_pit must be true or false") from None
    if not 0 <= slot <= 4:
        raise ValueError("character_slot must be between 0 and 4")
    if not 1 <= missions <= 9:
        raise ValueError("mission_count must be between 1 and 9")
    password = section["password"]
    if not password or any(ord(c) < 32 or ord(c) > 126 for c in password):
        raise ValueError("password must be nonempty and contain printable ASCII characters")
    username = section["username"]
    if not username or any(ord(c) <= 32 or ord(c) > 126 for c in username):
        raise ValueError("username must be nonempty printable ASCII text without spaces")
    game_dir = section.get("game_dir", "").strip()
    if game_dir:
        game_dir = str((Path(path).resolve().parent / Path(game_dir).expanduser()).resolve())
    client_exe = section.get("client_exe", "").strip()
    if client_exe:
        client_exe = str((Path(path).resolve().parent / Path(client_exe).expanduser()).resolve())
        if Path(client_exe).name.lower() != "client.exe":
            raise ValueError("client_exe must point to the game's client.exe")
    try:
        window_x = section.getint("window_x") if section.get("window_x", "").strip() else None
        window_y = section.getint("window_y") if section.get("window_y", "").strip() else None
    except ValueError:
        raise ValueError("window_x and window_y must be integer screen coordinates") from None
    return PlayerSettings(name, player_type, slot, password, missions, low_job, avoid_pit, username,
                          game_dir, window_x, window_y, client_exe, gate_navigation,
                          job_pickup, job_destinations, job_min_level, job_max_level,
                          vendor_route, vendor_cycles, vendor_quantity)


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
        self.on_position = None
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
        if self.on_position:
            self.on_position(pos)


PLAYER = Player()
