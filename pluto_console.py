"""Keep the elevated console visible, including when startup fails."""
from pathlib import Path
import runpy
import sys
import traceback


def main():
    script = Path(__file__).with_name("pluto.py")
    sys.argv[0] = str(script)
    exit_code = 0
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else int(exc.code is not None)
    except KeyboardInterrupt:
        print("Pluto stopped.")
    except Exception:
        traceback.print_exc()
        exit_code = 1
    print(f"\nPluto exited with code {exit_code}.")
    try:
        input("Press Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
