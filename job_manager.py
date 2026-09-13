#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small thread-safe job registry for the local desktop HTTP API."""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable

import applog

log = applog.get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JobConflictError(RuntimeError):
    pass


class JobCancelled(RuntimeError):
    """Raised inside a worker (through ``update``) once the job was cancelled or timed out."""


class _CommitGate:
    """Context manager handed to workers as ``update.commit()``.

    A worker must wrap every durable side effect (files, ledger, dashboard)
    in ``with update.commit():``. Entering the gate takes the manager lock and
    re-checks that this job is still the running owner; a cancelled or timed
    out job raises ``JobCancelled`` before writing anything. The lock is held
    for the whole block, so ``cancel`` cannot interleave with the writes and a
    later job can never be overwritten by an earlier, already cancelled one.
    """

    def __init__(self, manager: "JobManager", job_id: str):
        self._manager = manager
        self._job_id = job_id
        self._held = False

    def __enter__(self):
        self._manager._lock.acquire()
        self._held = True
        try:
            job = self._manager._jobs[self._job_id]
            self._manager._expire_locked(job)
            if job["status"] != "running" or job.get("cancel_requested"):
                raise JobCancelled(
                    (job.get("error") or {}).get("message") or "작업이 취소되었습니다."
                )
        except BaseException:
            self._manager._lock.release()
            self._held = False
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._held:
            self._manager._lock.release()
            self._held = False
        return False


# Seconds a job may run before it is reported as failed. The worker thread
# cannot be killed; it keeps running until its next ``update`` call, and its
# result is discarded. The narrative kind waits on a local model, so it gets
# the longest budget.
DEFAULT_TIMEOUTS = {
    "chronicle": 2400.0,
    "check": 600.0,
    "narrative": 1800.0,
    "feed": 1800.0,
    "feed_articles": 1800.0,
    "feed_community": 1800.0,
}
DEFAULT_TIMEOUT = 900.0
ACTIVE_STATUSES = ("queued", "running")


class JobManager:
    def __init__(self, keep: int = 40, timeouts: dict | None = None):
        self.keep = max(10, keep)
        self.timeouts = dict(DEFAULT_TIMEOUTS)
        if timeouts:
            self.timeouts.update(timeouts)
        self._jobs: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.RLock()

    def _timeout_for(self, kind: str) -> float:
        try:
            return max(1.0, float(self.timeouts.get(kind, DEFAULT_TIMEOUT)))
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT

    def start(self, kind: str, worker: Callable, *args, **kwargs) -> dict:
        with self._lock:
            for job in self._jobs.values():
                self._expire_locked(job)
            active = next(
                (job for job in self._jobs.values() if job["status"] in ACTIVE_STATUSES),
                None,
            )
            if active:
                raise JobConflictError(active["id"])
            job_id = uuid.uuid4().hex
            timeout = self._timeout_for(kind)
            job = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "progress": 0,
                "phase": "대기 중",
                "logs": [],
                "result": None,
                "error": None,
                "created_at": _now(),
                "started_at": None,
                "finished_at": None,
                "timeout_seconds": timeout,
                "cancel_requested": False,
                "_deadline": time.monotonic() + timeout,
            }
            self._jobs[job_id] = job
            self._trim_locked()
        thread = threading.Thread(
            target=self._run,
            args=(job_id, worker, args, kwargs),
            name=f"StarModeFeed-{kind}-{job_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.get(job_id)

    def _run(self, job_id: str, worker: Callable, args: tuple, kwargs: dict) -> None:
        with self._lock:
            job = self._jobs[job_id]
            # Only a still-queued job may start. A job cancelled (or expired)
            # while it was waiting for its thread must never be resurrected to
            # "running": it would pass the commit gate and overwrite a newer
            # job's output (follow-up review finding 2026-09-02, P2).
            self._expire_locked(job)
            if job["status"] != "queued" or job.get("cancel_requested"):
                log.info("job %s (%s) not started: %s", job_id[:8], job["kind"], job["status"])
                return
            job["status"] = "running"
            job["started_at"] = _now()

        def check_cancelled() -> None:
            """Check ownership without adding a log row or acquiring the state lock."""
            with self._lock:
                current = self._jobs[job_id]
                self._expire_locked(current)
                if current["status"] not in ACTIVE_STATUSES or current.get("cancel_requested"):
                    raise JobCancelled(
                        (current.get("error") or {}).get("message") or "작업이 취소되었습니다."
                    )

        def update(message: str, progress: int | None = None, phase: str | None = None) -> None:
            with self._lock:
                current = self._jobs[job_id]
                self._expire_locked(current)
                if current["status"] not in ACTIVE_STATUSES or current.get("cancel_requested"):
                    # Cancelled or timed out: stop the worker at its next
                    # progress report instead of letting it keep writing.
                    raise JobCancelled(
                        (current.get("error") or {}).get("message") or "작업이 취소되었습니다."
                    )
                current["logs"].append(
                    {"time": time.strftime("%H:%M:%S"), "message": str(message)}
                )
                current["logs"] = current["logs"][-120:]
                if progress is not None:
                    current["progress"] = max(0, min(100, int(progress)))
                if phase:
                    current["phase"] = phase

        # Workers wrap durable writes in ``with update.commit():`` so that a
        # cancelled or timed-out worker returning late can never overwrite a
        # newer job's output (review finding 2026-09-02, P1 #2).
        update.commit = lambda: _CommitGate(self, job_id)
        update.check_cancelled = check_cancelled
        update.job_id = job_id

        try:
            result = worker(update, *args, **kwargs)
            with self._lock:
                job = self._jobs[job_id]
                if job["status"] != "running":
                    # Cancelled or timed out while the worker was still busy;
                    # the late result is discarded.
                    log.info("job %s (%s) finished after %s; result discarded", job_id[:8], job["kind"], job["status"])
                    return
                job["result"] = result
                job["status"] = "completed"
                job["progress"] = 100
                job["phase"] = "완료"
                job["finished_at"] = _now()
        except JobCancelled:
            log.info("job %s (%s) stopped after cancellation", job_id[:8], job["kind"])
        except Exception as exc:
            log.exception("job %s (%s) failed: %s", job_id[:8], job["kind"], exc)
            with self._lock:
                job = self._jobs[job_id]
                if job["status"] != "running":
                    return
                job["status"] = "failed"
                job["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "trace": traceback.format_exc(limit=8),
                }
                job["phase"] = "실패"
                job["finished_at"] = _now()

    def _expire_locked(self, job: dict) -> None:
        if job["status"] in ACTIVE_STATUSES and time.monotonic() > float(job.get("_deadline") or 0):
            job["status"] = "failed"
            job["error"] = {
                "type": "JobTimeout",
                "message": f"작업 시간이 {int(job.get('timeout_seconds') or 0)}초를 넘어 중단했습니다.",
            }
            job["phase"] = "시간 초과"
            job["finished_at"] = _now()
            log.warning("job %s (%s) timed out", job["id"][:8], job["kind"])

    def cancel(self, job_id: str) -> dict | None:
        """Mark an active job cancelled. The worker stops at its next update."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job["cancel_requested"] = True
            if job["status"] in ACTIVE_STATUSES:
                job["status"] = "cancelled"
                job["error"] = {"type": "JobCancelled", "message": "사용자가 작업을 취소했습니다."}
                job["phase"] = "취소됨"
                job["finished_at"] = _now()
                log.info("job %s (%s) cancelled by user", job_id[:8], job["kind"])
            return self._copy(job)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                self._expire_locked(job)
            return self._copy(job) if job else None

    def latest(self) -> dict | None:
        with self._lock:
            if not self._jobs:
                return None
            job = next(reversed(self._jobs.values()))
            self._expire_locked(job)
            return self._copy(job)

    def _trim_locked(self) -> None:
        while len(self._jobs) > self.keep:
            first_id, first = next(iter(self._jobs.items()))
            if first["status"] in ACTIVE_STATUSES:
                break
            self._jobs.pop(first_id)

    @staticmethod
    def _copy(job: dict | None) -> dict | None:
        if job is None:
            return None
        public = {key: value for key, value in job.items() if not key.startswith("_")}
        return {
            **public,
            "logs": [dict(row) for row in job["logs"]],
            "result": job["result"],
            "error": dict(job["error"]) if job.get("error") else None,
        }
