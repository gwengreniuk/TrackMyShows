"""Phone remote control over the home network (like Kore, but usable from the web app).

A small HTTPS server inside the add-on's service. The phone app is a secure web page, so it
can only talk to an https:// address that answers the browser's CORS check, which Kodi's own
web server doesn't. Requests must carry the box's secret token, which the phone reads from
the box's (private) Google Drive heartbeat file; Kodi's web server password stays on.
"""
import hmac
import json
import os
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8443
ALLOWED_ORIGINS = ('https://gwengreniuk.github.io', 'http://localhost:8767', 'http://localhost:8000')
MAX_BODY = 64 * 1024

OK_PAGE = b"""<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>TrackMyShows remote</title><body style="font:16px system-ui;padding:24px;text-align:center">
<h2>&#10003; This TV is connected</h2><p>You can close this tab and go back to TrackMyShows.</p></body>"""


class Bridge:
    def __init__(self, certfile, keyfile, token, rpc, log=lambda m: None, port=PORT):
        """rpc: function(request_json_str) -> response_json_str (xbmc.executeJSONRPC)."""
        self.token = token
        self.rpc = rpc
        self.log = log
        self.port = port
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # keep kodi.log quiet
                pass

            def _cors(self):
                origin = self.headers.get('Origin', '')
                if origin in ALLOWED_ORIGINS:
                    self.send_header('Access-Control-Allow-Origin', origin)
                    self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-TMS-Token')
                self.send_header('Access-Control-Allow-Private-Network', 'true')  # Chrome: public site -> LAN
                self.send_header('Access-Control-Max-Age', '600')

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self):  # opened once in the phone's browser to accept the certificate
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(OK_PAGE)

            def do_POST(self):
                status, body = bridge.handle(self.path, self.headers.get('X-TMS-Token', ''),
                                             self.rfile.read(min(int(self.headers.get('Content-Length') or 0), MAX_BODY)))
                self.send_response(status)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(('0.0.0.0', port), Handler)
        self.httpd.daemon_threads = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        self.httpd.socket = ctx.wrap_socket(self.httpd.socket, server_side=True)
        self.thread = threading.Thread(target=self.httpd.serve_forever, name='tms-bridge', daemon=True)

    def handle(self, path, token, raw):
        """Pure request handling (tested without sockets): returns (status, body bytes)."""
        if path.rstrip('/') != '/rpc':
            return 404, b'{"error":"not found"}'
        if not self.token or not hmac.compare_digest(str(token), str(self.token)):
            return 403, b'{"error":"bad token"}'
        try:
            request = json.loads(raw.decode('utf-8'))
        except ValueError:
            return 400, b'{"error":"bad json"}'
        if not isinstance(request, (dict, list)):
            return 400, b'{"error":"bad request"}'
        response = self.rpc(json.dumps(request))
        return 200, (response or 'null').encode('utf-8')

    def start(self):
        self.thread.start()
        self.log('remote bridge listening on port %d' % self.port)

    def stop(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


def find_cert(*folders):
    """First folder containing bridge.pem + bridge.key."""
    for folder in folders:
        pem, key = os.path.join(folder, 'bridge.pem'), os.path.join(folder, 'bridge.key')
        if os.path.isfile(pem) and os.path.isfile(key):
            return pem, key
    return None, None
