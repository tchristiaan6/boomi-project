"""Thin HTTP adapter over core for fdatrack.com. No logic lives here.

Run locally:  uvicorn servers.api:app --reload
Deploy:       uvicorn servers.api:app --host 0.0.0.0 --port $PORT

Endpoints:
  GET /health          liveness
  GET /assess/stream   SSE: live trace events, then the full assessment
  POST /assess         blocking JSON (same cache)

Design notes:
- Assessments are cached in memory ~6h by normalized drug name. The
  underlying data updates daily at best, and the cache is why this runs as
  an always-on service rather than serverless.
- ANTHROPIC_API_KEY and FDA_API_KEY are server-side env vars, never exposed.
- A small concurrency gate keeps one noisy user from exhausting the
  openFDA rate budget; this is a feedback deployment, not a product launch.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from core.env import load_env

load_env()

from core.assess import assess
from core.normalize import norm_name

CACHE_TTL_SECONDS = int(os.environ.get("FDATRACK_CACHE_TTL", 6 * 3600))
MAX_CONCURRENT = int(os.environ.get("FDATRACK_MAX_CONCURRENT", "3"))
ASSESS_DEADLINE_SECONDS = 240

ALLOWED_ORIGINS = [
    "https://fdatrack.com",
    "https://www.fdatrack.com",
    "http://localhost:3000",
]

app = FastAPI(title="FDATrack API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()
_gate = threading.Semaphore(MAX_CONCURRENT)


def _cache_get(key: str) -> dict | None:
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
            return hit[1]
        if hit:
            del _cache[key]
    return None


def _cache_put(key: str, value: dict) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), value)


def _validate(drug: str) -> str:
    drug = (drug or "").strip()
    if not 2 <= len(drug) <= 80:
        raise HTTPException(422, "drug name must be 2-80 characters")
    return drug


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _run_assessment(drug: str, key: str, q: queue.Queue) -> None:
    """Worker thread: run the agent, forward trimmed events, then the result.
    Caches here, not in the response generator, so a client who disconnects
    mid-stream still leaves a warm cache behind. The trace events are cached
    with the result so later cache hits can replay the actual work performed
    instead of an unexplained answer."""
    events: list[tuple[str, dict]] = []

    def emit(kind: str, payload: dict) -> None:
        events.append((kind, payload))
        q.put((kind, payload))

    def on_event(kind: str, payload: dict) -> None:
        if kind == "tool_call":
            emit("tool_call", {"name": payload["name"], "input": payload["input"]})
        elif kind == "tool_result":
            prov = payload.get("result", {}).get("provenance", {})
            emit("tool_result", {
                "name": payload["name"],
                "matched": prov.get("total_matched"),
                "returned": prov.get("returned"),
                "warnings": prov.get("warnings", []),
                "error": payload.get("result", {}).get("error"),
            })
        elif kind == "text":
            emit("status", {"message": payload["text"][:300]})

    try:
        result = assess(drug, on_event=on_event)
        _cache_put(key, {
            "assessment": result.model_dump(),
            "events": events,
            "cached_at": time.time(),
        })
        q.put(("__result__", result.model_dump()))
    except Exception as exc:
        # Full detail to server logs; a plain sentence to the user.
        print(f"assessment failed for {drug!r}: {exc!r}", flush=True)
        q.put(("__error__",
               "The assessment failed on this run. This is usually "
               "transient. Try again in a moment."))
    finally:
        q.put(None)


@app.get("/health")
def health() -> dict:
    return {"ok": True, "cached_drugs": len(_cache)}


@app.get("/assess/stream")
def assess_stream(drug: str):
    """SSE stream: tool_call / tool_result / status events while the agent
    works, a 'done' event with the full assessment, or an 'error' event."""
    drug = _validate(drug)
    key = norm_name(drug)

    cached = _cache_get(key)
    if cached is not None:
        def replay():
            for kind, payload in cached.get("events", []):
                yield _sse(kind, payload)
            age_min = max(0, int((time.time() - cached.get("cached_at", 0)) / 60))
            yield _sse("status", {"message": (
                f"assessed {age_min} minute{'s' if age_min != 1 else ''} ago. "
                "FDA's underlying data updates about once a day, so "
                "assessments refresh every 6 hours."
            )})
            yield _sse("done", cached["assessment"])
        return StreamingResponse(replay(), media_type="text/event-stream")

    def generate():
        if not _gate.acquire(blocking=False):
            yield _sse("error", {
                "message": "The service is at capacity right now. "
                           "Try again in a minute."})
            return
        try:
            q: queue.Queue = queue.Queue()
            threading.Thread(
                target=_run_assessment, args=(drug, key, q), daemon=True
            ).start()
            deadline = time.time() + ASSESS_DEADLINE_SECONDS
            result: dict | None = None
            error: str | None = None
            while True:
                if time.time() > deadline:
                    error = "assessment timed out"
                    break
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    break
                kind, payload = item
                if kind == "__result__":
                    result = payload
                elif kind == "__error__":
                    error = payload
                else:
                    yield _sse(kind, payload)
            if result is not None:
                yield _sse("done", result)
            else:
                yield _sse("error", {"message": error or "assessment failed"})
        finally:
            _gate.release()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class AssessRequest(BaseModel):
    drug: str = Field(min_length=2, max_length=80)


@app.post("/assess")
def assess_blocking(req: AssessRequest) -> dict:
    """Non-streaming variant for programmatic use. Same cache."""
    drug = _validate(req.drug)
    key = norm_name(drug)
    cached = _cache_get(key)
    if cached is not None:
        return {"cached": True, "assessment": cached["assessment"]}
    if not _gate.acquire(blocking=False):
        raise HTTPException(429, "at capacity, try again shortly")
    try:
        result = assess(drug).model_dump()
    except Exception as exc:
        print(f"assessment failed for {drug!r}: {exc!r}", flush=True)
        raise HTTPException(
            502, "assessment failed; this is usually transient, try again"
        )
    finally:
        _gate.release()
    _cache_put(key, {"assessment": result, "events": [], "cached_at": time.time()})
    return {"cached": False, "assessment": result}
