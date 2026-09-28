"""OAC sign-in from the browser, for development: saves the user's token in .secrets/tokens.json.

Same flow as the official connector: it opens <OAC>/ui/dv/ui/api/v1/tokens/token?redirect_uri=<callback>, the user
signs in with their SSO, OAC POSTs a form-encoded body to the callback with access_token, refresh_token,
expires_in. The callback is a temporary local server on 127.0.0.1.

The redirect_uri has the same shape as the connector's (oac-mcp-connect 1.4, constants DEFAULT_REDIRECT_HOST,
DEFAULT_REDIRECT_PORT_RANGE and the callback path): http://127.0.0.1:<port 3000-9000>/oac-mcp-connect/callback/<nonce>.
With a different redirect_uri (localhost:5173/api/auth/callback) OAC answered "Your Analytics service is currently
paused", with the instance running (23/9): OAC probably accepts only callbacks of this shape.

Usage:  uv run python -m liveinsight.platforms.oac.login
"""
import json
import os
import random
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from dotenv import load_dotenv

from liveinsight.platforms.oac.auth import login_url

ROOT = Path(__file__).resolve().parents[3]
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORTS = range(3000, 9001)
CALLBACK_PREFIX = "/oac-mcp-connect/callback/"
PAGE = "<!doctype html><meta charset=utf-8><title>Live Insight</title><p style='font:16px system-ui;margin:3em'>{}</p>"


def bind_callback_server(handler, port=None):
    """The callback server on 127.0.0.1, on a free port of the connector's range."""
    ports = [port] if port else random.sample(CALLBACK_PORTS, 100)
    for p in ports:
        try:
            return HTTPServer((CALLBACK_HOST, p), handler)
        except OSError:                       # port taken: try the next one
            continue
    raise RuntimeError(f"no free port for the callback between {ports[0]} and {ports[-1]}")


def wait_for_tokens(oac_url, port=None, open_browser=True):
    """Starts the local callback, opens the sign-in and returns {accessToken, refreshToken, expiresIn}."""
    received = {}
    callback_path = CALLBACK_PREFIX + secrets.token_hex(32)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path.split("?")[0] != callback_path:
                return self._reply(404, "Unknown path.")
            form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
            try:
                received.update(accessToken=form["access_token"][0], refreshToken=form["refresh_token"][0],
                                expiresIn=int(form["expires_in"][0]))
            except (KeyError, ValueError):
                return self._reply(400, "OAC answered without a token.")
            self._reply(200, "Signed in. You can close this tab.")

        def do_GET(self):
            self._reply(405, "Waiting for OAC…")

        def _reply(self, code, text):
            body = PAGE.format(text).encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):          # no tokens in the logs
            pass

    server = bind_callback_server(Handler, port)
    url = login_url(oac_url, f"http://{CALLBACK_HOST}:{server.server_port}{callback_path}")
    print(f"Open this address and sign in to OAC (it opens by itself):\n  {url}", flush=True)
    if open_browser:
        webbrowser.open(url)
    server.timeout = 600
    while not received:
        server.handle_request()
    server.server_close()
    return received


def main():
    load_dotenv(ROOT / ".env")
    path = ROOT / os.environ.get("OAC_TOKENS", ".secrets/tokens.json")
    tokens = wait_for_tokens(os.environ["OAC_URL"])
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(tokens))
    path.chmod(0o600)
    print(f"Token saved in {path.relative_to(ROOT)} (expires in {tokens['expiresIn'] // 60} minutes)")


if __name__ == "__main__":
    sys.exit(main())
