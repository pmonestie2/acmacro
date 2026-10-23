"""Local HTTP application with explicit protocol connection ownership."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
import threading
from typing import TypedDict

from enb_protocol.capture import server_datagrams
from enb_protocol.connection import GameConnection
from enb_protocol.login import Credentials
from enb_protocol.world import World
from .sessions import SessionController

STATIC = Path(__file__).resolve().parent / 'static'


class NavigationRecord(TypedDict):
    id: int
    name: str
    kind: str
    position: tuple[float, float, float]
    visited: bool
    navigation_type: int


class AppState:
    def __init__(self, game_server: str, capture: Path | None, capture_server: str,
                 certificate_sha256: str | None = None) -> None:
        self.game_server = game_server
        self.world = World()
        self.rejected_datagrams = 0
        self.capture_time: str | None = None
        self.probe_lock = threading.Lock()
        self.controller = SessionController(game_server, certificate_sha256)
        if capture is not None:
            for payload in server_datagrams(capture, capture_server):
                try:
                    self.world.apply_datagram(payload)
                except ValueError:
                    self.rejected_datagrams += 1
            self.capture_time = datetime.fromtimestamp(capture.stat().st_mtime, timezone.utc).isoformat()

    def snapshot(self) -> dict[str, object]:
        session = self.controller.snapshot()
        use_session = session.name is not None
        items = session.objects if use_session else self.world.navigable()
        objects: list[NavigationRecord] = [
            {'id': item.object_id, 'name': item.name, 'kind': item.kind,
             'position': (item.position[0] / 1000, item.position[1] / 1000, item.position[2] / 1000),
             'visited': item.visited, 'navigation_type': item.navigation_type}
            for item in items
        ]
        return {'source': ('live' if session.status == 'connected' else 'session') if use_session else ('capture' if self.capture_time is not None else 'empty'),
                'sector': session.sector if use_session else self.world.sector, 'objects': objects, 'game_server': self.game_server,
                'captured_at': self.capture_time, 'rejected_datagrams': self.rejected_datagrams,
                'character_connected': session.status == 'connected', 'character': session.name,
                'session_status': session.status, 'session_error': session.error,
                'characters': [{'slot': item.slot, 'name': item.name, 'location': item.location}
                               for item in self.controller.character_list()],
                'navigation_status': session.navigation,
                'packets': session.packets, 'frames': session.frames,
                'packet_age': session.age,
                'position': [v / 1000 for v in session.position] if session.position else None}

    def probe(self) -> dict[str, object]:
        if not self.probe_lock.acquire(blocking=False):
            raise RuntimeError('A connection test is already running')
        try:
            connection = GameConnection.connect(self.game_server)
            try:
                status = connection.check_version()
                if status != 0:
                    raise ValueError(f'Game version rejected with status {status}')
                return {'ok': True, 'message': 'Direct RSA/RC4 connection verified; version 42.0 accepted. Character login has not been sent.'}
            finally:
                connection.close()
        finally:
            self.probe_lock.release()


class WebServer(ThreadingHTTPServer):
    daemon_threads = False

    def __init__(self, address: tuple[str, int], state: AppState) -> None:
        self.state = state
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: WebServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(30)

    def send_content(self, status: int, content_type: str, content: bytes) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def send_json(self, status: int, value: dict[str, object]) -> None:
        self.send_content(status, 'application/json; charset=utf-8', json.dumps(value).encode('utf-8'))

    def do_GET(self) -> None:
        if self.path == '/api/state':
            self.send_json(200, self.server.state.snapshot())
            return
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        asset = assets.get(self.path)
        if asset is None:
            self.send_json(404, {'error': 'Not found'})
            return
        name, content_type = asset
        self.send_content(200, content_type, (STATIC / name).read_bytes())

    def do_POST(self) -> None:
        if self.path not in ('/api/probe', '/api/authenticate', '/api/login', '/api/logout', '/api/navigate'):
            self.send_json(404, {'error': 'Not found'})
            return
        if self.headers.get('X-ENB-Request') != '1':
            self.send_json(403, {'error': 'Same-origin application request required'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            self.send_json(400, {'error': 'Invalid request length'})
            return
        if not 0 <= length <= 4096 or (self.path in ('/api/probe', '/api/logout') and length != 0):
            self.send_json(400, {'error': 'Invalid request body length'})
            return
        try:
            if self.path in ('/api/authenticate', '/api/login', '/api/navigate'):
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON request object')
                if self.path == '/api/authenticate':
                    username, password = body.get('username'), body.get('password')
                    if not isinstance(username, str) or not isinstance(password, str) or not 1 <= len(username) <= 128 or not 1 <= len(password) <= 128:
                        raise ValueError('Enter an account name and password')
                    self.server.state.controller.authenticate(Credentials(username, password))
                elif self.path == '/api/login':
                    slot = body.get('slot')
                    if type(slot) is not int or not 0 <= slot <= 4:
                        raise ValueError('Choose a character slot from 0 through 4')
                    self.server.state.controller.login(slot)
                else:
                    target, sector, mode = body.get('target'), body.get('sector'), body.get('mode')
                    if type(target) is not int or not isinstance(sector, str) or not isinstance(mode, str):
                        raise ValueError('Choose a navigation object on the current sector map')
                    self.server.state.controller.navigate(sector, target, mode)
                self.send_json(200, self.server.state.snapshot())
            elif self.path == '/api/logout':
                self.server.state.controller.logout()
                self.send_json(200, self.server.state.snapshot())
            else:
                self.send_json(200, self.server.state.probe())
        except RuntimeError as error:
            self.send_json(409, {'error': str(error)})
        except (OSError, ValueError, EOFError) as error:
            self.send_json(502, {'error': str(error)})


def serve(host: str, port: int, game_server: str, capture: Path | None, capture_server: str,
          certificate_sha256: str | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    state = AppState(game_server, capture, capture_server, certificate_sha256)
    with WebServer((host, port), state) as server:
        print(f'ENB web client: http://{host}:{server.server_port}', flush=True)
        print('Sign in with account credentials in the browser, then choose a character.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            state.controller.logout()
