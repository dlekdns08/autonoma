"""Tests for the live mocap ingest WebSocket — ``/api/mocap/live``.

These pin the contract documented in ``routers/mocap_live.py``:

* a valid frame is re-emitted on the bus as ``mocap.frame``
* a malformed frame (rot len != 4) is dropped — no event
* an oversized frame (> 32 KB) is dropped — no event
* a 200-frame burst inside one second is throttled to <70 events
* on close, ``mocap.frame.session_ended`` carries the accept/drop counts

Auth is exercised via a real signed session cookie produced by
``autonoma.auth.issue_session_token`` with a guest UUID — that path
doesn't touch the DB, so the test stays self-contained.

We use ``starlette.testclient.TestClient`` for the WebSocket itself
because httpx's ASGI transport does not implement the WS subprotocol;
the docstring on the file already calls this out as the chosen path.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from autonoma.auth import (
    SESSION_COOKIE_NAME,
    issue_session_token,
    new_guest_user_id,
)
from autonoma.event_bus import bus
from autonoma.routers import mocap_live as mocap_live_mod


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def app() -> FastAPI:
    """Throwaway app with only the mocap_live router mounted."""
    a = FastAPI()
    a.include_router(mocap_live_mod.router)
    return a


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Synchronous WS-capable test client.

    ``TestClient`` runs the ASGI app on a worker thread which lets us
    use the ``websocket_connect`` context manager from regular pytest
    code. The router itself is async so this is fine — Starlette bridges
    the loop.
    """
    return TestClient(app)


@pytest.fixture
def session_cookie() -> dict[str, str]:
    """A real, signed session cookie. Uses a guest user_id so no DB."""
    token = issue_session_token(new_guest_user_id())
    return {SESSION_COOKIE_NAME: token}


@pytest.fixture
def captured_events() -> list[tuple[str, dict[str, Any]]]:
    """Subscribe to ``mocap.frame*`` and return a list to assert against.

    The autouse ``_reset`` fixture in ``conftest.py`` already wipes
    ``bus._handlers`` before and after the test, so we don't have to
    worry about leaking subscriptions.
    """
    events: list[tuple[str, dict[str, Any]]] = []

    async def _on_frame(**data: Any) -> None:
        events.append(("mocap.frame", data))

    async def _on_end(**data: Any) -> None:
        events.append(("mocap.frame.session_ended", data))

    bus.on("mocap.frame", _on_frame)
    bus.on("mocap.frame.session_ended", _on_end)
    return events


# ── Helpers ───────────────────────────────────────────────────────────


def _good_frame() -> dict[str, Any]:
    """A minimal but fully-valid frame the router should accept."""
    return {
        "bones": {
            "Hips": {"pos": [0.0, 1.0, 0.0], "rot": [0.0, 0.0, 0.0, 1.0]},
            "Head": {"pos": [0.0, 1.6, 0.0], "rot": [0.0, 0.0, 0.0, 1.0]},
        },
        "blendshapes": {"Joy": 0.4},
        "root": {"pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0, 1.0]},
        "t": 12345,
    }


# ── Tests ─────────────────────────────────────────────────────────────


def test_valid_frame_emits_mocap_frame(
    client: TestClient,
    session_cookie: dict[str, str],
    captured_events: list[tuple[str, dict[str, Any]]],
) -> None:
    with client.websocket_connect(
        "/api/mocap/live?vrm=alice.vrm", cookies=session_cookie
    ) as ws:
        ws.send_text(json.dumps(_good_frame()))
        # Closing the WS triggers the finally block which emits the
        # session_ended event and unblocks the server task.
        ws.close()

    frame_events = [e for e in captured_events if e[0] == "mocap.frame"]
    assert len(frame_events) == 1
    name, payload = frame_events[0]
    assert payload["vrm_file"] == "alice.vrm"
    assert payload["bones"]["Hips"]["pos"] == [0.0, 1.0, 0.0]
    assert payload["bones"]["Hips"]["rot"] == [0.0, 0.0, 0.0, 1.0]
    assert payload["blendshapes"] == {"Joy": pytest.approx(0.4)}
    assert payload["root"]["rot"] == [0.0, 0.0, 0.0, 1.0]


def test_malformed_rot_drops_frame(
    client: TestClient,
    session_cookie: dict[str, str],
    captured_events: list[tuple[str, dict[str, Any]]],
) -> None:
    bad = _good_frame()
    # rot must be length-4; 3 floats triggers the validator.
    bad["bones"]["Hips"]["rot"] = [0.0, 0.0, 0.0]

    with client.websocket_connect(
        "/api/mocap/live?vrm=bob.vrm", cookies=session_cookie
    ) as ws:
        ws.send_text(json.dumps(bad))
        ws.close()

    frame_events = [e for e in captured_events if e[0] == "mocap.frame"]
    assert frame_events == []
    end_events = [e for e in captured_events if e[0] == "mocap.frame.session_ended"]
    assert len(end_events) == 1
    assert end_events[0][1]["frames_accepted"] == 0
    assert end_events[0][1]["frames_dropped"] == 1


def test_oversized_frame_dropped(
    client: TestClient,
    session_cookie: dict[str, str],
    captured_events: list[tuple[str, dict[str, Any]]],
) -> None:
    # Build a frame whose JSON serialisation comfortably exceeds 32 KB
    # — a wide ``blendshapes`` map is the cheapest knob to turn.
    huge = {
        "blendshapes": {f"k{i:05d}": 0.5 for i in range(8000)},
    }
    serialized = json.dumps(huge)
    assert len(serialized) > 32 * 1024  # sanity: actually oversized

    with client.websocket_connect(
        "/api/mocap/live?vrm=carol.vrm", cookies=session_cookie
    ) as ws:
        ws.send_text(serialized)
        ws.close()

    frame_events = [e for e in captured_events if e[0] == "mocap.frame"]
    assert frame_events == []


def test_burst_is_rate_limited(
    client: TestClient,
    session_cookie: dict[str, str],
    captured_events: list[tuple[str, dict[str, Any]]],
) -> None:
    """200 frames sent back-to-back should land below the 60 fps ceiling.

    The loop runs as fast as the test thread can push messages — well
    under one second on any normal box — so the token bucket should
    permit at most ~60 frames + the initial bucket capacity. The spec
    asks for "<70 events emitted", which gives us a couple of tokens of
    slack for refill during the send window.
    """
    payload = json.dumps(_good_frame())
    started = time.monotonic()
    with client.websocket_connect(
        "/api/mocap/live?vrm=dan.vrm", cookies=session_cookie
    ) as ws:
        for _ in range(200):
            ws.send_text(payload)
        ws.close()
    elapsed = time.monotonic() - started

    frame_events = [e for e in captured_events if e[0] == "mocap.frame"]
    # Sanity-guard the test environment: if the loopback stack ran the
    # burst slower than ~1.5 s the bucket would refill enough that the
    # rate-limit assertion no longer means much. In practice this is
    # always sub-second on dev machines and CI.
    assert elapsed < 1.5, (
        f"burst took {elapsed:.2f}s — too slow to meaningfully test rate limit"
    )
    assert len(frame_events) < 70, (
        f"expected <70 events under rate limit, got {len(frame_events)}"
    )
    # And we should still have *some* events — otherwise the limiter is
    # rejecting everything, which would be a different bug.
    assert len(frame_events) > 0


def test_session_ended_carries_counts(
    client: TestClient,
    session_cookie: dict[str, str],
    captured_events: list[tuple[str, dict[str, Any]]],
) -> None:
    bad = _good_frame()
    bad["bones"]["Hips"]["rot"] = [1.0, 2.0]  # malformed

    with client.websocket_connect(
        "/api/mocap/live?vrm=eve.vrm", cookies=session_cookie
    ) as ws:
        # 2 valid + 1 invalid → 2 accepted, 1 dropped.
        ws.send_text(json.dumps(_good_frame()))
        ws.send_text(json.dumps(bad))
        ws.send_text(json.dumps(_good_frame()))
        ws.close()

    # The server's ``finally`` emits ``mocap.frame.session_ended`` via
    # an ``await bus.emit(...)`` which is scheduled on the loop. In
    # isolation the TestClient join always lets it complete before
    # ``with`` exits, but under the full suite (with other async work
    # in flight) the emit can race with the assert below. Poll for up
    # to 0.5s — long enough to cover the legitimate emit window,
    # short enough that a real bug still trips the assert.
    import time
    for _ in range(25):
        if any(e[0] == "mocap.frame.session_ended" for e in captured_events):
            break
        time.sleep(0.02)

    end_events = [e for e in captured_events if e[0] == "mocap.frame.session_ended"]
    assert len(end_events) == 1
    payload = end_events[0][1]
    assert payload["vrm_file"] == "eve.vrm"
    assert payload["frames_accepted"] == 2
    assert payload["frames_dropped"] == 1
