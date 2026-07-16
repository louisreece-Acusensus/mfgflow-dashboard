#!/usr/bin/env python3
"""MFGFLOW dashboard server for Raspberry Pi.

Serves mfgflow_dashboard.html and proxies Smartsheet API calls, replicating
the Netlify function exactly: shared server key, passphrase gate, and a
sheet allowlist so the shared key can only read the two dashboard sheets.

Uses only the Python standard library — nothing to install.

Environment variables (set in mfgflow.env):
  PORT            port to listen on (default 8080 — NOT 5000, that's the pick list)
  SMARTSHEET_KEY  shared Smartsheet API key (optional; users can supply their own)
  DASH_PASS       site passphrase (optional; gate disabled if unset)
"""
import json
import os
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get('PORT', 8080))
SMARTSHEET_KEY = os.environ.get('SMARTSHEET_KEY', '')
DASH_PASS = os.environ.get('DASH_PASS', '')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Shared key may only read these sheets (same allowlist as the Netlify function)
ALLOWED_PATHS = {
    'sheets/6805949508439940',   # Production Schedule (main)
    'sheets/2372330635349892',   # Build Type Hours lookup
}

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'x-smartsheet-key, x-dash-pass',
}


class Handler(BaseHTTPRequestHandler):

    def _send(self, code, body, ctype='application/json'):
        try:
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            for k, v in CORS.items():
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client closed the connection before we finished responding

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/smartsheet':
            return self.proxy(parsed)

        # Static files (dashboard)
        path = parsed.path
        if path in ('/', '/index.html'):
            path = '/mfgflow_dashboard.html'
        fp = os.path.normpath(os.path.join(BASE_DIR, path.lstrip('/')))
        if not fp.startswith(BASE_DIR) or not os.path.isfile(fp):
            return self._json(404, {'message': 'Not found'})
        ctype = 'text/html; charset=utf-8' if fp.endswith('.html') else 'application/octet-stream'
        with open(fp, 'rb') as f:
            body = f.read()
        try:
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # client closed the connection before we finished responding

    def proxy(self, parsed):
        qs = parse_qs(parsed.query)
        path = (qs.get('path') or [''])[0]
        user_key = self.headers.get('x-smartsheet-key', '')
        api_key = user_key or SMARTSHEET_KEY

        if not api_key:
            return self._json(400, {'message': 'No API key: send x-smartsheet-key header or set SMARTSHEET_KEY env var'})
        if not path:
            return self._json(400, {'message': 'Missing path query parameter'})
        # Keyless (shared-key) access requires the passphrase, if one is set
        if not user_key and DASH_PASS and self.headers.get('x-dash-pass', '') != DASH_PASS:
            return self._json(401, {'message': 'Passphrase required'})
        # Shared key restricted to the dashboard's own sheets
        if not user_key and path not in ALLOWED_PATHS:
            return self._json(403, {'message': 'Path not allowed with server key'})

        req = urllib.request.Request(
            f'https://api.smartsheet.com/2.0/{path}',
            headers={'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                code = r.status
        except urllib.error.HTTPError as e:
            body = e.read()
            code = e.code
        except Exception as e:
            return self._json(502, {'message': f'Proxy error: {e}'})
        self._send(code, body)

    def log_message(self, fmt, *args):
        pass  # keep the journal quiet

    def handle_error(self, request, client_address, exc_info=None):
        # Client disconnecting mid-response (BrokenPipeError/ConnectionResetError)
        # is normal on a dashboard people click away from — don't spam the log.
        import sys
        exc = exc_info[1] if exc_info else sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


if __name__ == '__main__':
    print(f'MFGFLOW dashboard: http://0.0.0.0:{PORT}/')
    print(f'  shared key: {"set" if SMARTSHEET_KEY else "NOT SET"}   passphrase gate: {"ON" if DASH_PASS else "off"}')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
