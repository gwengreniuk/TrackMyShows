"""Serve the phone app locally for testing: python tools/serve_phone.py [port] [folder]

Unlike `python -m http.server`, this sends correct MIME types for JS modules on Windows.
Add http://localhost:<port> to your Google client's Authorized JavaScript origins to test sign-in.
"""
import functools
import http.server
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
                      '.webmanifest': 'application/manifest+json', '.png': 'image/png', '.html': 'text/html'}

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    folder = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, 'phone')
    print('Serving %s at http://localhost:%d/' % (folder, port))
    http.server.ThreadingHTTPServer(('', port), functools.partial(Handler, directory=folder)).serve_forever()
