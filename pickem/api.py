import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import web
from .predict import predict_event

MAX_SIMS = 200_000
executor = ThreadPoolExecutor(max_workers=1)
jobs = {}
jobs_lock = Lock()

app = FastAPI(title="CS2 Pick'Em Predictor", version="0.2.0", description="Swiss-stage Pick'Em forecasts from public HLTV pages.")


class PredictionRequest(BaseModel):
    sims: int = Field(20_000, ge=100, le=MAX_SIMS)
    pair_sims: int = Field(1000, ge=50, le=5000)
    goal: int = Field(5, ge=1, le=10)
    seed: int = 1
    stage: int | None = Field(default=None, description="1-based stage number for a multi-stage event page.")
    playoffs: bool = False
    update: bool = Field(default=False, description="Read new results from HLTV before predicting.")
    cutoff: str | None = Field(default=None, description="ISO datetime; only matches before it feed the ratings.")


def now():
    return datetime.now(timezone.utc).isoformat()


def run(event_id, request):
    result, _, _ = predict_event(event_id, request.stage, request.playoffs, request.update, request.cutoff, request.sims, request.pair_sims, request.goal, request.seed)
    return result


def run_job(job_id, event_id, request):
    with jobs_lock:
        jobs[job_id].update(status="running", updated_at=now())
    try:
        result = run(event_id, request)
    except BaseException as exc:
        with jobs_lock:
            jobs[job_id].update(status="failed", error={"message": str(exc), "traceback": traceback.format_exc(limit=6)}, updated_at=now())
    else:
        with jobs_lock:
            jobs[job_id].update(status="succeeded", result=result, updated_at=now())


@app.get("/")
def root():
    return {"name": "CS2 Pick'Em Predictor", "endpoints": ["/health", "/events/{event_id}/status", "/events/{event_id}/predict", "/events/{event_id}/predict/sync", "/jobs/{job_id}"]}


@app.get("/health")
def health():
    return {"status": "ok", "time": now(), "max_sims": MAX_SIMS}


@app.get("/events/{event_id}/status")
def event_status(event_id: int):
    try:
        event = web.parse_event(web.Site().get(f"/events/{event_id}/-"), event_id)
    except web.SiteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "event": event["name"], "start": event["start"], "formats": event["formats"],
        "stages": [
            {"name": stage["name"], "teams_known": len(stage["seeds"]), "official_seeds": stage["official_seeds"], "ready": len(stage["seeds"]) == 16,
             "matches_listed": len(stage["matches"]), "matches_played": sum(1 for match in stage["matches"] if match["winner"] is not None),
             "teams": [stage["teams"].get(team_id) for team_id in stage["seeds"]]}
            for stage in event["stages"]
        ],
    }


@app.post("/events/{event_id}/predict")
def start_prediction(event_id: int, request: PredictionRequest):
    job_id = uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"job_id": job_id, "event_id": event_id, "status": "queued", "created_at": now(), "updated_at": now(),
                        "request": request.model_dump(), "result": None, "error": None}
    executor.submit(run_job, job_id, event_id, request)
    return {"job_id": job_id, "status": "queued", "job_url": f"/jobs/{job_id}"}


@app.post("/events/{event_id}/predict/sync")
def run_prediction_sync(event_id: int, request: PredictionRequest):
    try:
        return run(event_id, request)
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except web.SiteError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return dict(job)
