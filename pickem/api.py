"""FastAPI wrapper for the pickem predictor.

The simulator is intentionally CPU/network heavy, so the default web
flow starts a background job and lets the frontend poll for completion.
"""

from __future__ import annotations

import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from hltv_api.client import HLTVError
from pickem.service import (
    PredictionError,
    PredictionOptions,
    event_status,
    parse_cutoff,
    predict_event,
)


MAX_SIMS = 200_000
_executor = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = Lock()


app = FastAPI(
    title="HLTV Pickem Predictor",
    version="0.1.0",
    description="Swiss-stage pickem prediction API backed by the HLTV mobile API.",
)


class PredictionRequest(BaseModel):
    n_sims: int = Field(30_000, ge=100, le=MAX_SIMS)
    seed: int = 42
    top_k: int = Field(5, ge=1, le=25)
    cutoff: str | None = Field(
        default=None,
        description="ISO datetime. Defaults to the event pickem deadline or first match time.",
    )
    force_team_ids: list[int] = Field(default_factory=list)
    force_stage_records: dict[str, Any] = Field(
        default_factory=dict,
        description='Team records keyed by team id. Values can be "3-2", [3, 2], or {"wins": 3, "losses": 2}.',
    )
    allow_random_r1: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_record(value: Any) -> tuple[int, int]:
    if isinstance(value, str):
        parts = value.replace(":", "-").split("-")
        if len(parts) != 2:
            raise ValueError(f"invalid record {value!r}; expected W-L")
        wins, losses = int(parts[0]), int(parts[1])
    elif isinstance(value, (list, tuple)) and len(value) == 2:
        wins, losses = int(value[0]), int(value[1])
    elif isinstance(value, dict):
        wins = int(value.get("wins"))
        losses = int(value.get("losses"))
    else:
        raise ValueError(f"invalid record {value!r}; expected W-L, [W,L], or object")
    if wins < 0 or losses < 0 or wins > 3 or losses > 3:
        raise ValueError(f"invalid record {wins}-{losses}; wins/losses must be in 0..3")
    return wins, losses


def _to_options(request: PredictionRequest) -> PredictionOptions:
    records: dict[int, tuple[int, int]] = {}
    for raw_tid, raw_record in request.force_stage_records.items():
        try:
            tid = int(raw_tid)
            records[tid] = _parse_record(raw_record)
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        cutoff = parse_cutoff(request.cutoff)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid cutoff: {request.cutoff!r}") from exc

    return PredictionOptions(
        n_sims=request.n_sims,
        seed=request.seed,
        top_k=request.top_k,
        cutoff=cutoff,
        force_team_ids=tuple(dict.fromkeys(request.force_team_ids)),
        force_stage_records=records,
        allow_random_r1=request.allow_random_r1,
    )


def _set_job(job_id: str, **updates: Any) -> None:
    with _jobs_lock:
        job = _jobs[job_id]
        job.update(updates)
        job["updated_at"] = _now()


def _run_prediction_job(job_id: str, event_id: int, request: PredictionRequest) -> None:
    _set_job(job_id, status="running")
    try:
        result = predict_event(event_id, _to_options(request))
    except PredictionError as exc:
        _set_job(
            job_id,
            status="failed",
            error={"message": str(exc), "status": exc.status, "details": exc.details},
        )
    except HLTVError as exc:
        _set_job(
            job_id,
            status="failed",
            error={"message": str(exc), "status": "hltv_error", "details": {"http_status": exc.status}},
        )
    except Exception as exc:
        _set_job(
            job_id,
            status="failed",
            error={
                "message": str(exc),
                "status": "unexpected_error",
                "details": {"traceback": traceback.format_exc(limit=6)},
            },
        )
    else:
        _set_job(job_id, status="succeeded", result=result)


@app.get("/")
def root() -> dict[str, Any]:
    return {
        "name": "HLTV Pickem Predictor",
        "endpoints": {
            "health": "/health",
            "event_status": "/events/{event_id}/status",
            "start_prediction": "/events/{event_id}/predict",
            "sync_prediction": "/events/{event_id}/predict/sync",
            "job": "/jobs/{job_id}",
        },
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "time": _now(), "max_sims": MAX_SIMS}


@app.get("/events/{event_id}/status")
def get_event_status(
    event_id: int,
    force_team_ids: list[int] = Query(default_factory=list),
    allow_random_r1: bool = True,
) -> dict[str, Any]:
    try:
        return event_status(
            event_id,
            force_team_ids=tuple(dict.fromkeys(force_team_ids)),
            allow_random_r1=allow_random_r1,
        )
    except HLTVError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/events/{event_id}/predict")
def start_prediction(event_id: int, request: PredictionRequest) -> dict[str, Any]:
    job_id = uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "job_id": job_id,
            "event_id": event_id,
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "request": request.model_dump(),
            "result": None,
            "error": None,
        }
    _executor.submit(_run_prediction_job, job_id, event_id, request)
    return {"job_id": job_id, "status": "queued", "job_url": f"/jobs/{job_id}"}


@app.post("/events/{event_id}/predict/sync")
def run_prediction_sync(event_id: int, request: PredictionRequest) -> dict[str, Any]:
    try:
        return predict_event(event_id, _to_options(request))
    except PredictionError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "status": exc.status, "details": exc.details}) from exc
    except HLTVError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)
