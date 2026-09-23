"""Manually test the Space input used during login."""
import time

from config import WINDOW_TITLE
from gui import bring_to_front_exact, key_press


def main():
    print("Leave the game at the screen where Space should work.", flush=True)
    print("In 5 seconds, this test will focus the game and press Space once.", flush=True)
    time.sleep(5)
    if not bring_to_front_exact(WINDOW_TITLE):
        raise RuntimeError("Cannot focus Earth & Beyond. No key was sent.")
    key_press("space", 0.2)
    print("Sent PyAutoGUI Space with a 0.2-second hold. Check whether the game responded.", flush=True)


if __name__ == "__main__":
    main()
