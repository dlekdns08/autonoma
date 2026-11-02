#!/usr/bin/env python
"""Text-to-motion server backed by NVIDIA Kimodo (SMPL-X checkpoint).

Runs next to the model on a GPU machine and speaks the contract that
``autonoma.gestures.motion`` expects::

    POST /generate  {"text": "...", "duration_s": 2.5, "fps": 30}
    200 {"fps": 30, "skeleton": "smpl22", "rotations": [[[x,y,z,w] × 22] × T]}

Generation shells out to ``kimodo_gen`` (so Kimodo may live in its own
conda env — point ``KIMODO_GEN`` at that env's binary) with the
``Kimodo-SMPLX-RP-v1`` model, reads the AMASS NPZ it writes and converts
it with :mod:`autonoma.gestures.kimodo`. Kimodo needs ~17 GB VRAM for
local generation (the Llama-3 text encoder; see its docs for CPU
offload) and access to the gated ``meta-llama/Meta-Llama-3-8B-Instruct``
weights via ``hf auth login``. Checkpoints download on first use.

Run::

    KIMODO_GEN=~/miniconda3/envs/kimodo/bin/kimodo_gen \\
    uv run python scripts/motion_server_kimodo.py --port 8090

then on the Autonoma side::

    AUTONOMA_GESTURE_MOTION_BACKEND=http
    AUTONOMA_GESTURE_MOTION_URL=http://<gpu-host>:8090/generate

Environment: ``KIMODO_GEN`` (binary, default ``kimodo_gen`` on PATH),
``KIMODO_MODEL`` (default ``Kimodo-SMPLX-RP-v1``), ``KIMODO_STEPS``
(diffusion steps, default 50 — half of Kimodo's default, plenty for
short gestures), ``KIMODO_TIMEOUT_S`` (default 120).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from autonoma.gestures.kimodo import amass_to_contract
from autonoma.gestures.motion import MAX_DURATION_S

logger = logging.getLogger("motion_server_kimodo")

KIMODO_GEN = os.environ.get("KIMODO_GEN", "kimodo_gen")
KIMODO_MODEL = os.environ.get("KIMODO_MODEL", "Kimodo-SMPLX-RP-v1")
KIMODO_STEPS = int(os.environ.get("KIMODO_STEPS", "50"))
KIMODO_TIMEOUT_S = float(os.environ.get("KIMODO_TIMEOUT_S", "120"))

app = FastAPI(title="Autonoma motion server (Kimodo)")
# Kimodo holds the GPU; one generation at a time.
_lock = asyncio.Lock()


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    duration_s: float = Field(default=2.5, gt=0, le=MAX_DURATION_S)
    fps: int = Field(default=30, ge=1, le=120)
    seed: int | None = None


def load_amass(path: Path) -> tuple[list[list[float]], list[list[float]], float]:
    import numpy as np

    data = np.load(path, allow_pickle=True)
    root = np.asarray(data["root_orient"], dtype=float)
    body = np.asarray(data["pose_body"], dtype=float)
    fps = float(np.asarray(data["mocap_frame_rate"]).item())
    return root.tolist(), body.tolist(), fps


async def run_kimodo(text: str, duration_s: float, seed: int | None, workdir: Path) -> Path:
    stem = workdir / "clip"
    argv = [
        KIMODO_GEN,
        text,
        "--model",
        KIMODO_MODEL,
        "--duration",
        f"{duration_s:.2f}",
        "--diffusion_steps",
        str(KIMODO_STEPS),
        "--output",
        str(stem),
    ]
    if seed is not None:
        argv += ["--seed", str(seed)]
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=str(workdir),
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=KIMODO_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(504, "kimodo_gen timed out")
    if proc.returncode != 0:
        tail = out.decode(errors="replace")[-2000:]
        logger.error("kimodo_gen failed (%s): %s", proc.returncode, tail)
        raise HTTPException(500, "kimodo_gen failed; see server log")
    amass = Path(f"{stem}_amass.npz")
    if not amass.exists():
        # Multiple samples / older layouts write into a folder.
        candidates = sorted(workdir.rglob("*_amass.npz"))
        if not candidates:
            raise HTTPException(500, "kimodo_gen wrote no AMASS NPZ (is the model SMPL-X?)")
        amass = candidates[0]
    return amass


@app.post("/generate")
async def generate(req: GenerateRequest) -> dict[str, Any]:
    async with _lock:
        workdir = Path(tempfile.mkdtemp(prefix="kimodo-"))
        try:
            amass = await run_kimodo(req.text, req.duration_s, req.seed, workdir)
            root, body, src_fps = await asyncio.to_thread(load_amass, amass)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    try:
        return amass_to_contract(root, body, src_fps, fps=req.fps)
    except ValueError as exc:
        raise HTTPException(500, f"unusable AMASS output: {exc}") from exc


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": shutil.which(KIMODO_GEN) is not None or Path(KIMODO_GEN).exists(),
        "model": KIMODO_MODEL,
        "kimodo_gen": KIMODO_GEN,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
