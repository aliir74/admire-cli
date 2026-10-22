"""OAuth 2.1 login for the Admire MCP server: dynamic client registration + PKCE."""

import base64
import hashlib
import http.server
import json
import secrets
import threading
import time
import urllib.parse
import webbrowser

from . import http as h
from .store import Store

CALLBACK_PATH = "/callback"
SCOPE = "mcp/access"


def discover(server_url):
    """Return (resource, auth_server_metadata) for an MCP server URL."""
    parsed = urllib.parse.urlparse(server_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    resource_meta = h.get_json(f"{origin}/.well-known/oauth-protected-resource{parsed.path}")
    resource = resource_meta.get("resource", server_url)
    issuer = resource_meta["authorization_servers"][0].rstrip("/")
    as_meta = h.get_json(f"{issuer}/.well-known/oauth-authorization-server")
    return resource, as_meta


def _pkce():
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    result = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        _CallbackHandler.result = dict(urllib.parse.parse_qsl(parsed.query))
        ok = "code" in _CallbackHandler.result
        body = ("Logged in to Admire. You can close this tab." if ok
                else "Admire login failed. Check the terminal.")
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass


def login(store: Store, server_url, open_browser=True, timeout=300):
    resource, as_meta = discover(server_url)

    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}{CALLBACK_PATH}"

    registration = h.post_json(as_meta["registration_endpoint"], {
        "client_name": "admire-cli",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    })
    client_id = registration["client_id"]

    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    auth_url = as_meta["authorization_endpoint"] + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    })

    _CallbackHandler.result = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Open this URL to log in:\n\n  {auth_url}\n", flush=True)
    if open_browser:
        webbrowser.open(auth_url)

    deadline = time.time() + timeout
    while _CallbackHandler.result is None and time.time() < deadline:
        time.sleep(0.2)
    server.shutdown()

    result = _CallbackHandler.result
    if result is None:
        raise SystemExit("Login timed out.")
    if "error" in result:
        raise SystemExit(f"Login failed: {result.get('error')} {result.get('error_description', '')}")
    if result.get("state") != state:
        raise SystemExit("Login failed: state mismatch.")

    tokens = h.post_form(as_meta["token_endpoint"], {
        "grant_type": "authorization_code",
        "code": result["code"],
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
        "resource": resource,
    })
    store.save({
        "server_url": server_url,
        "resource": resource,
        "token_endpoint": as_meta["token_endpoint"],
        "revocation_endpoint": as_meta.get("revocation_endpoint"),
        "client_id": client_id,
        **_token_fields(tokens),
    })


def _token_fields(tokens):
    fields = {"access_token": tokens["access_token"]}
    if tokens.get("refresh_token"):
        fields["refresh_token"] = tokens["refresh_token"]
    if tokens.get("expires_in"):
        fields["expires_at"] = int(time.time()) + int(tokens["expires_in"])
    return fields


def refresh(store: Store):
    creds = store.load()
    if not creds.get("refresh_token"):
        raise SystemExit("Session expired. Run `admire login`.")
    try:
        tokens = h.post_form(creds["token_endpoint"], {
            "grant_type": "refresh_token",
            "refresh_token": creds["refresh_token"],
            "client_id": creds["client_id"],
            "resource": creds["resource"],
        })
    except h.HTTPError as e:
        raise SystemExit(f"Token refresh failed ({e.status}). Run `admire login`.")
    creds.update(_token_fields(tokens))
    store.save(creds)
    return creds


def access_token(store: Store):
    creds = store.load()
    if not creds.get("access_token"):
        raise SystemExit("Not logged in. Run `admire login`.")
    if creds.get("expires_at") and creds["expires_at"] - 60 < time.time():
        creds = refresh(store)
    return creds["access_token"]


def logout(store: Store):
    creds = store.load()
    endpoint = creds.get("revocation_endpoint")
    if endpoint and creds.get("refresh_token"):
        try:
            h.post_form(endpoint, {
                "token": creds["refresh_token"],
                "token_type_hint": "refresh_token",
                "client_id": creds["client_id"],
            })
        except h.HTTPError:
            pass
    store.clear()


def decode_jwt_claims(token):
    """Best-effort decode of a JWT payload, for `admire status`. Returns {} if opaque."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        return {}
