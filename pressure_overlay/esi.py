import base64
import email.utils
import hashlib
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Lock, Thread

from . import windows

METADATA_URL = "https://login.eveonline.com/.well-known/oauth-authorization-server"
ESI_URL = "https://esi.evetech.net"
COMPATIBILITY_DATE = "2025-12-01"
# Public: it names the app on EVE's login page, not any character.
CLIENT_ID = "e1cdf96c7a914d1286191fd382ca3cdc"
CALLBACK_PORT = 8765
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}/callback"
SCOPES = ("esi-location.read_location.v1", "esi-location.read_ship_type.v1")
ISSUERS = {"https://login.eveonline.com", "https://login.eveonline.com/", "login.eveonline.com"}
USER_AGENT = "EVEPressureOverlay/0.1"
LOGIN_SECONDS = 300
POLL_SECONDS = 5
RETRY_SECONDS = 30
# ESI's per-route-group allowance, such as "150/15m".
RATE_LIMIT = re.compile(r"(\d+)/(\d+)([smh])")
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600}


class EsiError(Exception):
    pass


@dataclass
class Character:
    id: int
    name: str
    refresh_token: str
    access_token: str = ""
    expires: float = 0.0


@dataclass(frozen=True)
class Status:
    pilot: str = ""
    docked: bool | None = None
    ship_id: int | None = None
    ship: str = ""
    ship_group: str = ""
    error: str = ""


def b64encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64decode(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def fetch(url, *, form=None, token=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if url.startswith(ESI_URL):
        headers["X-Compatibility-Date"] = COMPATIBILITY_DATE
    with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=15) as response:
        return json.load(response), response.headers


_metadata = {}


def rate_spacing(*responses):
    """Seconds between polls that use at most half of each rate limit group's allowance."""
    used, windows = {}, {}
    for headers in responses:
        limit = RATE_LIMIT.fullmatch(headers.get("X-Ratelimit-Limit") or "")
        if not limit:
            continue
        group = headers.get("X-Ratelimit-Group", "")
        try:
            cost = int(headers.get("X-Ratelimit-Used") or 2)
        except ValueError:
            cost = 2
        used[group] = used.get(group, 0) + cost
        windows[group] = int(limit[2]) * UNIT_SECONDS[limit[3]] / max(1, int(limit[1]))
    return max((2 * used[group] * windows[group] for group in used), default=0.0)


def retry_after(headers, default):
    """How long ESI asked us to wait after a 429 rate limit or a 420 error limit."""
    for name in ("Retry-After", "X-ESI-Error-Limit-Reset"):
        try:
            return max(default, float(headers.get(name)))
        except (TypeError, ValueError):
            continue
    return default


def endpoint(name):
    if not _metadata:
        _metadata.update(fetch(METADATA_URL)[0])
    return _metadata[name]


def character_from_token(data, client_id, refresh_token=""):
    # The token comes straight from the SSO over HTTPS, so only its claims are checked.
    try:
        token = data["access_token"]
        claims = json.loads(b64decode(token.split(".")[1]))
        audience = claims.get("aud")
        subject = str(claims.get("sub", ""))
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as error:
        raise EsiError("EVE login returned an unreadable token.") from error
    audience = [audience] if isinstance(audience, str) else audience or []
    if (
        claims.get("iss") not in ISSUERS
        or client_id not in audience
        or "EVE Online" not in audience
        or not subject.startswith("CHARACTER:EVE:")
    ):
        raise EsiError("EVE login returned a token for a different app.")
    return Character(
        int(subject.rsplit(":", 1)[1]),
        str(claims.get("name", "")),
        data.get("refresh_token") or refresh_token,
        token,
        float(claims.get("exp", 0)),
    )


def login(client_id):
    """Run the SSO PKCE flow in the browser and return the chosen character."""
    verifier = b64encode(secrets.token_bytes(32))
    state = secrets.token_urlsafe(24)
    result = {}

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            if url.path != "/callback" or query.get("state") != [state]:
                self.send_error(404)
                return
            result["code"] = query.get("code", [""])[0]
            text = "EVE Pressure is linked" if result["code"] else "EVE login was cancelled"
            body = f"<!doctype html><p>{text}. You can close this tab.</p>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "redirect_uri": CALLBACK_URL,
            "client_id": client_id,
            "scope": " ".join(SCOPES),
            "code_challenge": b64encode(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    authorize = f"{endpoint('authorization_endpoint')}?{query}"
    with HTTPServer(("127.0.0.1", CALLBACK_PORT), Callback) as server:
        server.timeout = 1
        webbrowser.open(authorize)
        deadline = time.monotonic() + LOGIN_SECONDS
        while "code" not in result and time.monotonic() < deadline:
            server.handle_request()
    if not result.get("code"):
        raise EsiError("EVE login was cancelled or timed out.")
    form = {
        "grant_type": "authorization_code",
        "code": result["code"],
        "code_verifier": verifier,
        "client_id": client_id,
    }
    return character_from_token(fetch(endpoint("token_endpoint"), form=form)[0], client_id)


def seconds_until_expiry(headers):
    try:
        expires = email.utils.parsedate_to_datetime(headers["Expires"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return POLL_SECONDS
    return expires - time.time()


class EsiWorker:
    """Poll ESI for the followed pilot's dock state and current ship."""

    def __init__(self, path, client_id=""):
        self.path = path
        self.lock = Lock()
        self.client_id = None
        self.characters = {}
        self.pilot = ""
        self.status = Status()
        self.message = ""
        self.ship_names = {}
        self.wake = Event()
        self.stopped = Event()
        self.configure(client_id)
        Thread(target=self.run, daemon=True).start()

    @property
    def linked(self):
        with self.lock:
            return sorted((c.name for c in self.characters.values()), key=str.casefold)

    def configure(self, client_id):
        client_id = client_id.strip()
        with self.lock:
            if client_id == self.client_id:
                return
            self.client_id = client_id
            self.characters = self.load()
        self.wake.set()

    def follow(self, pilot):
        if pilot != self.pilot:
            self.pilot = pilot
            self.status = Status(pilot)
            self.wake.set()

    def load(self):
        try:
            data = json.loads(windows.unprotect(self.path.read_bytes()))
        except (OSError, ValueError):
            return {}
        # Tokens only work with the app that issued them.
        if not isinstance(data, dict) or data.get("client_id") != self.client_id:
            return {}
        characters = {}
        for item in data.get("characters", ()):
            try:
                character = Character(
                    int(item["id"]), str(item["name"]), str(item["refresh_token"])
                )
            except (KeyError, TypeError, ValueError):
                continue
            characters[character.name.casefold()] = character
        return characters

    def save(self):
        data = {
            "client_id": self.client_id,
            "characters": [
                {"id": c.id, "name": c.name, "refresh_token": c.refresh_token}
                for c in self.characters.values()
            ],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_bytes(windows.protect(json.dumps(data).encode()))
            temp.replace(self.path)
        except OSError:
            self.message = "ESI login could not be saved."

    def link(self):
        with self.lock:
            client_id = self.client_id
        if not client_id:
            self.message = "No ESI client ID is set."
            return
        self.message = "Finish the EVE login in your browser…"
        Thread(target=self.finish_link, args=(client_id,), daemon=True).start()

    def finish_link(self, client_id):
        try:
            character = login(client_id)
        except EsiError as error:
            self.message = str(error)
            return
        except (OSError, ValueError, KeyError):
            self.message = (
                f"EVE login failed. Check your connection, and that nothing else uses port "
                f"{CALLBACK_PORT}."
            )
            return
        with self.lock:
            if client_id != self.client_id:
                return
            self.characters[character.name.casefold()] = character
            self.save()
        self.message = f"Linked {character.name}."
        self.wake.set()

    def unlink(self, name):
        with self.lock:
            if self.characters.pop(name.casefold(), None) is not None:
                self.save()
        self.wake.set()

    def refresh(self, client_id, character):
        form = {
            "grant_type": "refresh_token",
            "refresh_token": character.refresh_token,
            "client_id": client_id,
        }
        try:
            data = fetch(endpoint("token_endpoint"), form=form)[0]
        except urllib.error.HTTPError as error:
            try:
                reason = json.loads(error.read()).get("error")
            except (ValueError, OSError, AttributeError):
                reason = None
            # Only a refused refresh token needs a new login; other errors can be passing.
            if reason == "invalid_grant":
                self.unlink(character.name)
                raise EsiError(f"Link {character.name} again in Settings.") from error
            raise
        fresh = character_from_token(data, client_id, character.refresh_token)
        with self.lock:
            if client_id == self.client_id:
                self.characters[fresh.name.casefold()] = fresh
                if fresh.refresh_token != character.refresh_token:
                    self.save()
        return fresh

    def poll(self):
        pilot = self.pilot
        with self.lock:
            client_id = self.client_id
            character = self.characters.get(pilot.casefold())
        if not client_id or character is None:
            self.status = Status(pilot)
            return RETRY_SECONDS
        try:
            if time.time() > character.expires - 60:
                character = self.refresh(client_id, character)
            base = f"{ESI_URL}/characters/{character.id}"
            location, location_headers = fetch(f"{base}/location", token=character.access_token)
            ship, ship_headers = fetch(f"{base}/ship", token=character.access_token)
            ship_type = int(ship["ship_type_id"])
            if ship_type not in self.ship_names:
                info = fetch(f"{ESI_URL}/universe/types/{ship_type}")[0]
                group = fetch(f"{ESI_URL}/universe/groups/{int(info['group_id'])}")[0]
                self.ship_names[ship_type] = str(info["name"]), str(group["name"])
        except EsiError as error:
            self.status = Status(pilot, error=str(error))
            return RETRY_SECONDS
        except urllib.error.HTTPError as error:
            self.status = Status(pilot, error=f"ESI returned {error.code}.")
            if error.code in (420, 429):
                return retry_after(error.headers or {}, 60)
            return RETRY_SECONDS
        except (OSError, ValueError, KeyError, TypeError):
            self.status = Status(pilot, error="ESI is unreachable.")
            return RETRY_SECONDS
        if pilot == self.pilot:
            self.status = Status(
                pilot,
                "station_id" in location or "structure_id" in location,
                ship.get("ship_item_id"),
                *self.ship_names[ship_type],
            )
        expiry = max(seconds_until_expiry(location_headers), seconds_until_expiry(ship_headers))
        delay = min(60, max(POLL_SECONDS, expiry + 0.5))
        return max(delay, rate_spacing(location_headers, ship_headers))

    def run(self):
        while not self.stopped.is_set():
            delay = self.poll()
            self.wake.wait(delay)
            self.wake.clear()
