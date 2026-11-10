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

"""Launch of a compute unit as a supervised runtime process, driven over its control socket.

A session is ``starting`` until the channel connects, ``running`` while it is, ``stopping`` once
the runtime closed the channel for a requested shutdown, and ``exited`` when the job has ended.

A launch is a job running ``autoware-system-designer-launch --control <socket>``, one job key per
compute unit. The daemon connects to the socket once the runtime serves it, keeps the runtime's
member records, forwards its ``member``, ``member.log`` and ``member.activity`` events to the event
stream and relays the
``launch.*`` and ``measure.*`` methods as requests over the socket. A ``launch`` event carries the
session view (without members) whenever its job, connection or runtime state changes.

Every member record the daemon keeps or forwards carries the runtime's ``restarts``, the times the
member was started again within the launch, and ``respawns``, the respawns among them.

Member output flows only for followed members: ``launch.follow`` asks the runtime to tail one
member and answers with the current tail of its files, ``launch.unfollow`` releases it. The daemon
is one runtime client, so follows are shared by every event stream and released together once the
last stream closes. Once the channel is gone, a follow answers the tails read from the runtime's log
directory, with no lines to come.

Topic rates flow only while followed: ``monitor.follow`` asks the runtime for a ``monitor`` event per
interval, forwarded as the ``monitor`` topic, and ``monitor.unfollow`` releases it. Follows of a unit
are counted over the callers and released together once the last event stream closes.

RViz is a job of its own (``rviz2``, one per daemon) in the same launch environment.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from autoware_system_designer_runtime.rates_record import RATES_FILE, RatesRecord

from ..exports import SystemExport
from . import api, jobs
from .events import EventHub

logger = logging.getLogger(__name__)

TOPIC_MEMBER = "member"
TOPIC_MEMBER_LOG = "member.log"
TOPIC_MEMBER_ACTIVITY = "member.activity"
TOPIC_MONITOR = "monitor"
TOPIC_LAUNCH = "launch"

LAUNCH_JOB_KIND = "launch"
LAUNCHER = "autoware-system-designer-launch"
RUNTIME_PACKAGE = "autoware_system_designer_runtime"
RVIZ_JOB_KIND = "rviz"
RVIZ = "rviz2"
# Default RViz configuration, looked up under the launch environment's ament prefixes.
RVIZ_CONFIG_SHARE = Path("share/autoware_launch/rviz/autoware.rviz")
POLICIES = ("guard", "open")
MEMBER_ACTIONS = ("stop", "restart", "kill")
MEASURE_VERBS = ("start", "stop", "status")

SESSION_STARTING = "starting"
SESSION_RUNNING = "running"
SESSION_STOPPING = "stopping"
SESSION_EXITED = "exited"

CONNECT_POLL_S = 0.2
REQUEST_TIMEOUT_S = 30.0
# Longest line read from a runtime; a ``monitor`` snapshot of a large unit is hundreds of KB.
MAX_LINE = 8 << 20
# Tail of a member file read from the log directory: at most this many lines from its last bytes.
TAIL_LINES = 200
TAIL_BYTES = 64 * 1024
# Past runs: the runtime's default log base holds one directory per run, with its run.json manifest.
LOG_BASE = Path("/tmp/autoware_system_designer_logs")
RUN_MANIFEST = "run.json"
MAX_RUNS = 100
SOCKET_DIR = "asd-studio"
_ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class LaunchEnvError(Exception):
    pass


def capture_env(script: Optional[Path]) -> dict[str, str]:
    """Environment launch jobs run in: this process's, with ``script`` sourced over it when given."""
    if script is None:
        return dict(os.environ)
    result = subprocess.run(
        ["bash", "-c", 'source "$1" >/dev/null && env -0', "env", str(script)],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise LaunchEnvError(f"cannot source {script}: {detail or f'exit {result.returncode}'}")
    env: dict[str, str] = {}
    for entry in result.stdout.split(b"\0"):
        key, sep, value = entry.partition(b"=")
        if sep:
            env[key.decode("utf-8", "replace")] = value.decode("utf-8", "replace")
    return env


def find_launcher(install_root: Path, env: Mapping[str, str]) -> Optional[Path]:
    """The runtime launcher: on the launch environment's PATH, else under the install tree's ``bin/``."""
    found = shutil.which(LAUNCHER, path=env.get("PATH"))
    if found:
        return Path(found)
    for candidate in (install_root / RUNTIME_PACKAGE / "bin" / LAUNCHER, install_root / "bin" / LAUNCHER):
        if os.access(candidate, os.X_OK):
            return candidate
    return None


def find_rviz_config(env: Mapping[str, str]) -> Optional[Path]:
    """The Autoware RViz configuration of the first ament prefix that ships one."""
    for prefix in filter(None, env.get("AMENT_PREFIX_PATH", "").split(os.pathsep)):
        candidate = Path(prefix) / RVIZ_CONFIG_SHARE
        if candidate.is_file():
            return candidate
    return None


def socket_dir() -> Path:
    """Private directory of the control sockets; short, since socket paths are capped near 108 bytes."""
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    path = base / f"{SOCKET_DIR}-{os.getuid()}"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


class ControlClient:
    """One connection to a runtime control socket: events to ``on_event``, requests answered by id."""

    def __init__(self, path: Path, on_event: Callable[[dict[str, Any]], None]) -> None:
        self._path = path
        self._on_event = on_event
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_unix_connection(str(self._path), limit=MAX_LINE)

    async def run(self) -> None:
        """Reads until the runtime closes the socket; every request still pending then fails."""
        assert self._reader is not None
        try:
            while True:
                try:
                    line = await self._reader.readline()
                except (asyncio.LimitOverrunError, ValueError):
                    logger.warning("control line over %d bytes from %s; connection dropped", MAX_LINE, self._path)
                    return
                if not line:
                    return
                self._receive(line)
        finally:
            self.close()

    def _receive(self, line: bytes) -> None:
        try:
            obj = json.loads(line)
        except ValueError:
            logger.warning("control message is not JSON: %r", line[:80])
            return
        if not isinstance(obj, dict):
            return
        if "event" in obj:
            self._on_event(obj)
            return
        future = self._pending.pop(obj.get("id"), None)
        if future is not None and not future.done():
            future.set_result(obj)

    async def call(self, method: str, params: dict[str, Any]) -> Any:
        if self._writer is None or self._writer.is_closing():
            raise api.ApiError(api.UNAVAILABLE, "control channel is closed")
        request_id = next(self._ids)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        payload = json.dumps({"id": request_id, "method": method, "params": params}, separators=(",", ":"))
        try:
            self._writer.write(payload.encode() + b"\n")
            await self._writer.drain()
            reply = await asyncio.wait_for(future, REQUEST_TIMEOUT_S)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise api.ApiError(api.UNAVAILABLE, f"runtime did not answer {method} within {REQUEST_TIMEOUT_S:g}s")
        except (ConnectionError, asyncio.CancelledError):
            self._pending.pop(request_id, None)
            raise api.ApiError(api.UNAVAILABLE, "control channel closed while waiting for the runtime")
        if reply.get("ok"):
            return reply.get("result")
        error = reply.get("error") if isinstance(reply.get("error"), dict) else {}
        raise api.ApiError(str(error.get("code") or api.INTERNAL), str(error.get("message") or "runtime error"))

    def close(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(api.ApiError(api.UNAVAILABLE, "control channel closed"))
        self._pending.clear()
        if self._writer is not None and not self._writer.is_closing():
            self._writer.close()


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


class LaunchSession:
    """One launch of a compute unit: its job, the control connection and the runtime's member records."""

    def __init__(self, system: SystemExport, compute_unit: str, mode: str, deploy: Optional[str], socket: Path):
        self.system = system
        self.compute_unit = compute_unit
        self.mode = mode
        self.deploy = deploy
        self.socket = socket
        self.job: Optional[jobs.Job] = None
        self.client: Optional[ControlClient] = None
        self.members: dict[str, dict[str, Any]] = {}
        self.follows: set[str] = set()
        # Callers following the unit's topic rates; the runtime is asked once while any does.
        self.monitors = 0
        self.control: dict[str, Any] = {
            "pid": None,
            "policy": None,
            "methods": [],
            "measure_armed": False,
            "monitor": False,
        }
        self.log_dir: Optional[Path] = None
        self.launch_ready = False
        self.shutting_down = False
        self.started_at = time.time()
        self.updated_at = self.started_at

    @staticmethod
    def count(record: dict[str, Any]) -> dict[str, Any]:
        """``record`` with the runtime's ``restarts`` and ``respawns``, 0 where it reports none."""
        return {**record, "restarts": _count(record.get("restarts")), "respawns": _count(record.get("respawns"))}

    @property
    def active(self) -> bool:
        return self.job is not None and self.job.active

    @property
    def state(self) -> str:
        if not self.active:
            return SESSION_EXITED
        if self.client is not None:
            return SESSION_RUNNING
        return SESSION_STOPPING if self.shutting_down else SESSION_STARTING

    def to_view(self, *, members: bool = False) -> dict[str, Any]:
        job = self.job
        view = {
            "compute_unit": self.compute_unit,
            "system": self.system.id,
            "mode": self.mode,
            "deploy": self.deploy,
            "state": self.state,
            "job": None if job is None else job.id,
            "job_state": None if job is None else job.state,
            "exit_code": None if job is None else job.exit_code,
            "connected": self.client is not None,
            "control": dict(self.control),
            "log_dir": None if self.log_dir is None else str(self.log_dir),
            "launch_ready": self.launch_ready,
            "shutting_down": self.shutting_down,
            "member_count": len(self.members),
            "updated_at": self.updated_at,
        }
        if members:
            view["members"] = list(self.members.values())
        return view


class LaunchManager:
    """Launch sessions of this daemon: one running per compute unit, and one mode of a system at a time
    across its units. The last session of each mode of a system on a unit stays until that mode's next."""

    def __init__(
        self,
        hub: EventHub,
        runner: jobs.JobRunner,
        *,
        install_root: Path,
        cwd: Path,
        env: Optional[Mapping[str, str]] = None,
        env_script: Optional[Path] = None,
        launcher: Optional[Path] = None,
        rviz_config: Optional[Path] = None,
        log_base: Optional[Path] = None,
    ) -> None:
        self._hub = hub
        self.log_base = log_base or LOG_BASE
        self._jobs = runner
        self._cwd = cwd
        self.env = dict(os.environ if env is None else env)
        self.env_script = env_script
        self.launcher = launcher or find_launcher(install_root, self.env)
        self.rviz_config = rviz_config or find_rviz_config(self.env)
        # (compute unit, system id, mode) -> the last session of that mode on the unit.
        self.sessions: dict[tuple[str, str, str], LaunchSession] = {}
        self._tasks: set[asyncio.Task] = set()

    def info(self) -> dict[str, Any]:
        rviz = shutil.which(RVIZ, path=self.env.get("PATH"))
        return {
            "launcher": None if self.launcher is None else str(self.launcher),
            "env_script": None if self.env_script is None else str(self.env_script),
            "ros_distro": self.env.get("ROS_DISTRO"),
            "rviz": rviz,
            "rviz_config": None if self.rviz_config is None else str(self.rviz_config),
        }

    def running_units(self, system_id: str) -> set[str]:
        return {
            session.compute_unit
            for session in self.sessions.values()
            if session.active and session.system.id == system_id
        }

    def views(self, compute_unit: Optional[str] = None) -> list[dict[str, Any]]:
        sessions = list(self.sessions.values()) if compute_unit is None else self._of_unit(compute_unit)
        sessions.sort(key=lambda s: (s.compute_unit, s.started_at))
        return [session.to_view(members=True) for session in sessions]

    def _of_unit(self, compute_unit: str) -> list[LaunchSession]:
        sessions = [session for session in self.sessions.values() if session.compute_unit == compute_unit]
        if not sessions:
            raise api.ApiError(api.NOT_FOUND, f"no launch of compute unit {compute_unit!r}")
        return sessions

    def session(
        self, compute_unit: str, *, system_id: Optional[str] = None, mode: Optional[str] = None
    ) -> LaunchSession:
        """The unit's session of ``mode`` of a system; without both the running one, else the latest."""
        sessions = self._of_unit(compute_unit)
        if (system_id is None) != (mode is None):
            raise api.ApiError(api.INVALID_PARAMS, "a launch scope needs both system and mode, or neither")
        if system_id is not None:
            session = self.sessions.get((compute_unit, system_id, mode))
            if session is None:
                raise api.ApiError(api.NOT_FOUND, f"no launch of mode {mode!r} on compute unit {compute_unit!r}")
            return session
        return max(sessions, key=lambda s: (s.active, s.started_at))

    def active_session(self, compute_unit: str) -> LaunchSession:
        session = self.session(compute_unit)
        if not session.active:
            raise api.ApiError(api.NOT_FOUND, f"the launch of compute unit {compute_unit!r} has exited")
        return session

    async def start(
        self,
        system: SystemExport,
        *,
        compute_unit: str,
        mode: str,
        deploy: Optional[str] = None,
        args: Optional[Mapping[str, str]] = None,
        policy: str = POLICIES[0],
        measure: bool = False,
        respawn: bool = False,
    ) -> LaunchSession:
        if self.launcher is None:
            raise api.ApiError(api.UNAVAILABLE, f"{LAUNCHER} not found on the launch PATH or under the install tree")
        self._require_ros()
        for running in self.sessions.values():
            if not running.active:
                continue
            if running.compute_unit == compute_unit:
                raise api.ApiError(
                    api.CONFLICT, f"compute unit {compute_unit!r} is already launched (job {running.job.id})"
                )
            if running.system.id == system.id and running.mode != mode:
                raise api.ApiError(
                    api.CONFLICT,
                    f"{system.id} runs mode {running.mode!r} on compute unit {running.compute_unit!r}",
                )
        socket = socket_dir() / f"{os.getpid()}-{compute_unit}.sock"
        argv = [
            str(self.launcher),
            str(system.structure_path(mode)),
            "--compute-unit",
            compute_unit,
            "--control",
            str(socket),
            "--control-policy",
            policy,
        ]
        if deploy:
            argv += ["--deploy", deploy]
        for name, value in (args or {}).items():
            argv += ["--arg", f"{name}={value}"]
        if measure:
            argv.append("--measure")
        if respawn:
            argv.append("--respawn")
        session = LaunchSession(system, compute_unit, mode, deploy, socket)
        _unlink(socket)
        spec = jobs.JobSpec(
            key=f"{LAUNCH_JOB_KIND}:{compute_unit}",
            kind=LAUNCH_JOB_KIND,
            title=f"launch {system.name} {mode}/{compute_unit}",
            argv=argv,
            cwd=self._cwd,
            env=self.env,
            params={
                "system": system.id,
                "mode": mode,
                "compute_unit": compute_unit,
                "deploy": deploy,
                "args": dict(args or {}),
                "policy": policy,
                "measure": measure,
                "respawn": respawn,
            },
            detail={"socket": str(socket)},
            on_exit=lambda job: self._on_exit(session),
        )
        session.job = await self._jobs.start(spec)
        self.sessions[(compute_unit, system.id, mode)] = session
        self._publish(session)
        task = asyncio.ensure_future(self._attach(session))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return session

    async def start_rviz(self) -> jobs.Job:
        """RViz on the daemon's configuration as a job; one RViz per daemon (``conflict`` while it runs)."""
        self._require_ros()
        rviz = shutil.which(RVIZ, path=self.env.get("PATH"))
        if rviz is None:
            raise api.ApiError(api.UNAVAILABLE, f"{RVIZ} not found on the launch PATH")
        argv = [rviz]
        if self.rviz_config is not None:
            argv += ["-d", str(self.rviz_config)]
        spec = jobs.JobSpec(
            key=RVIZ_JOB_KIND,
            kind=RVIZ_JOB_KIND,
            title="rviz",
            argv=argv,
            cwd=self._cwd,
            env=self.env,
            params={"config": None if self.rviz_config is None else str(self.rviz_config)},
        )
        return await self._jobs.start(spec)

    async def call(self, compute_unit: str, method: str, params: dict[str, Any]) -> Any:
        session = self.active_session(compute_unit)
        if session.client is None:
            raise api.ApiError(api.UNAVAILABLE, f"the control channel of {compute_unit!r} is not connected yet")
        return await session.client.call(method, params)

    async def follow(
        self, compute_unit: str, path: str, *, system_id: Optional[str] = None, mode: Optional[str] = None
    ) -> dict[str, Any]:
        """Tails one member on the runtime; the result carries the current ``out`` and ``err`` tails.
        Without a channel, the tails come from the member's files in the runtime's log directory."""
        session = self.session(compute_unit, system_id=system_id, mode=mode)
        if session.client is not None:
            result = await session.client.call("log.follow", {"path": path})
            session.follows.add(path)
            return result
        if session.log_dir is None:
            if not session.active:
                raise api.ApiError(api.NOT_FOUND, f"the launch of compute unit {compute_unit!r} has exited")
            raise api.ApiError(api.UNAVAILABLE, f"the control channel of {compute_unit!r} is not connected yet")
        if path not in session.members:
            raise api.ApiError(api.NOT_FOUND, f"no member {path!r}")
        member_dir = session.log_dir / member_slug(path)
        return {"path": path, "out": tail_lines(member_dir / "out"), "err": tail_lines(member_dir / "err")}

    async def unfollow(
        self, compute_unit: str, path: Optional[str], *, system_id: Optional[str] = None, mode: Optional[str] = None
    ) -> dict[str, Any]:
        """Releases one followed member, or every one of the unit when no path is given; a session
        without a channel has nothing to release."""
        session = self.session(compute_unit, system_id=system_id, mode=mode)
        if session.client is None:
            session.follows.clear()
            return {"released": []}
        result = await session.client.call("log.unfollow", {} if path is None else {"path": path})
        if path is None:
            session.follows.clear()
        else:
            session.follows.discard(path)
        return result

    async def monitor_follow(self, compute_unit: str) -> dict[str, Any]:
        """Counts one more follower of the unit's topic rates; the first one asks the runtime."""
        session = self.active_session(compute_unit)
        if session.client is None:
            raise api.ApiError(api.UNAVAILABLE, f"the control channel of {compute_unit!r} is not connected yet")
        result = await session.client.call("monitor.follow", {})
        session.monitors += 1
        return result

    async def monitor_unfollow(self, compute_unit: str) -> dict[str, Any]:
        """Counts one follower less; the last one releases the runtime's rates."""
        session = self.active_session(compute_unit)
        session.monitors = max(session.monitors - 1, 0)
        if session.monitors or session.client is None:
            return {"released": False}
        return await session.client.call("monitor.unfollow", {})

    # -- past runs: the runtime's log directories -----------------------------------

    def log_bases(self) -> list[Path]:
        """Directories holding run directories: the runtime's default base and the parents of the
        sessions' log directories."""
        bases = [self.log_base]
        for session in self.sessions.values():
            if session.log_dir is not None and session.log_dir.parent not in bases:
                bases.append(session.log_dir.parent)
        return bases

    def runs(self, system: Optional[SystemExport] = None) -> list[dict[str, Any]]:
        """Past runs, newest first: the directories holding a run manifest, less those an active session
        writes, its session serving them. With ``system``, runs of other systems are left out."""
        active = {session.log_dir for session in self.sessions.values() if session.active and session.log_dir}
        runs = []
        for base in self.log_bases():
            try:
                entries = [entry for entry in base.iterdir() if entry.is_dir()]
            except OSError:
                continue
            for run_dir in entries:
                manifest = _read_manifest(run_dir)
                if manifest is None or run_dir in active:
                    continue
                if system is not None and not _run_of_system(manifest, system):
                    continue
                runs.append(_run_summary(run_dir, manifest))
        runs.sort(key=lambda run: run["started"], reverse=True)
        return runs[:MAX_RUNS]

    def run(self, name: str) -> tuple[Path, dict[str, Any]]:
        """The log directory and manifest of a past run, by directory name under one of the log bases."""
        if not _plain_name(name):
            raise api.ApiError(api.INVALID_PARAMS, "run must be a directory name")
        for base in self.log_bases():
            run_dir = base / name
            manifest = _read_manifest(run_dir) if run_dir.is_dir() else None
            if manifest is not None:
                return run_dir, manifest
        raise api.ApiError(api.NOT_FOUND, f"no run {name!r}")

    def run_members(self, name: str) -> dict[str, Any]:
        """A past run with its members: the manifest's member paths that have a directory, each with its
        directory name."""
        run_dir, manifest = self.run(name)
        listed = manifest.get("members")
        paths = [path for path in listed if isinstance(path, str)] if isinstance(listed, list) else []
        members = [
            {"path": path, "slug": member_slug(path)} for path in paths if (run_dir / member_slug(path)).is_dir()
        ]
        return {**_run_summary(run_dir, manifest), "members": members}

    def run_rates(self, name: str, *, until_ns: Optional[int] = None, span_s: Optional[float] = None) -> dict[str, Any]:
        """Topic rates of a past run from its rates record: over ``span_s`` seconds up to the last sample at
        or before ``until_ns``, the whole run by default; ``times`` lists the record's samples."""
        run_dir, manifest = self.run(name)
        path = run_dir / RATES_FILE
        if not path.is_file():
            raise api.ApiError(api.NOT_FOUND, f"run {name!r} recorded no topic rates")
        try:
            record = RatesRecord.load(path)
            rates = record.rates(until_ns=until_ns, span_s=span_s)
        except (OSError, ValueError) as exc:
            raise api.ApiError(api.NOT_FOUND, f"rates of run {name!r} are not readable: {exc}") from exc
        return {**_run_summary(run_dir, manifest), "times": record.times, **rates}

    def run_tail(self, name: str, member: str) -> dict[str, Any]:
        """Tails of a member's ``out`` and ``err`` files in a past run; ``member`` is its directory name."""
        if not _plain_name(member):
            raise api.ApiError(api.INVALID_PARAMS, "member must be a directory name")
        member_dir = self.run(name)[0] / member
        if not member_dir.is_dir():
            raise api.ApiError(api.NOT_FOUND, f"no member {member!r} in run {name!r}")
        return {
            "run": name,
            "member": member,
            "out": tail_lines(member_dir / "out"),
            "err": tail_lines(member_dir / "err"),
        }

    def release_follows(self) -> None:
        """Drops every follow of every connected runtime; called once no event stream is left to read them."""
        for session in self.sessions.values():
            if session.client is None:
                continue
            if session.follows:
                session.follows.clear()
                self._request(session, "log.unfollow")
            if session.monitors:
                session.monitors = 0
                self._request(session, "monitor.unfollow")

    def _request(self, session: LaunchSession, method: str) -> None:
        """Sends a request whose answer nobody waits for; a failure is logged."""
        task = asyncio.ensure_future(session.client.call(method, {}))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_failure)

    async def shutdown(self, compute_unit: str) -> dict[str, Any]:
        """Shutdown through the runtime; a launch without a channel yet is cancelled as a job."""
        session = self.active_session(compute_unit)
        if session.client is None:
            assert session.job is not None
            self._jobs.cancel(session.job.id)
            return {"shutting_down": True, "cancelled": True}
        result = await session.client.call("shutdown", {})
        session.shutting_down = True
        self._publish(session)
        return result

    async def close(self) -> None:
        for session in self.sessions.values():
            if session.client is not None:
                session.client.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=1.0)

    # -- internals ---------------------------------------------------------

    def _require_ros(self) -> None:
        if not self.env.get("ROS_DISTRO"):
            raise api.ApiError(
                api.UNAVAILABLE,
                "the launch environment has no ROS setup sourced (start serve from a sourced shell "
                "or pass --env-script)",
            )

    async def _attach(self, session: LaunchSession) -> None:
        """Connects once the runtime serves its socket; a connection lost while the job lives is remade."""
        while session.active:
            client = ControlClient(session.socket, lambda obj: self._on_event(session, obj))
            try:
                await client.connect()
            except (OSError, ConnectionError):
                await asyncio.sleep(CONNECT_POLL_S)
                continue
            session.client = client
            try:
                await client.run()
            finally:
                if session.client is client:
                    session.client = None
                    self._publish(session)

    def _on_event(self, session: LaunchSession, obj: dict[str, Any]) -> None:
        kind = obj.get("event")
        if kind == "hello":
            session.control = {
                "pid": obj.get("pid"),
                "policy": obj.get("policy"),
                "methods": obj.get("methods") or [],
                "measure_armed": bool(obj.get("measure_armed")),
                "monitor": bool(obj.get("monitor")),
            }
            log_dir = obj.get("log_dir")
            session.log_dir = Path(log_dir) if isinstance(log_dir, str) and log_dir else None
            session.launch_ready = bool(obj.get("launch_ready"))
            session.shutting_down = bool(obj.get("shutting_down"))
            records = obj.get("members") if isinstance(obj.get("members"), list) else []
            listed = [r for r in records if isinstance(r, dict) and isinstance(r.get("path"), str)]
            session.members = {r["path"]: session.count(r) for r in listed}
            session.follows.clear()
            # A reconnected channel is a new runtime client: the counted followers keep their rates.
            if session.monitors and session.control["monitor"]:
                self._request(session, "monitor.follow")
            self._publish(session)
        elif kind == "member" and isinstance(obj.get("path"), str):
            record = session.count({k: v for k, v in obj.items() if k != "event"})
            session.members[record["path"]] = record
            self._hub.publish(
                TOPIC_MEMBER,
                {
                    "compute_unit": session.compute_unit,
                    "system": session.system.id,
                    "mode": session.mode,
                    **record,
                },
            )
        elif kind == "member.log" and isinstance(obj.get("path"), str):
            self._hub.publish(
                TOPIC_MEMBER_LOG,
                {
                    "compute_unit": session.compute_unit,
                    "path": obj["path"],
                    "stream": obj.get("stream"),
                    "lines": obj.get("lines") or [],
                },
            )
        elif kind == "member.activity" and isinstance(obj.get("rates"), dict):
            self._hub.publish(
                TOPIC_MEMBER_ACTIVITY,
                {
                    "compute_unit": session.compute_unit,
                    "system": session.system.id,
                    "mode": session.mode,
                    "window": obj.get("window"),
                    "rates": obj["rates"],
                },
            )

        elif kind == "monitor" and isinstance(obj.get("nodes"), list):
            self._hub.publish(
                TOPIC_MONITOR,
                {
                    "compute_unit": session.compute_unit,
                    "system": session.system.id,
                    "mode": session.mode,
                    **{k: v for k, v in obj.items() if k != "event"},
                },
            )

    def _on_exit(self, session: LaunchSession) -> None:
        if session.client is not None:
            session.client.close()
            session.client = None
        _unlink(session.socket)
        self._publish(session)

    def _publish(self, session: LaunchSession) -> None:
        session.updated_at = time.time()
        self._hub.publish(TOPIC_LAUNCH, session.to_view())


def member_slug(path: str) -> str:
    """Name of a member's directory under the runtime's log directory: the member path with every
    character outside letters, digits, ``_``, ``-`` and ``.`` replaced by ``_``, leading ``_`` dropped."""
    safe = "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in path).lstrip("_")
    return safe or "_unnamed_"


def _plain_name(name: Optional[str]) -> bool:
    """A single directory name: no separators, no traversal."""
    return bool(name) and "/" not in name and "\\" not in name and "\x00" not in name and name not in (".", "..")


def _read_manifest(run_dir: Path) -> Optional[dict[str, Any]]:
    """The run manifest of a log directory; None where there is none, so the directory is not a run."""
    try:
        manifest = json.loads((run_dir / RUN_MANIFEST).read_text())
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _run_of_system(manifest: Mapping[str, Any], system: SystemExport) -> bool:
    """Whether a run's manifest names ``system``, by system name or by a structure path under its
    export directory."""
    if manifest.get("system") == system.name:
        return True
    structure = manifest.get("system_structure")
    if not isinstance(structure, str):
        return False
    try:
        Path(structure).resolve().relative_to(system.export_dir.resolve())
    except (ValueError, OSError):
        return False
    return True


def _run_summary(run_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    started = manifest.get("started")
    members = manifest.get("members")

    def text(key: str) -> Optional[str]:
        value = manifest.get(key)
        return value if isinstance(value, str) else None

    return {
        "run": run_dir.name,
        "dir": str(run_dir),
        "started": float(started) if isinstance(started, (int, float)) and not isinstance(started, bool) else 0.0,
        "mode": text("mode"),
        "compute_unit": text("compute_unit"),
        "deploy": text("deploy"),
        "member_count": len(members) if isinstance(members, list) else 0,
        "rates": (run_dir / RATES_FILE).is_file(),
    }


def tail_lines(path: Path) -> list[str]:
    """The last lines of a file, at most ``TAIL_LINES`` from its final ``TAIL_BYTES``."""
    try:
        size = path.stat().st_size
        start = max(0, size - TAIL_BYTES)
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(size - start)
    except OSError:
        return []
    chunks = data.split(b"\n")
    if not chunks[-1]:
        chunks.pop()
    if start > 0 and chunks:
        chunks.pop(0)  # a line cut by the window start
    return [chunk.decode("utf-8", "replace") for chunk in chunks[-TAIL_LINES:]]


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("cannot remove %s: %s", path, exc)


def _log_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("control request failed: %s", exc)


def parse_args_param(value: Any) -> dict[str, str]:
    """System argument bindings of a ``launch.start`` request, ``{name: value}`` with plain scalar values."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise api.ApiError(api.INVALID_PARAMS, "args must be an object of name -> value")
    bound: dict[str, str] = {}
    for name, item in value.items():
        if not isinstance(name, str) or not _ARG_NAME.match(name):
            raise api.ApiError(api.INVALID_PARAMS, f"invalid argument name {name!r}")
        if isinstance(item, bool):
            item = "true" if item else "false"
        elif not isinstance(item, (str, int, float)):
            raise api.ApiError(api.INVALID_PARAMS, f"argument {name} must be a string or a number")
        bound[name] = str(item)
    return bound
