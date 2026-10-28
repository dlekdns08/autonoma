"""MCP (Model Context Protocol) server — feature #8.

Built on the official ``mcp`` SDK (2.x, ``mcp.server.mcpserver.MCPServer``
— the class formerly known as ``FastMCP``). Five tools are exposed:

    list_sessions        — enumerate active sessions from ``autonoma.api``
    start_swarm_headless — spawn a backend-only swarm run
    fetch_run_summary    — read one ``run_summary`` row by session_id
    fetch_diary          — page an agent's ``agent_journal`` entries
    fetch_world_events   — page rows from ``world_event_log``

Two transports share the same tool handlers:

* ``/mcp`` — the standard **streamable HTTP** transport from the SDK
  (stateless, JSON responses). Any MCP client can point at it, e.g.
  ``Client("http://host:8000/mcp")`` from the ``mcp`` package.
* ``POST /mcp/jsonrpc`` — the legacy hand-rolled JSON-RPC subset kept
  for backward compatibility. It dispatches ``tools/list`` and
  ``tools/call`` through the same ``MCPServer`` instance so the two
  transports can never disagree about what a tool does.

Auth: requests must carry ``X-MCP-Token: <settings.coordinator_token>``.
We re-use ``coordinator_token`` here so operators don't have to manage a
fourth secret; a dedicated ``mcp_token`` setting can be added later
without changing the wire format. The check is a constant-time compare
on both transports.

Tool results are JSON-stringified inside a single ``{type: "text",
text: ...}`` content block. The SDK would otherwise fan a ``list``
return value out into one block per row (and an empty list into zero
blocks), which breaks clients that expect to ``json.loads`` the first
block — so every tool returns the serialised string itself.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from sqlalchemy import desc, select
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from autonoma.config import settings

logger = logging.getLogger(__name__)


# ── JSON-RPC error codes (subset we use) ─────────────────────────────
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603

MCP_PATH = "/mcp"
"""Mount point of the streamable-HTTP transport on the FastAPI app."""

_SERVER_NAME = "autonoma-mcp"
_SERVER_VERSION = "0.1.0"


# ── Tool handlers ────────────────────────────────────────────────────
# These take the raw ``arguments`` dict so the legacy wire format and
# the typed SDK tools below share one implementation. Validation
# failures raise ``ValueError`` which both transports translate into
# an "invalid params" style error.


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


# ── MCPServer + typed tools ──────────────────────────────────────────

ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]

TOOL_NAMES: tuple[str, ...] = (
    "list_sessions",
    "start_swarm_headless",
    "fetch_run_summary",
    "fetch_diary",
    "fetch_world_events",
)


def _build_mcp_server() -> MCPServer[Any]:
    # ``MCPServer()`` calls ``logging.basicConfig`` with a RichHandler as
    # a convenience for stand-alone servers. Inside the API that would
    # silently re-route the root logger at import time, so snapshot and
    # restore the root logger's handlers/level around construction.
    root = logging.getLogger()
    prev_handlers, prev_level = list(root.handlers), root.level
    server: MCPServer[Any] = MCPServer(
        name=_SERVER_NAME,
        version=_SERVER_VERSION,
        instructions=(
            "Drive an Autonoma swarm: list live sessions, start headless runs "
            "and read run summaries, agent diaries and world events. Every "
            "tool returns a JSON document in a single text block."
        ),
    )
    if root.handlers != prev_handlers or root.level != prev_level:
        root.handlers[:] = prev_handlers
        root.setLevel(prev_level)
    return server


mcp_server: MCPServer[Any] = _build_mcp_server()


async def _run_tool(handler: ToolHandler, params: dict[str, Any]) -> str:
    """Run a raw handler and serialise its result the legacy way.

    ``ValueError`` is the handlers' "bad arguments" signal; surface it
    as a ``ToolError`` so the SDK reports it as an ``isError`` result
    carrying the message instead of a redacted crash.
    """
    try:
        result = await handler(params)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return json.dumps(result, ensure_ascii=False)


@mcp_server.tool(
    name="list_sessions",
    description="List active swarm sessions (session_id, owner, room, status).",
    structured_output=False,
)
async def list_sessions() -> str:
    return await _run_tool(_tool_list_sessions, {})


@mcp_server.tool(
    name="start_swarm_headless",
    description=(
        "Start a backend-only swarm run. Returns the synthetic "
        "session_id (negative integer) that the run is scoped to."
    ),
    structured_output=False,
)
async def start_swarm_headless(
    goal: Annotated[str, Field(description="What the swarm should accomplish.")],
    preset_id: Annotated[str, Field(description="Optional harness preset id.")] = "",
    max_rounds: Annotated[int, Field(ge=1, description="Hard ceiling on rounds.")] = 30,
) -> str:
    # ``owner_user_id`` is deliberately not accepted — see the C2 note
    # on ``_tool_start_swarm_headless``.
    return await _run_tool(
        _tool_start_swarm_headless,
        {"goal": goal, "preset_id": preset_id, "max_rounds": max_rounds},
    )


@mcp_server.tool(
    name="fetch_run_summary",
    description="Return run_summary rows for a given session_id (newest first).",
    structured_output=False,
)
async def fetch_run_summary(
    session_id: Annotated[int, Field(description="Session id to look up.")],
) -> str:
    return await _run_tool(_tool_fetch_run_summary, {"session_id": session_id})


@mcp_server.tool(
    name="fetch_diary",
    description="Recent agent_journal entries for a single character (newest first).",
    structured_output=False,
)
async def fetch_diary(
    character_uuid: Annotated[str, Field(description="Character UUID.")],
    limit: Annotated[int, Field(ge=1, le=500)] = 50,
) -> str:
    return await _run_tool(_tool_fetch_diary, {"character_uuid": character_uuid, "limit": limit})


@mcp_server.tool(
    name="fetch_world_events",
    description="Recent world_event_log rows (newest first).",
    structured_output=False,
)
async def fetch_world_events(
    session_id: Annotated[
        int | None,
        Field(description="Optional session filter (ignored if the table has no such column)."),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=500)] = 100,
) -> str:
    params: dict[str, Any] = {"limit": limit}
    if session_id is not None:
        params["session_id"] = session_id
    return await _run_tool(_tool_fetch_world_events, params)


# ── Auth ─────────────────────────────────────────────────────────────


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


# ── Streamable HTTP transport (``/mcp``) ─────────────────────────────
#
# The SDK's ``streamable_http_app()`` returns a Starlette app whose
# *lifespan* starts the session manager. Sub-app lifespans don't run
# when mounted inside FastAPI, and a ``StreamableHTTPSessionManager``
# can only be ``run()`` once, so instead we build a fresh manager per
# server lifespan (``lifespan()`` below) and route ``/mcp`` to a thin
# ASGI endpoint that checks the token and hands the request to
# whichever manager is live.

_active_manager: StreamableHTTPSessionManager | None = None


def _build_session_manager() -> StreamableHTTPSessionManager:
    # ``streamable_http_app()`` is the public way to get a manager
    # bound to this server; the Starlette app it returns is discarded.
    # DNS-rebinding protection is off: the API already serves on
    # arbitrary hosts (Docker, reverse proxies) and the endpoint is
    # gated by the shared token.
    mcp_server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return mcp_server.session_manager


@asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    """Run the streamable-HTTP session manager for the app's lifetime.

    Enter this from the FastAPI lifespan (``api.py`` does so when
    ``settings.mcp_server_enabled``). The legacy ``/mcp/jsonrpc``
    endpoint does not need it.
    """
    global _active_manager
    manager = _build_session_manager()
    async with manager.run():
        _active_manager = manager
        try:
            yield
        finally:
            if _active_manager is manager:
                _active_manager = None


class _StreamableHTTPEndpoint:
    """Raw ASGI endpoint: token gate → SDK streamable-HTTP handler."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        auth_failure = _check_token(headers.get("x-mcp-token"))
        if auth_failure is not None:
            await auth_failure(scope, receive, send)
            return
        manager = _active_manager
        if manager is None:
            response = _envelope_error(
                None,
                _INTERNAL_ERROR,
                "MCP streamable-HTTP transport is not running (server lifespan not started).",
                http_status=503,
            )
            await response(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)


streamable_http_asgi = _StreamableHTTPEndpoint()


def mount(app: FastAPI) -> None:
    """Attach both MCP transports to ``app``.

    * ``/mcp`` (GET/POST/DELETE) — streamable HTTP.
    * ``POST /mcp/jsonrpc`` — legacy JSON-RPC subset.

    The caller is responsible for entering :func:`lifespan` so the
    streamable transport has a running session manager.
    """
    app.include_router(router)
    # ``Route`` with a non-function endpoint uses it as a raw ASGI app
    # (``app.add_route`` would wrap it as a request→response handler).
    app.router.routes.append(
        Route(
            MCP_PATH,
            endpoint=streamable_http_asgi,
            methods=["GET", "POST", "DELETE"],
            name="mcp_streamable_http",
            include_in_schema=False,
        )
    )


# ── Legacy JSON-RPC transport (``POST /mcp/jsonrpc``) ────────────────


_PROTOCOL_VERSION = "2025-03-26"
_SERVER_INFO = {"name": _SERVER_NAME, "version": _SERVER_VERSION}
_CAPABILITIES = {
    "tools": {"listChanged": False},
    "prompts": {"listChanged": False},
    "resources": {"listChanged": False},
}


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _strip_tool_prefix(name: str, message: str) -> str:
    """``Error executing tool X: msg`` → ``msg`` (legacy wire fidelity)."""
    prefix = f"Error executing tool {name}: "
    return message[len(prefix) :] if message.startswith(prefix) else message


async def _dispatch(method: str, params: dict[str, Any]) -> Any:
    if method == "initialize":
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": _CAPABILITIES,
            "serverInfo": _SERVER_INFO,
        }
    if method == "tools/list":
        tools = await mcp_server.list_tools()
        return {
            "tools": [t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools]
        }
    if method == "prompts/list":
        return {"prompts": []}
    if method == "resources/list":
        return {"resources": []}
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise _RpcError(_INVALID_PARAMS, "arguments must be an object")
        if name not in TOOL_NAMES:
            raise _RpcError(_METHOD_NOT_FOUND, f"unknown tool: {name}")
        try:
            result = await mcp_server.call_tool(name, arguments)
        except UnexpectedToolError as exc:
            logger.exception("[mcp] tool %s crashed", name)
            raise _RpcError(_INTERNAL_ERROR, f"tool error: {exc.__cause__ or exc}")
        except ToolError as exc:
            raise _RpcError(_INVALID_PARAMS, _strip_tool_prefix(name, str(exc)))
        payload = result.model_dump(by_alias=True, exclude_none=True, mode="json")
        return {
            "content": payload.get("content", []),
            "isError": bool(payload.get("isError", False)),
        }
    raise _RpcError(_METHOD_NOT_FOUND, f"unknown method: {method}")


router = APIRouter(prefix=MCP_PATH, tags=["mcp"])


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
