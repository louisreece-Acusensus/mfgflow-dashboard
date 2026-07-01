"""
MFGFLOW Dashboard – local server
Double-click start_dashboard.bat to run this.
Keep this window open while using the dashboard.
"""
import http.server, urllib.request, urllib.error
import json, threading, webbrowser, os
from urllib.parse import urlparse, parse_qs

PORT   = 8080
FOLDER = os.path.dirname(os.path.abspath(__file__))
URL    = f"http://localhost:{PORT}/mfgflow_dashboard.html"


class Handler(http.server.SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FOLDER, **kwargs)

    # ── proxy endpoint ────────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "x-smartsheet-key")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/api/smartsheet"):
            params  = parse_qs(urlparse(self.path).query)
            path    = params.get("path", [""])[0]
            api_key = self.headers.get("x-smartsheet-key", "")

            if not path or not api_key:
                self._json(400, {"message": "Missing path or API key"})
                return

            try:
                req = urllib.request.Request(
                    f"https://api.smartsheet.com/2.0/{path}",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    self._json(resp.status, resp.read(), raw=True)

            except urllib.error.HTTPError as e:
                self._json(e.code, e.read(), raw=True)
            except Exception as e:
                self._json(502, {"message": str(e)})
        else:
            super().do_GET()

    # ── helpers ───────────────────────────────────────────────────────────────
    def _json(self, code, body, raw=False):
        data = body if raw else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type",  "application/json")
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Headers", "x-smartsheet-key")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass  # keep console tidy


# ── main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("  MFGFLOW Dashboard")
    print(f"  Opening: {URL}")
    print("  Keep this window open while using the dashboard.")
    print("  Press Ctrl+C (or close this window) to stop.")
    print("=" * 50)

    threading.Timer(1.2, lambda: webbrowser.open(URL)).start()

    try:
        with http.server.HTTPServer(("", PORT), Handler) as httpd:
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    except OSError as e:
        if "10048" in str(e) or "Address already in use" in str(e):
            print(f"\nPort {PORT} is already in use.")
            print(f"Try opening {URL} in your browser — it may already be running.")
            input("Press Enter to exit.")
        else:
            raise
