from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

from humanonn.persistence import HumanonnPersistence
persistence = HumanonnPersistence.from_env()


def _scan_mode(url: str, repo_url: str | None) -> str:
    has_live_url = bool((url or "").strip())
    has_repo_url = bool((repo_url or "").strip())
    if has_live_url and has_repo_url:
        return "combined"
    if has_live_url:
        return "live_only"
    if has_repo_url:
        return "source_only"
    return "invalid"


def _is_source_scan_log_line(line: str) -> bool:
    prefixes = (
        "Starting source-code scan",
        "Live site scraping disabled by HUMANONN_LIVE_SITE_SCRAPING=false; using source-code scoring only.",
        "No GitHub repo URL was provided, so source-code scoring could not run.",
        "Fetched ",
        "Checked source rule ",
        "Computed raw source code score ",
        "Added normalized source code score ",
        "Boosted DOM confidence to 1.0 from source-code agreement:",
        "Applied ATS source review via ",
    )
    return line.startswith(prefixes)


def _scan_stdout_reader(process: subprocess.Popen[str], output_queue: queue.Queue[tuple[str, str | int]]) -> None:
    try:
        assert process.stdout is not None
        for line in iter(process.stdout.readline, ""):
            if line:
                output_queue.put(("line", line.rstrip()))
        return_code = process.wait()
        output_queue.put(("done", return_code))
    except Exception as exc:
        output_queue.put(("error", str(exc)))


class ScanRequest(BaseModel):
    url: str = ""
    repo_url: str | None = None


@dataclass
class ScanJob:
    job_id: str
    url: str
    repo_url: str | None
    mode: str
    created_at: float
    output_path: Path
    process: subprocess.Popen[str] | None
    output_queue: queue.Queue[tuple[str, str | int]]
    reader_thread: threading.Thread | None
    live_logs: list[str] = field(default_factory=list)
    source_logs: list[str] = field(default_factory=list)
    done: bool = False
    return_code: int | None = None
    error: str | None = None
    report: dict[str, Any] | None = None
    screenshot_bytes: bytes | None = None
    artifact_bundle_bytes: bytes | None = None
    manifest_json: dict[str, Any] | None = None


REPORTS_DIR = Path(os.getenv("HUMANONN_WORKER_REPORTS_DIR", "/tmp/humanonn-worker-reports"))
REPORTS_DIR.mkdir(parents=True, exist_ok=True)
MAX_CONCURRENT_SCANS = int(os.getenv("HUMANONN_MAX_CONCURRENT_SCANS", "1"))

_JOBS: dict[str, ScanJob] = {}
_LOCK = threading.Lock()

app = FastAPI(title="Humanonn Worker", version="1.0")


def _running_jobs_count() -> int:
    return sum(1 for job in _JOBS.values() if not job.done and job.process and job.process.poll() is None)


def _drain_job_output(job: ScanJob) -> None:
    if job.done:
        return
    while True:
        try:
            kind, payload = job.output_queue.get_nowait()
        except queue.Empty:
            break
        if kind == "line":
            line = str(payload)
            if job.mode == "source_only" or _is_source_scan_log_line(line):
                job.source_logs.append(line)
            else:
                job.live_logs.append(line)
        elif kind == "error":
            job.error = str(payload)
            if job.mode == "source_only":
                job.source_logs.append(f"ERROR: {payload}")
            else:
                job.live_logs.append(f"ERROR: {payload}")
            job.done = True
        elif kind == "done":
            job.return_code = int(payload)
            job.done = True

    if job.done and job.report is None:
        if job.output_path.exists():
            try:
                job.report = json.loads(job.output_path.read_text(encoding="utf-8"))
            except Exception as exc:
                job.error = job.error or f"Could not parse report JSON: {str(exc).splitlines()[0]}"
        elif job.return_code not in (0, None):
            job.error = job.error or f"Scan failed with exit code {job.return_code}."


def _start_job(url: str, repo_url: str | None) -> ScanJob:
    mode = _scan_mode(url, repo_url)
    if mode == "invalid":
        raise HTTPException(status_code=400, detail="Provide a URL and/or a public GitHub repo URL.")

    timestamp = int(time.time())
    job_id = uuid.uuid4().hex
    out_dir = REPORTS_DIR / f"scan_{timestamp}_{job_id[:8]}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "site.json"

    cmd = [sys.executable, "-u", "-m", "humanonn", "scan", "--json", str(out_json)]
    env = os.environ.copy()
    if mode == "source_only":
        env["HUMANONN_LIVE_SITE_SCRAPING"] = "false"
        cmd.extend(["--no-llm", "--source-only"])
    else:
        cmd.insert(5, url.strip())
    if repo_url and repo_url.strip():
        cmd.extend(["--repo-url", repo_url.strip()])

    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    process = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        creationflags=creationflags,
    )

    output_queue: queue.Queue[tuple[str, str | int]] = queue.Queue()
    reader = threading.Thread(target=_scan_stdout_reader, args=(process, output_queue), daemon=True)
    reader.start()

    job = ScanJob(
        job_id=job_id,
        url=url,
        repo_url=repo_url,
        mode=mode,
        created_at=time.time(),
        output_path=out_json,
        process=process,
        output_queue=output_queue,
        reader_thread=reader,
    )
    return job


def _background_jobs_monitor() -> None:
    while True:
        try:
            time.sleep(2)
            with _LOCK:
                active_job_ids = list(_JOBS.keys())
                for job_id in active_job_ids:
                    job = _JOBS[job_id]
                    _drain_job_output(job)

                    if job.done:
                        if persistence.enabled:
                            try:
                                persistence.sync_artifacts(job)
                                persistence.upsert_job(job)
                            except Exception as e:
                                print(f"Failed to sync artifacts for completed job {job_id}: {e}", file=sys.stderr)
                        # Remove completed jobs from active memory dict
                        _JOBS.pop(job_id, None)
                    else:
                        if persistence.enabled:
                            try:
                                persistence.upsert_job(job)
                            except Exception as e:
                                print(f"Failed to update running job logs in DB: {e}", file=sys.stderr)
        except Exception as exc:
            print(f"Error in background jobs monitor: {exc}", file=sys.stderr)


@app.on_event("startup")
def startup_event():
    if persistence.enabled:
        try:
            persistence.ensure_schema()
            persistence.job_store.cleanup_stuck_jobs()
            print("Postgres database schema checked and stuck jobs cleaned up.", file=sys.stderr)
        except Exception as e:
            print(f"Failed to initialize database: {e}", file=sys.stderr)

    monitor_thread = threading.Thread(target=_background_jobs_monitor, daemon=True)
    monitor_thread.start()
    print("Background jobs monitor thread started.", file=sys.stderr)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "humanonn-worker", "status": "ok"}


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/scan")
def create_scan(request: ScanRequest) -> dict[str, Any]:
    with _LOCK:
        if _running_jobs_count() >= MAX_CONCURRENT_SCANS:
            raise HTTPException(status_code=429, detail="Worker is busy. Try again in a moment.")
        job = _start_job(request.url, request.repo_url)
        _JOBS[job.job_id] = job
        if persistence.enabled:
            try:
                persistence.upsert_job(job)
            except Exception as e:
                print(f"Failed to save job initialization to database: {e}", file=sys.stderr)
    return {"job_id": job.job_id, "mode": job.mode, "status": "running"}


@app.get("/scan/{job_id}")
def get_scan(job_id: str) -> dict[str, Any]:
    job = _JOBS.get(job_id)
    if not job:
        if persistence.enabled:
            try:
                db_job = persistence.load_job(job_id)
                if db_job:
                    return {
                        "job_id": db_job.get("job_id"),
                        "mode": db_job.get("mode"),
                        "done": db_job.get("status") in ("done", "failed"),
                        "return_code": db_job.get("return_code"),
                        "error": db_job.get("error"),
                        "live_logs": db_job.get("live_logs"),
                        "source_logs": db_job.get("source_logs"),
                        "report": db_job.get("report"),
                        "site_json": db_job.get("site_json"),
                        "manifest_json": db_job.get("manifest_json"),
                    }
            except Exception as e:
                print(f"Failed to load job from database: {e}", file=sys.stderr)
        raise HTTPException(status_code=404, detail="Scan job not found.")

    with _LOCK:
        _drain_job_output(job)
        return {
            "job_id": job.job_id,
            "mode": job.mode,
            "done": job.done,
            "return_code": job.return_code,
            "error": job.error,
            "live_logs": job.live_logs,
            "source_logs": job.source_logs,
            "report": job.report,
            "site_json": job.report,
            "manifest_json": job.manifest_json,
        }


@app.post("/scan/{job_id}/cancel")
def cancel_scan(job_id: str) -> dict[str, Any]:
    job = _JOBS.get(job_id)
    if job:
        with _LOCK:
            if not job.done and job.process and job.process.poll() is None:
                try:
                    job.process.terminate()
                    try:
                        job.process.wait(timeout=5)
                    except Exception:
                        job.process.kill()
                finally:
                    job.done = True
                    job.return_code = job.process.poll()
                    job.error = job.error or "Scan cancelled by user."
            _drain_job_output(job)
            if persistence.enabled:
                try:
                    persistence.sync_artifacts(job)
                    persistence.upsert_job(job)
                except Exception as e:
                    print(f"Failed to upsert cancelled job: {e}", file=sys.stderr)
        return {"job_id": job.job_id, "done": job.done, "status": "cancelled"}

    if persistence.enabled:
        try:
            db_job = persistence.load_job(job_id)
            if db_job:
                status = db_job.get("status")
                if status in ("running", "queued"):
                    sql = f"""
                    UPDATE {persistence.job_store.TABLE_NAME}
                    SET status = 'failed',
                        error = 'Scan cancelled by user.',
                        done_at = NOW(),
                        updated_at = NOW()
                    WHERE job_id = %s
                    """
                    with persistence.job_store._connect() as conn:
                        with conn.cursor() as cur:
                            cur.execute(sql, (job_id,))
                        conn.commit()
                return {"job_id": job_id, "done": True, "status": "cancelled"}
        except Exception as e:
            print(f"Failed to cancel db job: {e}", file=sys.stderr)

    raise HTTPException(status_code=404, detail="Scan job not found.")


@app.get("/scan/{job_id}/screenshot")
def get_job_screenshot(job_id: str):
    job = _JOBS.get(job_id)
    screenshot_bytes = None
    if job:
        screenshot_bytes = getattr(job, "screenshot_bytes", None)
        if not screenshot_bytes and job.done and job.report:
            screenshot_path = persistence._resolve_path(job.report.get("screenshot_path"))
            if screenshot_path and screenshot_path.exists():
                try:
                    screenshot_bytes = screenshot_path.read_bytes()
                except Exception:
                    pass

    if not screenshot_bytes and persistence.enabled:
        try:
            db_job = persistence.job_store.load(job_id)
            if db_job:
                screenshot_bytes = db_job.get("screenshot_bytes")
        except Exception:
            pass

    if not screenshot_bytes:
        raise HTTPException(status_code=404, detail="Screenshot not found.")

    return Response(content=screenshot_bytes, media_type="image/png")


@app.get("/scan/{job_id}/artifacts")
def get_job_artifacts(job_id: str):
    job = _JOBS.get(job_id)
    bundle_bytes = None
    if job:
        bundle_bytes = getattr(job, "artifact_bundle_bytes", None)

    if not bundle_bytes and persistence.enabled:
        try:
            db_job = persistence.job_store.load(job_id)
            if db_job:
                bundle_bytes = db_job.get("artifact_bundle_bytes")
        except Exception:
            pass

    if not bundle_bytes:
        raise HTTPException(status_code=404, detail="Artifact bundle not found.")

    return Response(
        content=bundle_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=artifacts_{job_id}.zip"}
    )