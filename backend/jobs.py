from threading import Lock
from typing import Dict, Optional
from .models.schemas import Job

_jobs: Dict[str, Job] = {}
_lock = Lock()


def put(job: Job) -> None:
    with _lock:
        _jobs[job.id] = job


def get(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def all_jobs():
    with _lock:
        return list(_jobs.values())
