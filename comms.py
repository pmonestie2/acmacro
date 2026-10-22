"""Local HTTP messages between macros; handlers never perform game input."""
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import uuid
from urllib.request import Request, build_opener, ProxyHandler


def post(port, path, payload, token, timeout=0.5):
    request = Request(f"http://127.0.0.1:{port}{path}",
                      data=json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", "X-Macro-Token": token}, method="POST")
    with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
        return json.load(response)


class PeerServer:
    def __init__(self, on_logoff, on_focus, status, port=0):
        self.token = uuid.uuid4().hex
        self.on_logoff, self.on_focus, self.status = on_logoff, on_focus, status
        self.seen = OrderedDict()
        self.lock = threading.Lock()
        peer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, data):
                body = json.dumps(data).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("X-Macro-Token") != peer.token:
                    return self.reply(403, {"error": "unauthorized"})
                self.reply(200 if self.path == "/status" else 404,
                           peer.status() if self.path == "/status" else {"error": "unknown endpoint"})

            def do_POST(self):
                if self.headers.get("X-Macro-Token") != peer.token:
                    return self.reply(403, {"error": "unauthorized"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError()
                    message = json.loads(self.rfile.read(length))
                    request_id = message.get("id")
                    if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
                        raise ValueError()
                except (ValueError, AttributeError):
                    return self.reply(400, {"error": "invalid message"})
                if self.path == "/focus/request":
                    peer.on_focus()
                    return self.reply(202, {"id": request_id, "status": "queued"})
                if self.path != "/message" or message.get("command") != "logoff":
                    return self.reply(400, {"error": "unknown command"})
                with peer.lock:
                    duplicate = request_id in peer.seen
                    if not duplicate:
                        peer.on_logoff()
                        peer.seen[request_id] = True
                        if len(peer.seen) > 256:
                            peer.seen.popitem(last=False)
                self.reply(202, {"id": request_id, "status": "queued", "duplicate": duplicate})

            def setup(self):
                super().setup()
                self.connection.settimeout(1)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.1}, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
