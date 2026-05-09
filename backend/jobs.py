import json
import os
from threading import Lock
from typing import Dict, Optional
from .models.schemas import Job
from . import config

_jobs: Dict[str, Job] = {}
_lock = Lock()
_jobs_dir = config.DATA / "jobs"
_jobs_dir.mkdir(exist_ok=True)


def _job_path(job_id: str) -> str:
    return str(_jobs_dir / f"{job_id}.json")


def put(job: Job) -> None:
    with _lock:
        _jobs[job.id] = job
    _persist(job)


def get(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def save(job: Job) -> None:
    """Call after each pipeline stage to persist current state."""
    _persist(job)


def all_jobs():
    with _lock:
        return list(_jobs.values())


def _persist(job: Job) -> None:
    try:
        with open(_job_path(job.id), "w") as f:
            json.dump(job.to_dict(), f)
    except Exception as e:
        print(f"[jobs] failed to persist {job.id}: {e}", flush=True)


def load_all() -> None:
    """Load all persisted jobs from disk on startup."""
    loaded = 0
    for fname in os.listdir(_jobs_dir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(_jobs_dir, fname)) as f:
                d = json.load(f)
            job = Job.from_dict(d)
            # Jobs that were mid-pipeline when server died -> mark as error
            if job.status in ("detecting", "analyzing", "generating", "applying"):
                job.status = "error"
                job.error = "Server restarted while pipeline was running. Re-upload to retry."
            with _lock:
                _jobs[job.id] = job
            loaded += 1
        except Exception as e:
            print(f"[jobs] failed to load {fname}: {e}", flush=True)
    if loaded:
        print(f"[jobs] loaded {loaded} persisted job(s)", flush=True)
