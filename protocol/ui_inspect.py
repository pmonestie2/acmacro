"""Perform one explicit game UI click and save a screenshot for review."""
import argparse
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ac import AcController
from gui import bring_to_front_exact

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('x', type=int)
parser.add_argument('y', type=int)
parser.add_argument('--output', default='composition_ui.png')
args = parser.parse_args()
bring_to_front_exact('Earth & Beyond')
controller = AcController('Earth & Beyond')
controller.lclick(args.x, args.y)
time.sleep(0.7)
controller.screenshot().save(Path(__file__).resolve().parent / 'captures' / args.output)
