"""Minimal client for the Oracle Analytics Cloud MCP server, over HTTP (JSON-RPC, "streamable HTTP").

Endpoint: <OAC_URL>/api/mcp, with the user's OAuth token (Authorization: Bearer).
Queries run with the identity and permissions of the token's owner: no technical account.

MCP sequence: initialize -> (Mcp-Session-Id header) -> notifications/initialized -> tools/call.
The answer can come as JSON or as an SSE stream: both are handled.
"""
import itertools
import json

import httpx

PROTOCOL_VERSION = "2025-06-18"
PREFIX = "oracle_analytics-"


class McpError(RuntimeError):
    pass


class OacMcp:
    def __init__(self, oac_url, token, timeout=120.0):
        """`token`: a string, or an object with .current() (OacToken) that renews it when needed."""
        self.url = oac_url.rstrip("/") + "/api/mcp"
        self.token = token
        self.http = httpx.Client(timeout=timeout, headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        })
        self.session_id = None
        self.server_info = None
        self._ids = itertools.count(1)

    # -- transport -----------------------------------------------------------
    def _post(self, payload):
        token = self.token if isinstance(self.token, str) else self.token.current()
        headers = {"Authorization": f"Bearer {token}"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        r = self.http.post(self.url, json=payload, headers=headers)
        if r.status_code >= 400:
            raise McpError(f"HTTP {r.status_code}: {r.text[:500]}")
        return r

    @staticmethod
    def _messages(r):
        """JSON-RPC messages in the response (plain JSON or SSE events)."""
        if not r.content:
            return []
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            out = []
            for event in r.text.split("\n\n"):
                data = "\n".join(l[5:].lstrip() for l in event.splitlines() if l.startswith("data:"))
                if data:
                    out.append(json.loads(data))
            return out
        body = r.json()
        return body if isinstance(body, list) else [body]

    def _request(self, method, params=None):
        rid = next(self._ids)
        r = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        for m in self._messages(r):
            if m.get("id") == rid:
                if "error" in m:
                    raise McpError(f"{method}: {m['error']}")
                return m["result"], r
        raise McpError(f"{method}: no response with id {rid}")

    # -- protocol -------------------------------------------------------------
    def initialize(self):
        result, r = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "live-insight", "version": "0.1"}})
        self.session_id = r.headers.get("mcp-session-id")
        self.server_info = result
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self):
        tools, cursor = [], None
        while True:
            result, _ = self._request("tools/list", {"cursor": cursor} if cursor else {})
            tools += result.get("tools", [])
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    def call(self, tool, **arguments):
        """Calls a tool (name with or without the oracle_analytics- prefix). Returns the MCP `result`."""
        name = tool if tool.startswith(PREFIX) else PREFIX + tool
        result, _ = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError(f"{name}: {text_of(result)[:1000]}")
        return result

    def read_resource(self, uri):
        """resources/read: metadata (metadata://catalog/...) or JSON content (content://catalog/...)."""
        result, _ = self._request("resources/read", {"uri": uri})
        return result.get("contents", [])

    def close(self):
        self.http.close()

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *exc):
        self.close()


def text_of(result):
    """The text of the `content` blocks of an MCP result, joined."""
    return "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")


def rows_of(result):
    """Rows of an execute_logical_sql result: list of dicts alias -> value.

    The result comes in batches ({"batches": [{"data": [...], "hasMore": ...}], "status": {...}}).
    """
    body = payload_of(result)
    if body.get("status", {}).get("error"):
        raise McpError(body["status"].get("message", "query error"))
    return [row for batch in body.get("batches", []) for row in batch.get("data", [])]


def payload_of(result):
    """The structured content of a result: structuredContent if present, else the text as JSON."""
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    text = text_of(result)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
