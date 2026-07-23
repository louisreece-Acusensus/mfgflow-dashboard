#!/usr/bin/env python3
"""MFGFLOW dashboard server for Raspberry Pi.

Serves mfgflow_dashboard.html, proxies Smartsheet API calls (shared server key,
passphrase gate, sheet allowlist — same as the old Netlify function), and now
also proxies the internal Pick List inventory system so the dashboard can show
live picking % per asset without exposing that system's login to the browser.

Uses only the Python standard library — nothing to install.

Environment variables (set in mfgflow.env):
  PORT            port to listen on (default 8080 — NOT 5000, that's the pick list)
  SMARTSHEET_KEY  shared Smartsheet API key (optional; users can supply their own)
  DASH_PASS       site passphrase (optional; gate disabled if unset)
  PICKLIST_BASE   Pick List server base URL (default http://192.168.130.145:5000)
  PICKLIST_FIRST  Pick List login — first name
  PICKLIST_LAST   Pick List login — last name
  PICKLIST_PASS   Pick List login — password
"""
import json
import os
import re
import time
import http.cookiejar
import urllib.request
import urllib.error
import urllib.parse
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(os.environ.get('PORT', 8080))
SMARTSHEET_KEY = os.environ.get('SMARTSHEET_KEY', '')
DASH_PASS = os.environ.get('DASH_PASS', '')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PICKLIST_BASE = os.environ.get('PICKLIST_BASE', 'http://192.168.130.145:5000').rstrip('/')
PICKLIST_FIRST = os.environ.get('PICKLIST_FIRST', '')
PICKLIST_LAST = os.environ.get('PICKLIST_LAST', '')
PICKLIST_PASS = os.environ.get('PICKLIST_PASS', '')
PICKLIST_TTL = 300  # seconds — the pick list itself only auto-syncs every 15 min

# Shared key may only read these sheets (same allowlist as the Netlify function)
ALLOWED_PATHS = {
    'sheets/6805949508439940',   # Production Schedule (main)
    'sheets/2372330635349892',   # Build Type Hours lookup
}

CORS = {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'x-smartsheet-key, x-dash-pass',
}


# ── Pick List scraper ────────────────────────────────────────────────────────
# The Pick List app has no API — it's a plain server-rendered inventory system
# gated behind a first-name/last-name/password login. We log in once here (on
# the Pi, server-side) and cache the resulting session cookie, so the browser
# never sees the Pick List credentials at all.

class _TableRowParser(HTMLParser):
    """Collects every <tr>'s cell text (in order) from every <table> on a page."""
    def __init__(self):
        super().__init__()
        self.rows = []
        self._cur_row = None
        self._cur_cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self._cur_row = []
        elif tag in ('td', 'th') and self._cur_row is not None:
            self._cur_cell = []

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._cur_cell is not None:
            text = ' '.join(''.join(self._cur_cell).split())
            self._cur_row.append(text)
            self._cur_cell = None
        elif tag == 'tr' and self._cur_row is not None:
            self.rows.append(self._cur_row)
            self._cur_row = None

    def handle_data(self, data):
        if self._cur_cell is not None:
            self._cur_cell.append(data)


_PCT_RE = re.compile(r'\(([\d.]+)\s*%\)')

def _extract_picklist(html_text):
    """Table columns are: Priority, ID, Asset, Head Assembly Name, Rev, Head WO,
    Issue Date, Progress, Status, Actions. We only need Asset + the % from Progress."""
    parser = _TableRowParser()
    parser.feed(html_text)
    out = {}
    for row in parser.rows:
        if len(row) < 8:
            continue
        asset = row[2].strip()
        if not asset or asset == '-':
            continue
        m = _PCT_RE.search(' '.join(row))
        if not m:
            continue
        try:
            out[asset] = float(m.group(1))
        except ValueError:
            pass
    return out


_pick_jar = http.cookiejar.CookieJar()
_pick_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_pick_jar))
_pick_cache = {'data': {}, 'at': 0.0}


def _picklist_login():
    body = urllib.parse.urlencode({
        'first_name': PICKLIST_FIRST,
        'last_name': PICKLIST_LAST,
        'password': PICKLIST_PASS,
    }).encode()
    req = urllib.request.Request(
        f'{PICKLIST_BASE}/auth/login', data=body, method='POST',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )
    with _pick_opener.open(req, timeout=15) as r:
        r.read()


def _picklist_fetch_html():
    req = urllib.request.Request(f'{PICKLIST_BASE}/picklists/')
    with _pick_opener.open(req, timeout=15) as r:
        text = r.read().decode('utf-8', 'replace')
    if 'Existing User Login' in text:
        # No session yet, or it expired — log in and retry once.
        _picklist_login()
        with _pick_opener.open(req, timeout=15) as r:
            text = r.read().decode('utf-8', 'replace')
    return text


def get_picklist_data():
    now = time.time()
    if _pick_cache['data'] and (now - _pick_cache['at']) < PICKLIST_TTL:
        return _pick_cache['data']
    if not (PICKLIST_FIRST and PICKLIST_LAST and PICKLIST_PASS):
        return {}
    try:
        html_text = _picklist_fetch_html()
        data = _extract_picklist(html_text)
        if data:
            _pick_cache['data'] = data
            _pick_cache['at'] = now
    except Exception as e:
        print(f'Pick List fetch failed: {e}')
    return _pick_cache['data']


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
        if parsed.path == '/api/picklist':
            return self.picklist(parsed)

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

    def picklist(self, parsed):
        # Same passphrase gate as the Smartsheet shared-key path — this endpoint
        # has no "bring your own key" option, so it always needs the passphrase
        # if one is configured.
        if DASH_PASS and self.headers.get('x-dash-pass', '') != DASH_PASS:
            return self._json(401, {'message': 'Passphrase required'})
        return self._json(200, get_picklist_data())

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
    print(f'  pick list login: {"set" if (PICKLIST_FIRST and PICKLIST_PASS) else "NOT SET"} ({PICKLIST_BASE})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
