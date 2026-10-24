"""Exchange short messages with the Rust server agent over the private VM network."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlsplit

MAX_BODY = 16 * 1024
MAX_MESSAGES = 200


class MessageStore:
    """Keep a small append-only coordination log outside the project folder."""

    def __init__(self, path: Path) -> None:
        """Load the persistent message sequence and prepare its parent directory."""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.messages: list[dict[str, object]] = []
        if self.path.exists():
            for line in self.path.read_text(encoding='utf-8').splitlines():
                self.messages.append(json.loads(line))
            self.messages = self.messages[-MAX_MESSAGES:]

    def append(self, sender: str, message: str) -> dict[str, object]:
        """Record one message and return its assigned sequence number.

        Returns:
            dict[str, object]: Stored message with ID and UTC timestamp.
        """
        with self.lock:
            sequence = int(self.messages[-1]['id']) + 1 if self.messages else 1
            entry: dict[str, object] = {
                'id': sequence,
                'from': sender,
                'message': message,
                'at': datetime.now(timezone.utc).isoformat(),
            }
            with self.path.open('a', encoding='utf-8') as output:
                output.write(json.dumps(entry, separators=(',', ':')) + '\n')
            self.messages.append(entry)
            self.messages = self.messages[-MAX_MESSAGES:]
            return entry

    def after(self, sequence: int) -> list[dict[str, object]]:
        """Returns:
            list[dict[str, object]]: Retained messages newer than the requested ID.
        """
        with self.lock:
            return [entry for entry in self.messages if int(entry['id']) > sequence]


class CoordinationHandler(BaseHTTPRequestHandler):
    """Expose health and message exchange without serving filesystem content."""

    store: MessageStore

    def respond(self, status: int, payload: object) -> None:
        """Send a compact JSON response with an explicit no-cache policy."""
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        """Return the endpoint description, health, or messages since an ID."""
        request = urlsplit(self.path)
        if request.path == '/':
            self.respond(200, {'service': '42 and Beyond coordination',
                               'read': 'GET /messages?after=0',
                               'write': 'POST /messages {"from":"agent","message":"text"}'})
        elif request.path == '/health':
            self.respond(200, {'ok': True})
        elif request.path == '/messages':
            try:
                after = int(parse_qs(request.query).get('after', ['0'])[0])
                if after < 0:
                    raise ValueError('negative sequence')
            except ValueError:
                self.respond(400, {'error': 'after must be a nonnegative integer'})
                return
            self.respond(200, {'messages': self.store.after(after)})
        else:
            self.respond(404, {'error': 'unknown endpoint'})

    def do_POST(self) -> None:
        """Accept one bounded UTF-8 JSON message."""
        if urlsplit(self.path).path != '/messages':
            self.respond(404, {'error': 'unknown endpoint'})
            return
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > MAX_BODY:
                raise ValueError('message body must be 1–16384 bytes')
            payload = json.loads(self.rfile.read(size))
            sender = payload['from']
            message = payload['message']
            if not isinstance(sender, str) or not isinstance(message, str) or \
                    not 1 <= len(sender) <= 80 or not 1 <= len(message) <= 8000:
                raise ValueError('invalid sender or message')
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.respond(400, {'error': str(error)})
            return
        self.respond(201, self.store.append(sender, message))


def main() -> None:
    """Start a private-interface coordination server until interrupted."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='192.168.47.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--messages', type=Path,
                        default=Path.home() / 'Library/Logs/42 and Beyond/coordination.jsonl')
    args = parser.parse_args()
    CoordinationHandler.store = MessageStore(args.messages)
    server = ThreadingHTTPServer((args.bind, args.port), CoordinationHandler)
    print(f'Coordination server listening at http://{args.bind}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
