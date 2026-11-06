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
member records, forwards its ``member`` and ``member.log`` events to the event stream and relays the
``launch.*`` and ``measure.*`` methods as requests over the socket. A ``launch`` event carries the
session view (without members) whenever its job, connection or runtime state changes.

Member output flows only for followed members: ``launch.follow`` asks the runtime to tail one
member and answers with the current tail of its files, ``launch.unfollow`` releases it. The daemon
is one runtime client, so follows are shared by every event stream and released together once the
last stream closes.

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

from ..exports import SystemExport
from . import api, jobs
from .events import EventHub

logger = logging.getLogger(__name__)

TOPIC_MEMBER = "member"
TOPIC_MEMBER_LOG = "member.log"
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
MAX_LINE = 64 * 1024
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
        self.control: dict[str, Any] = {"pid": None, "policy": None, "methods": [], "measure_armed": False}
        self.launch_ready = False
        self.shutting_down = False
        self.updated_at = time.time()

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
            "launch_ready": self.launch_ready,
            "shutting_down": self.shutting_down,
            "member_count": len(self.members),
            "updated_at": self.updated_at,
        }
        if members:
            view["members"] = list(self.members.values())
        return view


class LaunchManager:
    """Launch sessions of this daemon, one per compute unit; the last session of a unit stays until the next."""

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
    ) -> None:
        self._hub = hub
        self._jobs = runner
        self._cwd = cwd
        self.env = dict(os.environ if env is None else env)
        self.env_script = env_script
        self.launcher = launcher or find_launcher(install_root, self.env)
        self.rviz_config = rviz_config or find_rviz_config(self.env)
        self.sessions: dict[str, LaunchSession] = {}
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
        return {unit for unit, session in self.sessions.items() if session.active and session.system.id == system_id}

    def views(self, compute_unit: Optional[str] = None) -> list[dict[str, Any]]:
        sessions = self.sessions.values() if compute_unit is None else [self.session(compute_unit)]
        return [session.to_view(members=True) for session in sorted(sessions, key=lambda s: s.compute_unit)]

    def session(self, compute_unit: str) -> LaunchSession:
        session = self.sessions.get(compute_unit)
        if session is None:
            raise api.ApiError(api.NOT_FOUND, f"no launch of compute unit {compute_unit!r}")
        return session

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
        running = self.sessions.get(compute_unit)
        if running is not None and running.active:
            raise api.ApiError(
                api.CONFLICT, f"compute unit {compute_unit!r} is already launched (job {running.job.id})"
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
        self.sessions[compute_unit] = session
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

    async def follow(self, compute_unit: str, path: str) -> dict[str, Any]:
        """Tails one member on the runtime; the result carries the current ``out`` and ``err`` tails."""
        session = self.active_session(compute_unit)
        result = await self.call(compute_unit, "log.follow", {"path": path})
        session.follows.add(path)
        return result

    async def unfollow(self, compute_unit: str, path: Optional[str]) -> dict[str, Any]:
        """Releases one followed member, or every one of the unit when no path is given."""
        session = self.active_session(compute_unit)
        result = await self.call(compute_unit, "log.unfollow", {} if path is None else {"path": path})
        if path is None:
            session.follows.clear()
        else:
            session.follows.discard(path)
        return result

    def release_follows(self) -> None:
        """Drops every follow of every connected runtime; called once no event stream is left to read them."""
        for session in self.sessions.values():
            if session.follows and session.client is not None:
                session.follows.clear()
                task = asyncio.ensure_future(session.client.call("log.unfollow", {}))
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
            }
            session.launch_ready = bool(obj.get("launch_ready"))
            session.shutting_down = bool(obj.get("shutting_down"))
            records = obj.get("members") if isinstance(obj.get("members"), list) else []
            session.members = {r["path"]: r for r in records if isinstance(r, dict) and isinstance(r.get("path"), str)}
            session.follows.clear()
            self._publish(session)
        elif kind == "member" and isinstance(obj.get("path"), str):
            record = {k: v for k, v in obj.items() if k != "event"}
            session.members[record["path"]] = record
            self._hub.publish(
                TOPIC_MEMBER, {"compute_unit": session.compute_unit, "system": session.system.id, **record}
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

    def _on_exit(self, session: LaunchSession) -> None:
        if session.client is not None:
            session.client.close()
            session.client = None
        _unlink(session.socket)
        self._publish(session)

    def _publish(self, session: LaunchSession) -> None:
        session.updated_at = time.time()
        self._hub.publish(TOPIC_LAUNCH, session.to_view())


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
        logger.warning("releasing follows failed: %s", exc)


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
