"""Minimal stdlib HTTP helpers."""

import json
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "admire-cli"
TIMEOUT = 60


class HTTPError(Exception):
    def __init__(self, status, body, headers=None):
        super().__init__(f"HTTP {status}: {body[:500]}")
        self.status = status
        self.body = body
        self.headers = headers or {}


def request(method, url, body=None, headers=None):
    """Return (status, headers, body_text). Raises HTTPError on 4xx/5xx."""
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, dict(resp.headers), resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise HTTPError(e.code, e.read().decode("utf-8", "replace"), dict(e.headers)) from None


def get_json(url):
    _, _, text = request("GET", url, headers={"Accept": "application/json"})
    return json.loads(text)


def post_json(url, payload):
    _, _, text = request("POST", url, json.dumps(payload).encode(), {
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    return json.loads(text)


def post_form(url, fields):
    _, _, text = request("POST", url, urllib.parse.urlencode(fields).encode(), {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })
    return json.loads(text) if text.strip() else {}
