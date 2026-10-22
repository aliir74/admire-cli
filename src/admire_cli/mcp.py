"""Minimal MCP client over the Streamable HTTP transport."""

import json

from . import http as h
from . import oauth
from .store import Store

PROTOCOL_VERSION = "2025-06-18"


class MCPError(Exception):
    pass


def _parse_response(headers, text, request_id):
    content_type = {k.lower(): v for k, v in headers.items()}.get("content-type", "")
    if "text/event-stream" not in content_type:
        return json.loads(text) if text.strip() else None
    # SSE: collect `data:` lines per event, return the message answering our request.
    data_lines = []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "" and data_lines:
            message = json.loads("\n".join(data_lines))
            data_lines = []
            if message.get("id") == request_id:
                return message
    return None


class Client:
    def __init__(self, store: Store, server_url=None):
        self.store = store
        self.server_url = server_url or store.load().get("server_url")
        if not self.server_url:
            raise SystemExit("Not logged in. Run `admire login`.")
        self.session_id = None
        self._next_id = 0

    def _post(self, payload, retry=True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {oauth.access_token(self.store)}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            _, resp_headers, text = h.request(
                "POST", self.server_url, json.dumps(payload).encode(), headers)
        except h.HTTPError as e:
            if e.status == 401 and retry:
                oauth.refresh(self.store)
                return self._post(payload, retry=False)
            raise
        session = {k.lower(): v for k, v in resp_headers.items()}.get("mcp-session-id")
        if session:
            self.session_id = session
        return resp_headers, text

    def _rpc(self, method, params=None):
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers, text = self._post(payload)
        message = _parse_response(headers, text, self._next_id)
        if message is None:
            raise MCPError(f"No response to {method}")
        if "error" in message:
            err = message["error"]
            raise MCPError(f"{err.get('message')} ({err.get('code')})")
        return message.get("result")

    def initialize(self):
        result = self._rpc("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "admire-cli", "version": "0.1.0"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self):
        tools, cursor = [], None
        while True:
            result = self._rpc("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name, arguments):
        return self._rpc("tools/call", {"name": name, "arguments": arguments})
