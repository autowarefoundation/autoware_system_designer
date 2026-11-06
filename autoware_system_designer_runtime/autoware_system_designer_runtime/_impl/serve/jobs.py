# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Supervised subprocess jobs with a bounded log and their event stream.

A job is one child process in its own process group, stdout and stderr merged into a log ring of
numbered lines. At most one job per key is active; a finished job stays listed until evicted by
newer finished jobs. Events: ``job`` (job view on every state change) and ``job.log`` (contiguous
line batches ``{"job", "first_seq", "lines"}``).
"""

from __future__ import annotations

import asyncio
import collections
import logging
import os
import signal
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from . import api
from .events import EventHub

logger = logging.getLogger(__name__)

TOPIC_JOB = "job"
TOPIC_JOB_LOG = "job.log"

STATE_RUNNING = "running"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
FINAL_STATES = frozenset({STATE_SUCCEEDED, STATE_FAILED, STATE_CANCELLED})

LOG_RING_LINES = 5000
MAX_LINE_CHARS = 4096
READ_CHUNK_BYTES = 64 * 1024
LOG_FLUSH_INTERVAL_S = 0.2
LOG_BATCH_LINES = 200
FINISHED_JOBS_KEPT = 20
# Cancel escalation: SIGINT, then SIGTERM, then SIGKILL to the job's process group.
CANCEL_TERM_AFTER_S = 10.0
CANCEL_KILL_AFTER_S = 5.0
SHUTDOWN_WAIT_S = 5.0


@dataclass
class JobSpec:
    """What to run; hooks run on the event loop and may fill ``job.detail``."""

    key: str
    kind: str
    title: str
    argv: list[str]
    cwd: Path
    env: Optional[dict[str, str]] = None
    params: dict[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    on_line: Optional[Callable[["Job", str], None]] = None
    on_exit: Optional[Callable[["Job"], None]] = None


class LogRing:
    """Last ``capacity`` lines with daemon-independent, gap-free sequence numbers starting at 1."""

    def __init__(self, capacity: int = LOG_RING_LINES) -> None:
        self._lines: collections.deque[str] = collections.deque(maxlen=capacity)
        self.last_seq = 0

    @property
    def first_seq(self) -> int:
        return self.last_seq - len(self._lines) + 1

    def append(self, line: str) -> int:
        self._lines.append(line)
        self.last_seq += 1
        return self.last_seq

    def since(self, seq: int, limit: Optional[int] = None) -> tuple[int, list[str]]:
        """(first returned seq, lines) after ``seq``; lines already evicted are skipped."""
        start = max(seq + 1, self.first_seq)
        skip = start - self.first_seq
        count = len(self._lines) - skip
        if limit is not None:
            count = min(count, limit)
        return start, [self._lines[skip + i] for i in range(max(count, 0))]


class Job:
    def __init__(self, job_id: str, spec: JobSpec) -> None:
        self.id = job_id
        self.spec = spec
        self.state = STATE_RUNNING
        self.exit_code: Optional[int] = None
        self.error: Optional[str] = None
        self.created_at = time.time()
        self.finished_at: Optional[float] = None
        self.cancel_requested = False
        self.log = LogRing()
        self.detail: dict[str, Any] = dict(spec.detail)
        self.process: Optional[asyncio.subprocess.Process] = None
        self.done = asyncio.Event()

    @property
    def active(self) -> bool:
        return self.state not in FINAL_STATES

    def to_view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.spec.key,
            "kind": self.spec.kind,
            "title": self.spec.title,
            "params": self.spec.params,
            "argv": self.spec.argv,
            "cwd": str(self.spec.cwd),
            "state": self.state,
            "cancel_requested": self.cancel_requested,
            "exit_code": self.exit_code,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "log": {"first_seq": self.log.first_seq, "last_seq": self.log.last_seq},
            "detail": self.detail,
        }


class JobRunner:
    def __init__(self, hub: EventHub, *, finished_kept: int = FINISHED_JOBS_KEPT) -> None:
        self._hub = hub
        self._finished_kept = finished_kept
        self._jobs: dict[str, Job] = {}
        self._active_by_key: dict[str, Job] = {}
        self._counter = 0
        self._tasks: set[asyncio.Task] = set()

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda job: job.created_at, reverse=True)

    def get(self, job_id: str) -> Job:
        job = self._jobs.get(job_id)
        if job is None:
            raise api.ApiError(api.NOT_FOUND, f"no job {job_id!r}")
        return job

    def active_for(self, key: str) -> Optional[Job]:
        return self._active_by_key.get(key)

    async def start(self, spec: JobSpec) -> Job:
        running = self._active_by_key.get(spec.key)
        if running is not None:
            raise api.ApiError(api.CONFLICT, f"job {running.id} is still running", {"job": running.id})
        self._counter += 1
        job = Job(f"{spec.kind}-{self._counter}", spec)
        env = dict(os.environ if spec.env is None else spec.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            job.process = await asyncio.create_subprocess_exec(
                *spec.argv,
                cwd=str(spec.cwd),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            raise api.ApiError(api.UNAVAILABLE, f"cannot start {spec.argv[0]!r}: {exc}") from exc
        self._jobs[job.id] = job
        self._active_by_key[spec.key] = job
        self._evict_finished()
        self._publish_state(job)
        task = asyncio.ensure_future(self._supervise(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        if not job.active or job.cancel_requested:
            return job
        job.cancel_requested = True
        self._signal(job, signal.SIGINT)
        self._publish_state(job)
        task = asyncio.ensure_future(self._escalate(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def shutdown(self, wait_s: float = SHUTDOWN_WAIT_S) -> None:
        """Cancel every active job; process groups still alive after ``wait_s`` are killed."""
        active = [job for job in self._jobs.values() if job.active]
        for job in active:
            self.cancel(job.id)
        if active:
            await asyncio.wait([asyncio.ensure_future(job.done.wait()) for job in active], timeout=wait_s)
        for job in active:
            if job.active:
                self._signal(job, signal.SIGKILL)
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=wait_s)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _signal(job: Job, sig: signal.Signals) -> None:
        if job.process is None or job.process.returncode is not None:
            return
        try:
            os.killpg(job.process.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    async def _escalate(self, job: Job) -> None:
        for sig, delay in ((signal.SIGTERM, CANCEL_TERM_AFTER_S), (signal.SIGKILL, CANCEL_KILL_AFTER_S)):
            try:
                await asyncio.wait_for(job.done.wait(), delay)
                return
            except asyncio.TimeoutError:
                logger.warning("job %s ignores cancel; sending %s", job.id, sig.name)
                self._signal(job, sig)

    async def _supervise(self, job: Job) -> None:
        pending: list[str] = []
        flush_handle: Optional[asyncio.TimerHandle] = None

        def flush() -> None:
            nonlocal flush_handle
            if flush_handle is not None:
                flush_handle.cancel()
                flush_handle = None
            if pending:
                first = job.log.last_seq - len(pending) + 1
                self._hub.publish(TOPIC_JOB_LOG, {"job": job.id, "first_seq": first, "lines": list(pending)})
                pending.clear()

        def add_line(raw: bytes) -> None:
            nonlocal flush_handle
            line = raw.decode("utf-8", "replace").rstrip("\r")[:MAX_LINE_CHARS]
            job.log.append(line)
            pending.append(line)
            if job.spec.on_line is not None:
                try:
                    job.spec.on_line(job, line)
                except Exception:  # noqa: BLE001 - a hook must not stop log capture
                    logger.exception("job %s line hook failed", job.id)
            if len(pending) >= LOG_BATCH_LINES:
                flush()
            elif flush_handle is None:
                flush_handle = asyncio.get_running_loop().call_later(LOG_FLUSH_INTERVAL_S, flush)

        assert job.process is not None and job.process.stdout is not None
        buffer = b""
        try:
            while True:
                chunk = await job.process.stdout.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for raw in lines:
                    add_line(raw)
                # An unterminated line is cut at the length cap so the buffer stays bounded.
                while len(buffer) > MAX_LINE_CHARS * 4:
                    add_line(buffer[: MAX_LINE_CHARS * 4])
                    buffer = buffer[MAX_LINE_CHARS * 4 :]
            if buffer:
                add_line(buffer)
            job.exit_code = await job.process.wait()
        except Exception as exc:  # noqa: BLE001 - recorded on the job
            logger.exception("job %s supervision failed", job.id)
            job.error = str(exc)
            self._signal(job, signal.SIGKILL)
            job.exit_code = await job.process.wait()
        flush()

        if job.cancel_requested:
            job.state = STATE_CANCELLED
        elif job.exit_code == 0 and job.error is None:
            job.state = STATE_SUCCEEDED
        else:
            job.state = STATE_FAILED
        job.finished_at = time.time()
        if self._active_by_key.get(job.spec.key) is job:
            del self._active_by_key[job.spec.key]
        if job.spec.on_exit is not None:
            try:
                job.spec.on_exit(job)
            except Exception:  # noqa: BLE001 - the final state is published regardless
                logger.exception("job %s exit hook failed", job.id)
        job.done.set()
        self._publish_state(job)
        self._evict_finished()

    def _publish_state(self, job: Job) -> None:
        self._hub.publish(TOPIC_JOB, job.to_view())

    def _evict_finished(self) -> None:
        finished = sorted((j for j in self._jobs.values() if not j.active), key=lambda j: j.created_at)
        for job in finished[: max(len(finished) - self._finished_kept, 0)]:
            del self._jobs[job.id]
