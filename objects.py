from dataclasses import dataclass
from typing import List, Optional, Tuple
import pyautogui as pag

import re

@dataclass(frozen=True)
class Point:
    dx: int
    dy: int
    value: int  # ACTool stores the highest RGB channel expected (0..255)

class ActoolObject:
    def __init__(self, name: str, points: List[Point],
                 anchor: Tuple[int, int]):
        self.name = name
        self.points = points
        self.anchor = anchor  # (x, y) from "// Test at coords X, Y", if present

    @classmethod
    def from_string(cls, definition: str) -> "ActoolObject":
        """
        Parse an ACTool Object ... End definition.
        Supports inline '|' chunks and extracts anchor from '// Test at coords X, Y'.
        """
        lines = [ln.strip() for ln in definition.splitlines() if ln.strip()]
        if not lines or not lines[0].lower().startswith("object"):
            raise ValueError("Object definition must start with 'Object <name>'")

        # Name
        # e.g. "Object gate   // Test at coords 1227, 508"
        header = lines[0]
        mname = re.match(r"object\s+([^\s/]+)", header, re.IGNORECASE)
        if not mname:
            raise ValueError("Could not parse object name")
        name = mname.group(1)

        # Anchor extraction from any comment like "// Test at coords X, Y"
        anchor = None
        mcoords = re.search(r"test\s+at\s+coords\s+(-?\d+)\s*,\s*(-?\d+)",
                            definition, re.IGNORECASE)
        if mcoords:
            anchor = (int(mcoords.group(1)), int(mcoords.group(2)))

        # Collect point lines until "End"
        points: List[Point] = []
        for ln in lines[1:]:
            if ln.lower().startswith("end"):
                break
            # Split by '|' and parse "value=dx,dy"
            for chunk in ln.split("|"):
                chunk = chunk.strip()
                if not chunk:
                    continue
                mv = re.match(r"(\d+)\s*=\s*(-?\d+)\s*,\s*(-?\d+)", chunk)
                if mv:
                    val = int(mv.group(1))
                    dx  = int(mv.group(2))
                    dy  = int(mv.group(3))
                    points.append(Point(dx=dx, dy=dy, value=val))

        if not points:
            raise ValueError("No points parsed from object")

        return cls(name=name, points=points, anchor=anchor)

    def test(self, 
             objvar: int = 7,
             scale: float = 1.0,
             at: Optional[Tuple[int, int]] = None,
             screenshot=None,
             offset: Tuple[int, int] = (0, 0),
             min_match_ratio: float = 1.0) -> bool:
        """
        Test this object on screen.
        - By default uses the parsed anchor; override with `at=(x,y)`.
        - `offset` shifts the anchor (e.g., window top-left) before testing.
        - Compares max(R,G,B) to point.value with ±objvar variance.
        - `scale` multiplies offsets for DPI/resolution scaling.
        - `min_match_ratio` (0..1]: allow partial matches (default 1.0 = all).
        """
        if at is None:
            if self.anchor is None:
                raise ValueError("No anchor available; provide `at=(x,y)`")
            at = self.anchor

        ax, ay = at
        ax += offset[0]
        ay += offset[1]
        # One screenshot (ACTool copies the screen once before comparing)
        snap = screenshot if screenshot is not None else pag.screenshot()
        px = snap.load()

        matched = 0
        total = len(self.points)

        for p in self.points:
            sx = int(round(ax + p.dx * scale))
            sy = int(round(ay + p.dy * scale))
            try:
                r, g, b = px[sx, sy]
            except Exception:
                # Off-screen or capture issue → count as mismatch
                continue
            maxrgb = max(r, g, b)
            if abs(maxrgb - p.value) <= objvar:
                matched += 1

        return matched >= int(round(total * min_match_ratio))

    def __repr__(self) -> str:
        anc = f"{self.anchor}" if self.anchor else "None"
        return f"<ActoolObject name={self.name!r} points={len(self.points)} anchor={anc}>"


MISSION_5_OBJ = ActoolObject.from_string("""
Object mission_5   // Test at coords 37, 480
  66=0,0|68=2,0|71=4,0|
  74=0,2|243=2,2|243=4,2|
  75=0,4|243=2,4|
  61=0,8|59=2,8|57=4,8|
End
""")

MISSION_6_OBJ = ActoolObject.from_string("""
Object mission_6   // Test at coords 37, 481
  77=0,0|80=2,0|
  74=0,2|243=4,2|
  71=0,4|243=2,4|
  63=4,6|
  52=4,8|
End
""")

MISSION_7_OBJ = ActoolObject.from_string("""
Object mission_7   // Test at coords 35, 479
  243=3,3|243=4,3|243=5,3|243=6,3|243=7,3|
  243=7,5|
  243=7,6|
  243=6,7|
  243=6,8|
End
""")

MISSION_4_OBJ = ActoolObject.from_string("""
object mission_4   // Test at coords 35, 481
  75=0,0|77=2,0|80=4,0|84=6,0|
  70=0,2|74=2,2|75=4,2|243=8,2|
  70=0,4|71=2,4|243=6,4|243=8,4|
end
""")

FORFEIT_OBJ = ActoolObject.from_string("""
Object Forfeit   // Test at coords 584, 264
  254=0,0|
  254=0,2|254=6,2|254=14,2|
  254=0,4|254=6,4|254=14,4|
End
""")

MISSION_SCROLL_OBJ = ActoolObject.from_string("""
Object MissionScroll   // Test at coords 319, 571
  2=0,0|2=1,0|2=2,0|
  2=0,1|2=1,1|2=2,1|
  2=0,2|2=1,2|2=2,2|
  2=0,3|2=1,3|2=2,3|
  2=0,4|2=1,4|1=2,4|
  1=0,5|1=1,5|1=2,5|
  2=0,6|1=1,6|1=2,6|
  166=0,7|166=1,7|166=2,7|
  163=0,8|163=1,8|163=2,8|
End
""")

TRADE_OPEN_OBJ = ActoolObject.from_string("""
Object tradeOpen   // Test at coords   1092, 470
  92=0,0|150=2,0|122=4,0|52=6,0|33=8,0|86=10,0|
  49=0,2|77=2,2|150=4,2|163=6,2|147=8,2|162=10,2|
  49=0,4|49=2,4|91=4,4|182=6,4|231=8,4|162=10,4|
  57=0,6|88=2,6|183=4,6|174=6,6|137=8,6|173=10,6|
End Object
""")

TRADE2_OPEN_OBJ = ActoolObject.from_string("""
Object trade2open   // Test at coords  955, 223
  254=0,2|254=4,2|254=6,2|254=12,2|254=16,2|
  254=0,4|254=20,4|
  254=2,6|254=12,6|254=16,6|
End Object
""")

TRADE_CLOSE_OBJ = ActoolObject.from_string("""
Object tradeClose   // Test at coords 1092, 472
  49=0,0|78=2,0|150=4,0|160=6,0|145=8,0|162=10,0|
  49=0,2|49=2,2|92=4,2|183=6,2|231=8,2|162=10,2|
  57=0,4|89=2,4|184=4,4|174=6,4|137=8,4|173=10,4|
End
""")

JOB_OPEN_OBJ = ActoolObject.from_string("""
Object jobOpen   // Test at coords 931, 575
  254=0,0|254=2,0|254=4,0|220=6,0|
  23=0,2|20=2,2|20=4,2|17=6,2|
  254=0,4|254=2,4|10=4,4|51=6,4|
  52=0,6|24=2,6|25=4,6|28=6,6|
  254=0,8|254=2,8|254=4,8|29=6,8|
  56=0,10|56=2,10|30=4,10|57=6,10|
End
""")

LARGE_MAP_OBJ = ActoolObject.from_string("""
Object largeMap   // Test at coords 707, 186
  91=0,0|90=2,0|90=4,0|
  227=0,2|227=2,2|227=4,2|
  190=0,4|190=2,4|190=4,4|
  93=0,6|93=2,6|92=4,6|
End
""")

IGATE_OBJ = ActoolObject.from_string("""
Object igate   // Test at coords 1230, 517
  32=0,0|31=2,0|26=4,0|71=6,0|53=8,0|30=10,0|32=12,0|
  31=0,2|31=2,2|80=4,2|88=6,2|80=8,2|61=10,2|27=12,2|
  31=0,4|60=2,4|54=4,4|58=6,4|58=8,4|48=10,4|48=12,4|
  31=0,6|32=2,6|32=4,6|72=6,6|50=8,6|32=10,6|31=12,6|
End
""")

GATE_OBJ = ActoolObject.from_string("""
Object gate   // Test at coords 1227, 508
  222=0,0|171=2,0|84=4,0|227=6,0|112=8,0|85=10,0|
  77=0,2|233=2,2|255=4,2|254=6,2|97=8,2|85=10,2|
  127=0,4|250=2,4|239=4,4|244=6,4|93=8,4|86=10,4|
  71=0,6|71=2,6|69=4,6|67=6,6|68=8,6|89=10,6|
  93=0,8|93=2,8|90=4,8|87=6,8|74=8,8|170=10,8|
End
""")

ISTATION_OBJ = ActoolObject.from_string("""
Object istation   // Test at coords 1221, 506
  18=0,0|20=2,0|32=4,0|78=6,0|89=8,0|88=10,0|
  22=0,2|19=2,2|34=4,2|70=6,2|85=8,2|73=10,2|
  20=0,4|28=2,4|68=4,4|88=6,4|60=8,4|26=10,4|
  21=0,6|49=2,6|87=4,6|76=6,6|26=8,6|32=10,6|
  22=0,8|68=2,8|88=4,8|66=6,8|32=8,8|31=10,8|
End
""")

STATION_OBJ = ActoolObject.from_string("""
Object station   // Test at coords 1224, 506
  59=0,0|156=2,0|238=4,0|252=6,0|
  69=0,2|145=2,2|221=4,2|225=6,2|
  132=0,4|223=2,4|212=4,4|87=6,4|
  193=0,6|235=2,6|88=4,6|82=6,6|
  228=0,8|221=2,8|77=4,8|90=6,8|
End
""")

WAIT_SECTOR_OBJ = ActoolObject.from_string("""
Object waitSector   // Test at coords 297, 142
  37=0,0|37=2,0|37=4,0|37=6,0|
  35=0,2|35=2,2|35=4,2|35=6,2|
  36=0,4|42=2,4|52=4,4|72=6,4|
  79=0,6|77=2,6|79=4,6|78=6,6|
  76=0,8|76=2,8|78=4,8|78=6,8|
End
""")

INVENTORY_OBJ = ActoolObject.from_string("""
Object inventory   // Test at coords 35, 685
  78=0,0|82=2,0|85=4,0|86=6,0|89=8,0|
  86=0,2|74=2,2|65=4,2|131=6,2|87=8,2|
  84=0,4|69=2,4|75=4,4|178=6,4|135=8,4|
  78=0,6|69=2,6|118=4,6|119=6,6|103=8,6|
  74=0,8|90=2,8|233=4,8|201=6,8|181=8,8|
End
""")

ISAT_OBJ = ActoolObject.from_string("""
Object isat   // Test at coords 1229, 509
  62=0,0|32=2,0|64=4,0|44=6,0|66=8,0|46=10,0|
  60=0,2|48=2,2|68=4,2|50=6,2|66=8,2|60=10,2|
  77=0,4|43=2,4|42=4,4|44=6,4|55=8,4|64=10,4|
  44=0,6|35=2,6|24=4,6|25=6,6|29=8,6|29=10,6|
End
""")

SAT_OBJ = ActoolObject.from_string("""
Object sat   // Test at coords 1225, 512
  139=0,0|190=2,0|205=4,0|147=6,0|184=8,0|
  173=0,2|96=2,2|199=4,2|96=6,2|74=8,2|
  69=0,4|187=2,4|64=4,4|73=6,4|81=8,4|
  70=0,6|168=2,6|65=4,6|92=6,6|90=8,6|
End
""")

TOW_ON_OBJ = ActoolObject.from_string("""
Object towOn   // Test at coords 319, 490
  96=0,0|96=2,0|254=6,0|97=10,0|98=18,0|98=22,0|
  84=0,2|84=2,2|254=6,2|254=12,2|254=16,2|
  65=0,4|66=2,4|66=4,4|254=6,4|66=8,4|67=14,4|254=22,4|
End
""")

WORMHOLE_OBJ = ActoolObject.from_string("""
Object wormhole   // Test at coords 394, 353
  61=0,0|254=4,0|118=6,0|135=8,0|254=10,0|
  64=0,2|220=2,2|152=4,2|64=6,2|30=8,2|254=10,2|
End
""")

JUPITER_OBJ = ActoolObject.from_string("""
Object jupiter   // Test at coords 235, 360
  254=6,2|254=16,2|254=18,2|
  254=6,4|254=14,4|254=20,4|
  254=6,6|254=14,6|254=20,6|
End
""")

CHARON_S_OBJ = ActoolObject.from_string("""
Object charonS   // Test at coords 290, 544
  254=2,2|254=4,2|254=10,2|254=16,2|
  254=2,4|254=16,4|
End
""")

CHARON_S1_OBJ = ActoolObject.from_string("""
Object charonS1   // Test at coords 258, 546
  254=12,0|254=18,0|
  254=12,2|
  254=4,4|254=12,4|
  254=6,6|254=8,6|254=12,6|254=18,6|
End
""")

PLUTO_OBJ = ActoolObject.from_string("""
Object pluto   // Test at coords 195, 361
  254=2,0|254=10,0|
  254=2,2|254=10,2|
  254=2,4|254=4,4|254=10,4|
End
""")

SATURN_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 235, 361
254=2,0|
254=4,2|254=14,2|254=24,2|
254=6,4|254=10,4|254=12,4|254=14,4|254=24,4|
254=6,6|254=14,6|254=24,6|254=28,6|
End
""")

URANUS_OBJ = ActoolObject.from_string("""
Object uranus   // Test at coords 234, 360
  254=2,0|254=8,0|
  254=2,2|254=8,2|254=18,2|254=20,2|
  254=2,4|254=8,4|
  254=2,6|254=8,6|254=16,6|
End
""")


AKERON_OBJ = ActoolObject.from_string("""
Object akeron   // Test at coords 212, 359
254=0,0|
254=2,4|254=10,4|254=16,4|254=20,4|
254=0,6|254=2,6|254=8,6|254=10,6|254=16,6|254=18,6|254=20,6|
End""") 


ABA_OBJ = ActoolObject.from_string("""
Object aba   // Test at coords 286, 362
  254=4,0|254=6,0|254=12,0|254=18,0|
  254=12,2|
  254=2,4|254=8,4|254=12,4|
End
""")

EARTH_OBJ = ActoolObject.from_string("""
Object earth   // Test at coords 238, 363
  254=4,0|254=6,0|254=20,0|
  254=12,2|254=14,2|
End
""")

EARTH_S_OBJ = ActoolObject.from_string("""
Object earthS   // Test at coords 207, 540
  254=6,4|
  254=6,6|254=14,6|254=16,6|254=22,6|254=24,6|
  254=6,8|254=18,8|254=22,8|
End
""")

NEWED_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 244, 362
254=10,0|254=12,0|254=14,0|254=22,0|254=24,0|254=28,0|254=30,0|254=32,0|
254=8,2|254=14,2|254=20,2|254=28,2|254=34,2|
254=8,4|254=14,4|254=20,4|254=28,4|254=34,4|
End""")

SOMERS_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 208, 546
254=0,0|254=2,0|254=8,0|254=10,0|254=14,0|254=16,0|254=22,0|254=24,0|
254=4,2|254=12,2|254=20,2|254=26,2|
254=4,4|254=12,4|254=20,4|
End""")

CARP_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 234, 362
254=0,0|254=2,0|254=8,0|254=10,0|254=16,0|254=24,0|254=26,0|
254=4,2|254=8,2|254=22,2|254=28,2|
254=4,4|254=8,4|254=22,4|
End""")

GLENN_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 238, 361
254=2,0|254=12,0|
254=6,2|254=8,2|254=12,2|254=16,2|254=20,2|254=24,2|254=28,2|
254=8,4|254=12,4|254=16,4|254=18,4|254=20,4|254=24,4|
254=2,6|254=8,6|254=12,6|254=16,6|254=24,6|
End""")

SWOOP_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 207, 358
254=2,4|254=12,4|254=18,4|254=20,4|254=28,4|
254=8,6|254=16,6|254=22,6|
254=4,8|254=6,8|254=10,8|254=16,8|254=22,8|
End""")

XIPE_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 218, 362
254=6,0|254=12,0|254=18,0|254=26,0|254=28,0|
254=6,2|254=12,2|254=24,2|
254=4,4|254=12,4|254=24,4|
254=2,6|254=8,6|254=12,6|254=16,6|254=18,6|254=26,6|254=28,6|
End""")

PRASAD_S_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 206, 546
254=2,0|254=10,0|254=12,0|254=16,0|254=18,0|254=26,0|
254=2,2|254=6,2|254=10,2|254=20,2|254=24,2|
254=2,4|254=10,4|254=20,4|254=30,4|
254=2,6|254=10,6|254=16,6|254=18,6|254=24,6|254=26,6|
End""") 

PRASAD_TRADE_OBJ = ActoolObject.from_string("""
Object MappedObject   // Test at coords 51, 320
201=0,0|83=2,0|240=4,0|
212=0,2|86=2,2|253=4,2|
117=0,4|127=2,4|166=4,4|
196=0,6|185=2,6|216=4,6|
205=0,8|204=2,8|221=4,8|
End""")


LOWJOB1_OBJ = ActoolObject.from_string("""
Object lowJob1   // Test at coords 367, 597 (puck)
  254=10,0|
254=0,2|254=6,2|254=10,2|
254=0,4|254=10,4|
254=0,6|254=10,6|
End
""")

LOWJOB2_OBJ = ActoolObject.from_string("""
Object lowJob2   // Test at coords 345, 597 puck trash
  254=10,2|254=18,2|
254=18,4|
254=18,6|
End
""")

LOWJOB3_OBJ = ActoolObject.from_string("""
Object lowJob3   // Test at coords 348, 597  ariel trash
  254=6,0|254=18,0|
254=18,2|
254=4,4|254=8,4|254=18,4|
254=18,6|
254=2,8|254=10,8|254=18,8|
End
""")

LOWJOB4_OBJ = ActoolObject.from_string("""
Object lowJob4   // Test at coords 365, 598 ariel sat.
  254=0,2|
254=0,4|254=8,4|254=10,4|254=12,4|
254=0,6|
End
""")

JOB1_OBJ = ActoolObject.from_string("""
Object job1   // Test at coords 352, 598
  254=2,2|254=12,2|254=16,2|
  254=2,4|254=16,4|254=20,4|
  254=4,6|254=16,6|
End
""")

JOB2_OBJ = ActoolObject.from_string("""
Object job2   // Test at coords 354, 596
  254=0,0|254=2,0|
  254=6,4|254=10,4|254=18,4|254=22,4|254=28,4|254=32,4|
  254=10,6|254=14,6|254=16,6|254=18,6|254=22,6|
End
""")

CHARON_PIT_OBJ = ActoolObject.from_string("""
Object charon_pit   // Test at coords 472, 598
254=0,0|
254=0,2|254=8,2|
254=0,4|254=2,4|254=8,4|
End 
""")

CHARON_PIT1_OBJ = ActoolObject.from_string("""
Object charon_pit1   // Test at coords 474, 598
254=10,0|
254=6,2|254=10,2|
254=0,4|254=6,4|254=10,4|254=16,4|
254=6,6|254=10,6|
End 
""")

CHARON_PIT2_OBJ = ActoolObject.from_string("""
Object charon_pit2   // Test at coords 474, 598
254=6,0|
254=2,2|254=6,2|
254=2,4|254=6,4|254=12,4|
254=2,6|254=6,6|
End
""")

CHARON_MONSTER_OBJ = ActoolObject.from_string("""
Object charonMonster   // Test at coords 1104, 555
  254=2,2|254=4,2|254=10,2|254=12,2|254=18,2|254=20,2|
  254=4,4|254=8,4|254=14,4|
  254=4,6|254=8,6|254=14,6|
End
""")

PLUTO_TRADE_OBJ = ActoolObject.from_string("""
Object tradeObj   // Test at coords 62, 332
  207=0,0|152=2,0|8=4,0|27=6,0|69=8,0|81=10,0|
  8=0,2|41=2,2|80=4,2|102=6,2|124=8,2|125=10,2|
  52=0,4|93=2,4|47=4,4|173=6,4|118=8,4|124=10,4|
  105=0,6|143=2,6|123=4,6|121=6,6|130=8,6|123=10,6|
  140=0,8|133=2,8|124=4,8|117=6,8|106=8,8|103=10,8|
End
""")


GATES = {
    "pluto": {
        "akeron": (405, 617),
        "charonS": (418, 576),
    },
    "akeron": {
        "saturn": (335, 548),
        "pluto":  (387, 530),
    },

    "jupiter": {
        "saturn": (403, 619),
    },

    "saturn": {
        "akeron": (429, 568),
        "uranus": (380, 576),
        "aba":    (379, 588),
    },

    "uranus": {
        "pluto": (386, 527),
    },

    "aba": {
        "saturn": (446, 560),
        "earth":  (281, 497),
    },

    "earth": {
        "aba":    (327, 490),
        "earthS": (362, 517),
    },
    "newed": {
        "somerS": (386, 585),
        "carp":   (493, 536),
    },
    "carp":{
        "newed": (369, 551),
        "glenn": (275, 488),
    },
    "glenn":{ 
        "carp": (438, 613),
        "swoop": (480, 507),
    },
    "swoop":{
        "glenn": (348,593),
        "xipe": (308, 617),
    },
    "xipe":{
        "swoop": (313, 565),
        "prasadS":(323, 537)
    },

}



