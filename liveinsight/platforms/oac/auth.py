"""The user's OAuth token for Oracle Analytics Cloud.

The token belongs to the user (not to a technical account): MCP queries run with their permissions.
Mechanism taken from the official oac-mcp-connect 1.4 connector (see docs/SPIKE-MCP.md):

- login:   the browser opens <OAC>/ui/dv/ui/api/v1/tokens/token?redirect_uri=<callback>;
           OAC POSTs a form-encoded body to the callback with access_token, refresh_token, expires_in.
- refresh: POST <OAC>/api/dv/api/v1/tokens/token/refresh
           Authorization: Bearer <access token still valid>, Content-Type: text/plain,
           body = refresh token. JSON answer: accessToken, refreshToken, expiresIn.
           It must happen BEFORE expiry: the refresh authenticates with the current access token.
- file:    the same format as "Download token" in the OAC profile: {accessToken, refreshToken, expiresIn}.
"""
import base64
import json
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

REFRESH_MARGIN = 300    # seconds before expiry when the token is renewed


class TokenExpired(RuntimeError):
    """The access token expired: it cannot be refreshed any more, a new sign-in is needed."""


def login_url(oac_url, redirect_uri):
    return f"{oac_url.rstrip('/')}/ui/dv/ui/api/v1/tokens/token?{urlencode({'redirect_uri': redirect_uri})}"


def jwt_claims(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class OacToken:
    def __init__(self, oac_url, access_token, refresh_token, path=None):
        self.oac_url = oac_url.rstrip("/")
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.path = Path(path) if path else None
        self._lock = threading.RLock()      # requests and background renewal: one refresh at a time

    @classmethod
    def from_file(cls, oac_url, path):
        t = json.loads(Path(path).read_text())
        return cls(oac_url, t["accessToken"], t["refreshToken"], path)

    @property
    def expires_at(self):
        return jwt_claims(self.access_token)["exp"]

    def seconds_left(self):
        return self.expires_at - time.time()

    def refresh(self):
        with self._lock:
            if self.seconds_left() <= 0:
                raise TokenExpired("access token expired: it cannot be refreshed any more, sign in again")
            r = httpx.post(f"{self.oac_url}/api/dv/api/v1/tokens/token/refresh", content=self.refresh_token,
                           headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "text/plain"},
                           timeout=30)
            r.raise_for_status()
            t = r.json()
            self.access_token, self.refresh_token = t["accessToken"], t["refreshToken"]
            if self.path:
                self.path.write_text(json.dumps({"accessToken": self.access_token, "refreshToken": self.refresh_token,
                                                 "expiresIn": t["expiresIn"]}))

    def current(self):
        """A valid access token, renewed when expiry is close."""
        with self._lock:
            if self.path and self.path.exists():        # another process (keepalive, login) may have renewed it
                t = json.loads(self.path.read_text())
                self.access_token, self.refresh_token = t["accessToken"], t["refreshToken"]
            if self.seconds_left() < REFRESH_MARGIN:
                self.refresh()
            return self.access_token


def keepalive():
    """Development: keeps .secrets/tokens.json alive, renewing it before expiry.

    Usage:  uv run python -m liveinsight.platforms.oac.auth
    Reads the file again at every round, so a token downloaded by hand in the meantime is adopted.
    """
    import os
    from dotenv import load_dotenv
    root = Path(__file__).resolve().parents[3]
    load_dotenv(root / ".env")
    path = root / os.environ.get("OAC_TOKENS", ".secrets/tokens.json")
    while True:
        token = OacToken.from_file(os.environ["OAC_URL"], path)
        left = token.seconds_left()
        if left <= 0:
            print(time.strftime("%H:%M"), "token expired: sign in again", flush=True)
        elif left < 2 * REFRESH_MARGIN:
            token.refresh()
            print(time.strftime("%H:%M"), "token renewed", flush=True)
        time.sleep(60)


if __name__ == "__main__":
    keepalive()
