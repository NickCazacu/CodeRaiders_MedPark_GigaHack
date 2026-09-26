"""Local HTTP service for the audio preprocessing stage.

It deliberately accepts paths in a shared local volume rather than uploading audio
through the API. This keeps large recordings out of n8n execution data and guarantees
that audio never leaves the hospital network.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.config import PreprocessConfig
from app.pipeline.preprocess import preprocess

WORK_ROOT = Path(os.environ.get("MOM_PREPROCESS_WORK_DIR", "data/preprocess-jobs")).resolve()
SOURCE_ROOT = Path(os.environ.get("MOM_SOURCE_ROOT", ".")).resolve()
WORKERS = int(os.environ.get("MOM_PREPROCESS_WORKERS", "1"))
MAX_UPLOAD_BYTES = int(os.environ.get("MOM_PREPROCESS_MAX_UPLOAD_BYTES", str(2 * 1024**3)))
executor = ThreadPoolExecutor(max_workers=max(1, WORKERS), thread_name_prefix="preprocess")

app = FastAPI(title="MedPark preprocessing service", version="0.1.0")


class CreateJob(BaseModel):
    source_path: str = Field(description="Absolute path within MOM_SOURCE_ROOT")
    job_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    options: dict[str, Any] = Field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_dir(job_id: str) -> Path:
    return WORK_ROOT / job_id


def _state_path(job_id: str) -> Path:
    return _job_dir(job_id) / "status.json"


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _read_state(job_id: str) -> dict[str, Any]:
    path = _state_path(job_id)
    if not path.is_file():
        raise KeyError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _source_path(value: str) -> Path:
    path = Path(value).resolve()
    try:
        path.relative_to(SOURCE_ROOT)
    except ValueError as exc:
        raise ValueError(f"source_path must be inside {SOURCE_ROOT}") from exc
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _job_id(value: str | None) -> str:
    if value is None:
        return uuid.uuid4().hex[:16]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("job_id must contain 1-80 letters, numbers, underscores, or hyphens")
    return value


def _validate_options(options: dict[str, Any]) -> None:
    reserved = {"work_dir", "cache"}.intersection(options)
    if reserved:
        names = ", ".join(sorted(reserved))
        raise ValueError(f"service controls option(s): {names}")


def _submit_job(job_id: str, source: Path, options: dict[str, Any]) -> dict[str, str]:
    _validate_options(options)
    if _state_path(job_id).exists():
        raise FileExistsError(f"job already exists: {job_id}")
    state = {"job_id": job_id, "state": "queued", "created_at": _now(), "source_path": str(source)}
    _write_json(_state_path(job_id), state)
    executor.submit(_run_job, job_id, source, options)
    return {"job_id": job_id, "status_url": f"/jobs/{job_id}", "result_url": f"/jobs/{job_id}/result"}


def _run_job(job_id: str, source: Path, options: dict[str, Any]) -> None:
    state = _read_state(job_id)
    state.update(state="running", started_at=_now())
    _write_json(_state_path(job_id), state)
    try:
        # Each job has its own artefacts. Cache is intentionally disabled: cache keys
        # are a local optimisation, while service jobs must remain independently auditable.
        cfg = PreprocessConfig(work_dir=_job_dir(job_id), cache=False, **options)
        result = preprocess(source, cfg, job_id=job_id)
        result_path = _job_dir(job_id) / "result.json"
        payload = result.model_dump(mode="json")
        # The result directory is a shared volume, but its mount point is different in
        # the service container and on the MedPark host. Keep this reference portable.
        # The consumer resolves it relative to result.json.
        if payload.get("wav_path"):
            payload["wav_path"] = Path(payload["wav_path"]).name
        _write_json(result_path, payload)
        state.update(
            state="done",
            finished_at=_now(),
            result_path=str(result_path),
            speech_duration_s=result.speech_duration_s,
            chunks=len(result.chunks),
        )
    except Exception as exc:  # state must make failures visible to n8n polling
        state.update(state="failed", finished_at=_now(), error=str(exc))
    _write_json(_state_path(job_id), state)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs", status_code=202)
def create_job(request: CreateJob) -> dict[str, str]:
    try:
        source = _source_path(request.source_path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        return _submit_job(_job_id(request.job_id), source, request.options)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/jobs/upload", status_code=202)
async def create_upload_job(
    audio: UploadFile = File(...),
    job_id: str | None = Form(default=None),
    options_json: str = Form(default="{}"),
) -> dict[str, str]:
    """Stage an n8n multipart upload privately, then enqueue the normal job."""
    try:
        options = json.loads(options_json)
        if not isinstance(options, dict):
            raise ValueError("options_json must contain a JSON object")
        safe_job_id = _job_id(job_id)
        _validate_options(options)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _state_path(safe_job_id).exists():
        raise HTTPException(status_code=409, detail=f"job already exists: {safe_job_id}")

    filename = Path(audio.filename or "meeting-audio").name
    if not filename or filename in {".", ".."}:
        filename = "meeting-audio"
    source = _job_dir(safe_job_id) / "source" / filename
    total = 0
    try:
        source.parent.mkdir(parents=True, exist_ok=True)
        with source.open("wb") as target:
            while chunk := await audio.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError(f"upload exceeds the {MAX_UPLOAD_BYTES} byte limit")
                target.write(chunk)
        return _submit_job(safe_job_id, source, options)
    except ValueError as exc:
        source.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    finally:
        await audio.close()


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    try:
        return _read_state(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@app.get("/jobs/{job_id}/result")
def get_result(job_id: str) -> dict[str, Any]:
    try:
        state = _read_state(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc
    if state["state"] != "done":
        raise HTTPException(status_code=409, detail=f"job is {state['state']}")
    return json.loads(Path(state["result_path"]).read_text(encoding="utf-8"))
