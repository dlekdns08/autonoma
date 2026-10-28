"""Workspace routes: files, templates, file history, agent memory, export.

Extracted from ``autonoma.api``: paths, methods, auth dependencies,
status codes and behaviour are unchanged. Live session state stays in
``autonoma.api`` and is reached through lazy imports so importing this
router never triggers the module-load cycle.
"""

from __future__ import annotations

import io
import logging
import zipfile
from typing import TYPE_CHECKING, Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi import (
    status as http_status,
)
from fastapi.responses import Response

from autonoma.auth import (
    SESSION_COOKIE_NAME,
    read_session_token,
    require_active_user,
    require_admin,
)
from autonoma.db.users import (
    User,
    get_user_by_id,
)
from autonoma.event_bus import bus

if TYPE_CHECKING:
    from autonoma.api import SessionState

logger = logging.getLogger(__name__)

router = APIRouter()


def _live_sessions() -> dict[int, SessionState]:
    """The live session registry, owned by ``autonoma.api``.

    Imported lazily: ``autonoma.api`` includes this router at module
    load, so a top-level import here would be a cycle.
    """
    from autonoma.api import _sessions

    return _sessions


def _session_project(session_id: int | None) -> Any | None:
    if session_id is None:
        return None
    session = _live_sessions().get(session_id)
    return session.project if session else None


def _require_session(session_id: int | None) -> SessionState:
    if session_id is None:
        raise HTTPException(status_code=400, detail="session query parameter is required")
    session = _live_sessions().get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


async def _require_session_owner(session_id: int | None, request: Request) -> SessionState:
    """Like ``_require_session`` but rejects cross-user access.

    If the session was created by a logged-in user (``owner_user_id`` is
    set), the caller's cookie must identify that same user — or an
    admin. Anonymous sessions (legacy admin-password or BYO-key paths)
    retain the old "session id is itself the capability" contract since
    there is no user to bind them to.
    """
    session = _require_session(session_id)
    if session.owner_user_id is None:
        return session

    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    cookie_user_id = read_session_token(cookie_token or "")
    cookie_user = await get_user_by_id(cookie_user_id) if cookie_user_id else None
    if cookie_user is None or cookie_user.status != "active":
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for this session's artifacts",
        )
    if cookie_user.id != session.owner_user_id and cookie_user.role != "admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="This session belongs to another user",
        )
    return session


@router.get("/api/files")
async def list_files(
    request: Request,
    session: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    # Gate on ownership so a viewer can't enumerate logged-in users'
    # sessions by probing sequential ids. See ``_require_session_owner``
    # for the anonymous-session passthrough rule.
    sess = await _require_session_owner(session, request)
    project = sess.project
    if project is None:
        return {
            "project": None,
            "total": 0,
            "limit": limit,
            "offset": offset,
            "files": [],
        }
    # Slice BEFORE serializing — a long-running swarm with 10k+
    # artifacts would otherwise burn O(N) memory and serialization
    # time on every poll.
    page = project.files[offset : offset + limit]
    return {
        "project": project.name,
        "total": len(project.files),
        "limit": limit,
        "offset": offset,
        "files": [
            {
                "path": f.path,
                "size": len(f.content),
                "description": f.description,
                "created_by": f.created_by,
            }
            for f in page
        ],
    }


@router.get("/api/files/download")
async def download_file(
    request: Request,
    path: str,
    session: int | None = Query(default=None),
) -> Response:
    sess = await _require_session_owner(session, request)
    project = sess.project
    if project is None:
        raise HTTPException(status_code=404, detail="No active project for session")
    artifact = next((f for f in project.files if f.path == path), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    filename = path.rsplit("/", 1)[-1] or "file"
    return Response(
        content=artifact.content.encode("utf-8"),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/files/zip")
async def download_zip(
    request: Request,
    session: int | None = Query(default=None),
) -> Response:
    sess = await _require_session_owner(session, request)
    project = sess.project
    if project is None or not project.files:
        raise HTTPException(status_code=404, detail="No files to download")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for artifact in project.files:
            safe_path = "/".join(seg for seg in artifact.path.split("/") if seg and seg != "..")
            if not safe_path:
                continue
            zf.writestr(safe_path, artifact.content)
    project_name = project.name or "autonoma-project"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{project_name}.zip"'},
    )


@router.get("/api/templates")
async def list_templates(
    _user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return all scaffold templates (name, description, file count).

    Auth-gated so the template list isn't leaked to anonymous probes —
    pairs with ``apply_template`` below which writes into a running
    session and therefore *must* be authenticated.
    """
    from autonoma.templates import SCAFFOLD_TEMPLATES

    return {
        "templates": [
            {
                "id": tid,
                "name": tmpl["name"],
                "description": tmpl["description"],
                "file_count": len(tmpl["files"]),
            }
            for tid, tmpl in SCAFFOLD_TEMPLATES.items()
        ]
    }


@router.post("/api/templates/{template_id}/apply")
async def apply_template(
    template_id: str,
    payload: dict[str, Any],
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Apply a scaffold template to a running session.

    Queues placeholder file tasks for Coder agents to fill in.
    ``payload`` must include ``session_id`` of the running session.

    Authorisation: the caller must own the target session
    (``session.owner_user_id == user.id``) — without this gate, any
    authenticated user could inject placeholder files into an admin's
    swarm, which Coder agents would then execute. Admins can target any
    session (matches the pattern used by ``/api/workspace/export``).
    """
    from autonoma.templates import SCAFFOLD_TEMPLATES

    tmpl = SCAFFOLD_TEMPLATES.get(template_id)
    if tmpl is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_id}' not found",
        )

    session_id = payload.get("session_id")
    session = _live_sessions().get(session_id) if isinstance(session_id, int) else None
    # Ownership check — refuse cross-tenant template injection. We
    # 404 (not 403) so a probing client can't enumerate which session
    # ids exist by watching for the auth-vs-not-found split.
    if session is not None and user.role != "admin":
        if session.owner_user_id is not None and session.owner_user_id != user.id:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail="Session not found",
            )

    created: list[str] = []
    if session is not None and session.project is not None:
        from autonoma.models import FileArtifact

        for file_spec in tmpl["files"]:
            path = file_spec["path"]
            description = file_spec["description"]
            # Only add if not already present
            if not any(f.path == path for f in session.project.files):
                artifact = FileArtifact(
                    path=path,
                    content=f"# {description}\n# TODO: implement\n",
                    created_by="scaffold",
                    description=description,
                )
                session.project.files.append(artifact)
                created.append(path)
                await bus.emit(
                    "file.created",
                    path=path,
                    size=len(artifact.content),
                    description=description,
                    agent="scaffold",
                )

    return {
        "template_id": template_id,
        "name": tmpl["name"],
        "files_created": created,
        "total_files": len(tmpl["files"]),
    }


@router.get("/api/files/history")
async def get_file_history(
    path: str,
    session: int | None = Query(default=None),
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the version list for a file path within a session.

    Accessible to authenticated users (not admin-only). Content is omitted
    from the list — use ``GET /api/files/history/{version_id}`` for that.
    """
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import file_history as fh_table

    if session is None:
        raise HTTPException(status_code=400, detail="session query parameter required")

    # Cross-user history reads are an admin-only capability. If the
    # session is still live and owned by this user that's fine; if it's
    # anonymous we fall back to "admin only" rather than leaving history
    # reads open to anyone who guesses a session id.
    live = _live_sessions().get(session)
    if live is not None and live.owner_user_id is not None:
        if live.owner_user_id != user.id and user.role != "admin":
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="This session belongs to another user",
            )
    elif user.role != "admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can read history for anonymous or ended sessions",
        )

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            select(
                fh_table.c.id,
                fh_table.c.path,
                fh_table.c.created_by,
                fh_table.c.created_at,
                fh_table.c.version_number,
                fh_table.c.content,
            )
            .where(
                fh_table.c.session_id == session,
                fh_table.c.path == path,
            )
            .order_by(fh_table.c.version_number)
        )
        rows = result.fetchall()

    versions = [
        {
            "version_id": r.id,
            "version_number": r.version_number,
            "created_by": r.created_by,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "size": len(r.content),
        }
        for r in rows
    ]
    return {"path": path, "session_id": session, "versions": versions}


@router.get("/api/files/history/{version_id}")
async def get_file_version_content(
    version_id: int,
    user: User = Depends(require_active_user),
) -> dict[str, Any]:
    """Return the full content of a specific file version by ``version_id``."""
    from sqlalchemy import select

    from autonoma.db.engine import get_engine
    from autonoma.db.schema import file_history as fh_table

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(select(fh_table).where(fh_table.c.id == version_id))
        row = result.first()

    if row is None:
        raise HTTPException(status_code=404, detail="version_not_found")

    # Same ownership gate as the list endpoint: either the session is
    # live and owned by this user, or the caller is an admin. Version
    # ids are sequential so we can't rely on them being unguessable.
    live = _live_sessions().get(row.session_id)
    if live is not None and live.owner_user_id is not None:
        if live.owner_user_id != user.id and user.role != "admin":
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="This version belongs to another user's session",
            )
    elif user.role != "admin":
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="Only admins can read versions from anonymous or ended sessions",
        )

    return {
        "version_id": row.id,
        "session_id": row.session_id,
        "path": row.path,
        "content": row.content,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "version_number": row.version_number,
    }


# ── /api/agents/{name}/memory — Feature 11: Agent Memory Inspector ─────────


@router.get("/api/agents/{agent_name}/memory")
async def get_agent_memory(
    agent_name: str,
    session: int | None = Query(default=None),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Return the live AgentMemory state for ``agent_name``. Admin-only.

    Requires a running swarm (returns 404 if none is active for the given
    session). The ``session`` query param identifies which session's swarm
    to inspect; when omitted, the first session with a live swarm is used.
    """
    target_session: SessionState | None = None
    if session is not None:
        target_session = _live_sessions().get(session)
    else:
        for s in _live_sessions().values():
            if s.swarm is not None:
                target_session = s
                break

    if target_session is None or target_session.swarm is None:
        raise HTTPException(status_code=404, detail="no_active_swarm")

    swarm = target_session.swarm
    agent = swarm.agents.get(agent_name)
    if agent is None:
        raise HTTPException(status_code=404, detail=f"agent_not_found: {agent_name}")

    memory = getattr(agent, "memory", None)
    if memory is None:
        raise HTTPException(status_code=404, detail="agent_has_no_memory")

    mem_dict = memory.to_dict()
    experiences = [
        {
            "content": e["text"],
            "category": e["type"],
            "round": e["round"],
        }
        for e in mem_dict.get("private", [])
    ]
    hindsight_notes = [f"{n['title']}: {n['lesson']}" for n in mem_dict.get("hindsight", [])]

    # Relationship opinions: trust values for each peer this agent has
    # interacted with. Pulled from the swarm's relationship graph.
    relationship_opinions: dict[str, float] = {}
    rel_graph = getattr(swarm, "relationships", None)
    if rel_graph is not None:
        for (frm, to), rel in rel_graph._graph.items():
            if frm == agent_name:
                relationship_opinions[to] = round(rel.trust, 3)

    return {
        "agent": agent_name,
        "experiences": experiences,
        "hindsight_notes": hindsight_notes,
        "relationship_opinions": relationship_opinions,
    }


# ── /api/workspace/export — Feature 21: Workspace Export ───────────────────


@router.get("/api/workspace/export")
async def export_workspace(
    session: int | None = Query(default=None),
    format: str = Query(default="zip"),
    user: User = Depends(require_active_user),
) -> Response:
    """Download the full workspace as a zip archive.

    Includes all generated files, a README_AUTONOMA.txt with run metadata,
    a tasks.json summary, and a chat_log.txt with all agent messages.
    Accessible to authenticated users.
    """
    if format != "zip":
        raise HTTPException(status_code=400, detail="only format=zip is supported")

    project = _session_project(session)
    if project is None:
        raise HTTPException(status_code=404, detail="No active project for session")

    sess = _live_sessions().get(session) if session is not None else None
    swarm = sess.swarm if sess is not None else None

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── Project files ──
        for artifact in project.files:
            safe_path = "/".join(seg for seg in artifact.path.split("/") if seg and seg != "..")
            if not safe_path:
                continue
            zf.writestr(safe_path, artifact.content)

        # ── README_AUTONOMA.txt ──
        agent_names = [a.name for a in project.agents]
        round_count = swarm._round if swarm is not None else 0
        task_summary_lines = [
            f"  [{t.status.value.upper()}] {t.title} (assigned: {t.assigned_to or 'unassigned'})"
            for t in project.tasks
        ]
        readme_lines = [
            "AUTONOMA WORKSPACE EXPORT",
            "=" * 40,
            f"Project : {project.name}",
            f"Goal    : {project.description}",
            f"Agents  : {', '.join(agent_names)}",
            f"Rounds  : {round_count}",
            f"Files   : {len(project.files)}",
            f"Tasks   : {len(project.tasks)}",
            "",
            "TASK SUMMARY",
            "-" * 20,
        ] + task_summary_lines
        zf.writestr("README_AUTONOMA.txt", "\n".join(readme_lines))

        # ── tasks.json ──
        import json as _json

        tasks_data = [
            {
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "status": t.status.value,
                "priority": t.priority.value,
                "assigned_to": t.assigned_to,
                "created_by": t.created_by,
                "output": t.output,
                "artifacts": t.artifacts,
                "created_at": t.created_at.isoformat(),
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in project.tasks
        ]
        zf.writestr("tasks.json", _json.dumps(tasks_data, indent=2))

        # ── chat_log.txt ──
        chat_lines = []
        for msg in sorted(project.messages, key=lambda m: m.timestamp):
            ts = msg.timestamp.strftime("%H:%M:%S")
            chat_lines.append(f"[{ts}] {msg.sender} -> {msg.recipient}: {msg.content}")
        zf.writestr("chat_log.txt", "\n".join(chat_lines))

    session_tag = str(session)[:8] if session is not None else "unknown"
    filename = f"autonoma_workspace_{session_tag}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
