"""
macro_context.py

Bootstrap context for my Earth & Beyond (Net-7) Python macro,
converted from ACTool.

This file is meant to be IMPORTED in a new ChatGPT session so it
knows what I already have: state, helpers, navigation, job logic API.
"""

import time
from dataclasses import dataclass
from typing import Callable

# --------------------------------------------------------------------
# 1. GLOBAL STATE (replacement for ACTool $vars)
# --------------------------------------------------------------------

@dataclass
class MacroState:
    lowJob: int = 0          # was $lowJob
    avoidPit: int = 0        # was $avoidPit
    currentpos: str | None = None
    currentdest: str | None = None

S = MacroState()

# --------------------------------------------------------------------
# 2. STUB: ActoolObject (actual implementation lives elsewhere)
# --------------------------------------------------------------------

class ActoolObject:
    """
    Minimal stub so ChatGPT knows this exists.
    Real implementation: .from_string() sets internal pattern and anchor.
    .test(at=None) returns True/False if object is found.
    """
    def __init__(self, name: str, anchor: tuple[int, int] | None = None):
        self.name = name
        self.anchor = anchor or (0, 0)

    @classmethod
    def from_string(cls, s: str) -> "ActoolObject":
        # your real implementation parses ACTool Object..End
        # and extracts anchor from '// Test at coords X, Y'
        return cls("placeholder")

    def test(self, at: tuple[int, int] | None = None) -> bool:
        # your real implementation checks screen pixels
        return False

# --------------------------------------------------------------------
# 3. OBJECT PLACEHOLDERS (actual definitions are in your objects module)
# --------------------------------------------------------------------
# In your real code you do things like:
#   GATE_OBJ = ActoolObject.from_string("""Object gate ...""")
#   IGATE_OBJ = ...
#   STATION_OBJ = ...
#   WAIT_SECTOR_OBJ = ...
#   INVENTORY_OBJ = ...
#   JOB_OPEN_OBJ = ...
#   LOW_JOB1_OBJ, LOW_JOB2_OBJ, ...
#   JOB1_OBJ, JOB2_OBJ, ...
#   CHARON_PIT_OBJ, CHARON_PIT1_OBJ, CHARON_PIT2_OBJ, ...
#
# Here we just declare the names so ChatGPT can reference them.

GATE_OBJ = ActoolObject("gate")
IGATE_OBJ = ActoolObject("igate")
STATION_OBJ = ActoolObject("station")
ISTATION_OBJ = ActoolObject("istation")
WAIT_SECTOR_OBJ = ActoolObject("waitSector")
INVENTORY_OBJ = ActoolObject("inventory")
LARGE_MAP_OBJ = ActoolObject("largeMap")
JOB_OPEN_OBJ = ActoolObject("jobOpen")

LOW_JOB1_OBJ = ActoolObject("lowJob1")
LOW_JOB2_OBJ = ActoolObject("lowJob2")
LOW_JOB3_OBJ = ActoolObject("lowJob3")
LOW_JOB4_OBJ = ActoolObject("lowJob4")

JOB1_OBJ = ActoolObject("job1")
JOB2_OBJ = ActoolObject("job2")

CHARON_PIT_OBJ  = ActoolObject("charon_pit")
CHARON_PIT1_OBJ = ActoolObject("charon_pit1")
CHARON_PIT2_OBJ = ActoolObject("charon_pit2")

# --------------------------------------------------------------------
# 4. INPUT HELPERS (signatures + brief behavior)
# --------------------------------------------------------------------

# Sleep hook (use this in helper code so you can swap with _yield if needed)
SLEEP_FN: Callable[[float], None] = time.sleep

def _yield(seconds: float) -> None:
    """
    Sleep for `seconds`. In real code this slices the sleep and
    runs scheduled tasks (watchdog/every logic).
    """
    SLEEP_FN(seconds)

def lclick(x: int, y: int, click: int = 1) -> None:
    """
    Move mouse to (x,y) and optionally left click.
    Real implementation uses pyautogui or win32api mouse_event.
    """
    pass

def lsclick(x: int, y: int) -> None:
    """
    Shift+left click at (x,y).
    Real implementation: keybd_event(VK_SHIFT down), click, VK_SHIFT up.
    """
    pass

# --- Keyboard using win32api.keybd_event (game-compatible) ---

# In your actual code you have VK mapping + normalize_key + _send + kdown/key_press.
# Here we just keep the API and explain behavior.

def kdown(key: str, seconds: float) -> None:
    """
    Hold a key for `seconds`, then release it.
    Accepts 'A', 'a', 'left', '{LEFT}', etc.
    Implementation uses win32api.keybd_event with extended flags for arrows.
    """
    pass

def key_press(key: str, delay: float = 0.03) -> None:
    """
    Quick tap of a key (press + short delay + release).
    Uses same mapping as kdown().
    """
    pass

# --------------------------------------------------------------------
# 5. LOGGING + WINDOW FOCUS (signatures)
# --------------------------------------------------------------------

def log(msg: str) -> None:
    """
    Log a message to your macro log file and/or console.
    Real implementation uses logging + file, like:
        logging.info(msg)
    """
    print(msg)

def bring_to_front_exact(title_substr: str) -> bool:
    """
    Use win32gui.EnumWindows / SetForegroundWindow to focus the game
    window whose title contains `title_substr`.
    """
    return True

# --------------------------------------------------------------------
# 6. NAVIGATION: get_map, gate_internal, gate, transition_space
# --------------------------------------------------------------------

def get_map() -> None:
    """
    Open and normalize the sector map.
    Real implementation:
      key_press('u'); _yield(.1); key_press('m');
      if LARGE_MAP_OBJ.test(): lclick(711,187); ...
      drags map to consistent position.
    """
    pass

def has_damage() -> bool:
    """
    Check several pixel positions for 'red' to detect ship damage.
    Real implementation samples screen pixels and returns True if any
    are red enough.
    """
    return False

def transition_space() -> None:
    """
    Wait for sector load after gating:
      wait_for(WAIT_SECTOR_OBJ, max_tries=15)
      wait_for(INVENTORY_OBJ)
      _yield(7)
    """
    pass

def wait_for(obj: ActoolObject,
             max_tries: int | None = None,
             timeout: float | None = None,
             interval: float = 2.0,
             move_cursor: bool = True,
             c: bool = False,
             damage: bool = False) -> bool:
    """
    Generic wait: repeatedly call obj.test() using its anchor.
    Returns True if found, False on timeout/tries exceeded.
    """
    return False

def wait_for_either(obj_a: ActoolObject,
                    obj_b: ActoolObject,
                    timeout: float) -> bool:
    """
    Wait until either obj_a or obj_b becomes visible at their anchors.
    Returns True if found, False if timeout.
    """
    return False

def gate_internal(x: int, y: int, no_gate: bool = False) -> bool:
    """
    Core gate logic (ACTool gateInternal):
      - press ` to center
      - get_map()
      - lclick(x,y) on map
      - wait for igate/istation then gate/station
      - optionally click gate button and transition_space()
    Returns True on success, False to retry.
    """
    return False

def gate(x: int, y: int, no_gate: bool = False) -> bool:
    """
    High-level gate wrapper (ACTool gate):
      while True:
          if gate_internal(x,y,no_gate): return True
          log("retrying") ...
    """
    return False

# --------------------------------------------------------------------
# 7. SECTOR GRAPH (dict-of-dict of gate coords)
# --------------------------------------------------------------------

GATES = {
    # example structure; you already have this fully defined in your code
    # "earth": {"aba": (327, 490), "earthS": (362, 517)},
    # "aba":   {"saturn": (446, 560), "earth": (281, 497)},
    # "saturn":{"akeron": (429,568), "uranus": (380,576), "aba": (379,588)},
}

# --------------------------------------------------------------------
# 8. EARTH / CHARON ROOM MOVEMENT (signatures)
# --------------------------------------------------------------------

def sbreset(point: int) -> None:
    """
    Equivalent of ACTool sbreset:
      setmemory addr1, 0
      setmemory addr2, point
      log
      say '/starbase reset'
    """
    pass

def setpos(pos: str) -> None:
    """
    Set currentpos and posTime, and log it.
    In this context, simplified to just assign S.currentpos.
    """
    S.currentpos = pos
    log(f"we are in {pos}")

# charon room
def charon1_2(): ...
def charon2_3(): ...
def charon3_2(): ...
def charon2_1(): ...
def charon_space(): ...
def charon3_vendor(): ...

# earth room
def earth1_2(): ...
def earth2_3(): ...
def earth3_vendor(): ...
def earth2_j(): ...
def earthj_2(): ...
def earth3_2(): ...
def earth2_1(): ...
def earth1_space(): ...

# --------------------------------------------------------------------
# 9. JOB SYSTEM (signatures)
# --------------------------------------------------------------------

def count_mission() -> int:
    """
    Translated from ACTool countMission:
    returns number of missions found (1..7).
    """
    return 0

def sort_job(): ...
def open_job(): ...
def closeJob(): ...
def walkJob(): ...

def is_good_job() -> bool: ...
def is_good_low_job() -> bool: ...
def is_pit_job() -> bool: ...

def pick_batch_job(): ...
def pick_batch_low_job(): ...

def pick_job(): ...
def close_map(): ...

# --------------------------------------------------------------------
# 10. EARTH FLOW WRAPPER (allEarth)
# --------------------------------------------------------------------

def all_earth():
    """
    Translated ACTool allEarth:
      - delays
      - earth1_2, earth2_3, earth3_vendor
      - log SELLALL IN EARTH
      - earth3_2
      - optionally earth2_j + job loop
      - earth2_1, earth1_space
      - currentdest = pluto
    """
    _yield(7)
    close_map()
    _yield(5)

    earth1_2()
    earth2_3()
    earth3_vendor()
    log("SELLALL IN EARTH")
    earth3_2()

    if S.lowJob >= 0:
        earth2_j()
        walkJob()
        pick_job()
        closeJob()
        earthj_2()

    earth2_1()
    earth1_space()

    S.currentdest = "pluto"
