"""Send one HTTP logoff command to a running macro, then exit."""
import argparse
import uuid
from focus import SharedFocus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("toon", help="Character running the receiving macro")
    args = parser.parse_args()
    coordination = SharedFocus("message-sender", "MessageSender", claim_game=False)
    try:
        result = coordination.send_logoff(args.toon, uuid.uuid4().hex)
        print(f"Logoff for {args.toon}: {result['status']}")
    finally:
        coordination.close()


if __name__ == "__main__":
    main()
