"""MCP (Model Context Protocol) HTTP transport — feature #8.

Implements a simplified subset of the MCP JSON-RPC 2.0 wire format over
a single ``POST /mcp/jsonrpc`` endpoint so MCP clients can drive
Autonoma. Five tools are exposed:

    list_sessions        — enumerate active sessions from ``autonoma.api``
    start_swarm_headless — spawn a backend-only swarm run
    fetch_run_summary    — read one ``run_summary`` row by session_id
    fetch_diary          — page an agent's ``agent_journal`` entries
    fetch_world_events   — page rows from ``world_event_log``

Auth: requests must carry ``X-MCP-Token: <settings.coordinator_token>``.
We re-use ``coordinator_token`` here so operators don't have to manage a
fourth secret; a dedicated ``mcp_token`` setting can be added later
without changing the wire format.

The handler returns the standard MCP envelope; tool results are
JSON-stringified inside a single ``{type: "text", text: ...}`` content
block, matching the convention used by the upstream ``mcp`` SDK.
"""

from __future__ import annotations

import hmac
import json
import logging
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import desc, select

from autonoma.config import settings

logger = logging.getLogger(__name__)


# ── JSON-RPC error codes (subset we use) ─────────────────────────────
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


# ── Tool handlers ────────────────────────────────────────────────────


async def _tool_list_sessions(_params: dict[str, Any]) -> Any:
    """Active swarm sessions from the live ``autonoma.api`` registry.

    Imported lazily so the MCP server can run in test contexts that
    don't pull in the full WebSocket app. If the import fails (or the
    module hasn't initialised), we return an empty list rather than
    surface an internal error.
    """
    try:
        from autonoma.api import _sessions  # noqa: PLC0415 — lazy
    except Exception:  # pragma: no cover — defensive
        return []

    out: list[dict[str, Any]] = []
    for sid, sess in list(_sessions.items()):
        room = getattr(sess, "swarm", None)
        out.append(
            {
                "session_id": sid,
                "owner_user_id": getattr(sess, "owner_user_id", None),
                "room_id": getattr(sess, "room_id", sid),
                "is_admin": bool(getattr(sess, "is_admin", False)),
                "status": "running" if room is not None else "idle",
            }
        )
    return out


async def _tool_start_swarm_headless(params: dict[str, Any]) -> Any:
    """Kick off a backend-only swarm run via ``_run_swarm_headless``.

    C2 fix (owner_user_id spoofing): the tool no longer accepts an
    ``owner_user_id`` argument. Previously a holder of the MCP token
    could spawn a session impersonating any user simply by passing
    that user's id, which then leaked into ownership-gated artifact
    endpoints. The MCP token in the current design isn't a *user*
    token — it's a server-side capability — so we attribute every
    headless run to a constant ``"mcp"`` placeholder. When MCP grows
    a real per-user identity model the constant should be replaced
    with the verified subject from the bound token.
    """
    goal = str(params.get("goal") or "").strip()
    if not goal:
        raise ValueError("goal is required")
    preset_id = str(params.get("preset_id") or "")
    max_rounds_raw = params.get("max_rounds", 30)
    try:
        max_rounds = int(max_rounds_raw)
    except (TypeError, ValueError):
        raise ValueError("max_rounds must be an integer")

    # Constant placeholder owner. Any future per-user MCP token model
    # should derive this from the verified token subject — see docstring.
    owner = "mcp"

    from autonoma.api import _run_swarm_headless  # lazy

    sid = await _run_swarm_headless(
        goal=goal,
        owner_user_id=owner,
        preset_id=preset_id,
        max_rounds=max_rounds,
    )
    return {"session_id": sid}


async def _tool_fetch_run_summary(params: dict[str, Any]) -> Any:
    """Return the ``run_summary`` row for a given session, or None."""
    sid_raw = params.get("session_id")
    if sid_raw is None:
        raise ValueError("session_id is required")
    try:
        session_id = int(sid_raw)
    except (TypeError, ValueError):
        raise ValueError("session_id must be an integer")

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import run_summary

    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(run_summary)
                .where(run_summary.c.session_id == session_id)
                .order_by(desc(run_summary.c.id))
            )
        ).all()
    return [_row_to_jsonable(r._mapping) for r in rows]


async def _tool_fetch_diary(params: dict[str, Any]) -> Any:
    """Recent ``agent_journal`` entries for a single character."""
    uuid = str(params.get("character_uuid") or "").strip()
    if not uuid:
        raise ValueError("character_uuid is required")
    limit = int(params.get("limit", 50))
    if limit <= 0:
        limit = 50
    limit = min(limit, 500)

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import agent_journal

    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                select(agent_journal)
                .where(agent_journal.c.character_uuid == uuid)
                .order_by(desc(agent_journal.c.created_at))
                .limit(limit)
            )
        ).all()
    return [_row_to_jsonable(r._mapping) for r in rows]


async def _tool_fetch_world_events(params: dict[str, Any]) -> Any:
    """Recent ``world_event_log`` rows.

    The upstream table currently has no ``session_id`` column — the
    parameter is accepted for forward-compatibility and silently
    ignored when the column isn't present.
    """
    limit = int(params.get("limit", 100))
    if limit <= 0:
        limit = 100
    limit = min(limit, 500)
    session_filter = params.get("session_id")

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import world_event_log

    stmt = select(world_event_log).order_by(desc(world_event_log.c.id)).limit(limit)
    if session_filter is not None and "session_id" in world_event_log.c:
        try:
            stmt = stmt.where(world_event_log.c.session_id == int(session_filter))
        except (TypeError, ValueError):
            raise ValueError("session_id must be an integer")

    engine = get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).all()
    return [_row_to_jsonable(r._mapping) for r in rows]


def _row_to_jsonable(mapping: Any) -> dict[str, Any]:
    """SQLAlchemy ``RowMapping`` → plain JSON-serialisable dict."""
    out: dict[str, Any] = {}
    for key, value in dict(mapping).items():
        if hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


# ── Tool registry ────────────────────────────────────────────────────


ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_sessions",
        "description": "List active swarm sessions (session_id, owner, room, status).",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_list_sessions,
    },
    {
        "name": "start_swarm_headless",
        "description": (
            "Start a backend-only swarm run. Returns the synthetic "
            "session_id (negative integer) that the run is scoped to."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What the swarm should accomplish."},
                # ``owner_user_id`` was removed in the C2 fix — the
                # MCP token isn't a user token, so accepting this
                # field would let any caller impersonate an arbitrary
                # user_id. Server attributes the run to a fixed
                # ``"mcp"`` placeholder instead.
                "preset_id": {"type": "string", "description": "Optional harness preset id."},
                "max_rounds": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 30,
                    "description": "Hard ceiling on rounds.",
                },
            },
            "required": ["goal"],
            "additionalProperties": False,
        },
        "handler": _tool_start_swarm_headless,
    },
    {
        "name": "fetch_run_summary",
        "description": "Return run_summary rows for a given session_id (newest first).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "integer", "description": "Session id to look up."},
            },
            "required": ["session_id"],
            "additionalProperties": False,
        },
        "handler": _tool_fetch_run_summary,
    },
    {
        "name": "fetch_diary",
        "description": "Recent agent_journal entries for a single character (newest first).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "character_uuid": {"type": "string", "description": "Character UUID."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 50,
                },
            },
            "required": ["character_uuid"],
            "additionalProperties": False,
        },
        "handler": _tool_fetch_diary,
    },
    {
        "name": "fetch_world_events",
        "description": "Recent world_event_log rows (newest first).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "integer",
                    "description": "Optional session filter (ignored if the table has no such column).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "default": 100,
                },
            },
            "additionalProperties": False,
        },
        "handler": _tool_fetch_world_events,
    },
]


def _public_tool(t: dict[str, Any]) -> dict[str, Any]:
    """Tool dict with the handler stripped (handler is server-private)."""
    return {k: v for k, v in t.items() if k != "handler"}


def _find_tool(name: str) -> dict[str, Any] | None:
    for t in _TOOLS:
        if t["name"] == name:
            return t
    return None


# ── JSON-RPC method dispatcher ──────────────────────────────────────


_PROTOCOL_VERSION = "2025-03-26"
_SERVER_INFO = {"name": "autonoma-mcp", "version": "0.1.0"}
_CAPABILITIES = {
    "tools": {"listChanged": False},
    "prompts": {"listChanged": False},
    "resources": {"listChanged": False},
}


async def _dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": _CAPABILITIES,
            "serverInfo": _SERVER_INFO,
        }
    if method == "tools/list":
        return {"tools": [_public_tool(t) for t in _TOOLS]}
    if method == "prompts/list":
        return {"prompts": []}
    if method == "resources/list":
        return {"resources": []}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _RpcError(_INVALID_PARAMS, "arguments must be an object")
        tool = _find_tool(name)
        if tool is None:
            raise _RpcError(_METHOD_NOT_FOUND, f"unknown tool: {name}")
        try:
            result = await tool["handler"](arguments)
        except ValueError as exc:
            raise _RpcError(_INVALID_PARAMS, str(exc))
        except _RpcError:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("[mcp] tool %s crashed", name)
            raise _RpcError(_INTERNAL_ERROR, f"tool error: {exc}")
        return {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            "isError": False,
        }
    raise _RpcError(_METHOD_NOT_FOUND, f"unknown method: {method}")


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── HTTP transport ───────────────────────────────────────────────────


router = APIRouter(prefix="/mcp", tags=["mcp"])


def _envelope_error(req_id: Any, code: int, message: str, http_status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": code, "message": message},
        },
    )


def _check_token(supplied: str | None) -> JSONResponse | None:
    expected = settings.coordinator_token
    if not expected:
        return _envelope_error(
            None,
            _INTERNAL_ERROR,
            "MCP server: coordinator_token is not configured.",
            http_status=401,
        )
    if not supplied or not hmac.compare_digest(supplied, expected):
        return _envelope_error(
            None,
            _INVALID_REQUEST,
            "Missing or invalid X-MCP-Token.",
            http_status=401,
        )
    return None


@router.post("/jsonrpc")
async def jsonrpc(
    request: Request,
    x_mcp_token: str | None = Header(default=None, alias="X-MCP-Token"),
) -> JSONResponse:
    auth_failure = _check_token(x_mcp_token)
    if auth_failure is not None:
        return auth_failure

    try:
        raw = await request.body()
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        return _envelope_error(None, _PARSE_ERROR, f"parse error: {exc}")

    if not isinstance(payload, dict):
        return _envelope_error(None, _INVALID_REQUEST, "request must be a JSON object")

    req_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    if payload.get("jsonrpc") != "2.0":
        return _envelope_error(req_id, _INVALID_REQUEST, "jsonrpc must be '2.0'")
    if not isinstance(method, str):
        return _envelope_error(req_id, _INVALID_REQUEST, "method must be a string")
    if not isinstance(params, dict):
        return _envelope_error(req_id, _INVALID_PARAMS, "params must be an object")

    try:
        result = await _dispatch(method, params)
    except _RpcError as exc:
        return _envelope_error(req_id, exc.code, exc.message)

    return JSONResponse(content={"jsonrpc": "2.0", "id": req_id, "result": result})
