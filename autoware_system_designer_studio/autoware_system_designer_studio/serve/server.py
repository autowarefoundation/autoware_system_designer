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

"""Loopback daemon: the studio site drawn from the exported systems, the request API and the event stream.

Routes:
  GET  /                 -> systems index
  GET  /<site path>      -> page or data script rendered from the exports (see pages.render)
  GET  /assets/<file>    -> web bundle shipped with this package
  GET  /api/<alias>      -> registered method with empty params (health, systems, jobs, launch)
  GET  /api/events       -> Server-Sent Events stream
  POST /api/rpc          -> request envelope (see api)

Compute units bound to a peer daemon (see peers) are served through it: requests naming such a unit,
or a job or run id of it (``<unit>/<id>``), are forwarded, lists are merged with the peers' and the
peers' events are relayed, so a client talks to this daemon alone.

Access rules: the listener is bound to 127.0.0.1; every request must name this listener in Host
(DNS rebinding); API requests flagged cross-site by the browser are refused; POST requires
``Content-Type: application/json`` and the ``X-System-Designer-Client`` header, which a cross-origin
page cannot send without a preflight this daemon never grants.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import mimetypes
import os
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from .. import exports
from ..pages import assets
from ..pages.render import INDEX_PAGE, Site, system_pages
from . import api, build, events, jobs, launch, peers, record_watch
from .http import MAX_HEADER_BYTES, HttpError, Request, Response, read_request, response_head, write_response

logger = logging.getLogger(__name__)

BIND_HOST = "127.0.0.1"
LOOPBACK_HOST_NAMES = ("127.0.0.1", "localhost")
CLIENT_HEADER = "X-System-Designer-Client"
RPC_PATH = "/api/rpc"
EVENTS_PATH = "/api/events"
API_PREFIX = "/api/"
BUNDLE_PREFIX = f"/{assets.BUNDLE_DIR}/"
# Packages whose code this daemon runs; rebuilding one inside the served install needs a restart.
OWN_PACKAGES = ("autoware_system_designer_studio", "autoware_system_designer_runtime")

TOPIC_DAEMON = "daemon"
TOPIC_SYSTEMS_CHANGED = "systems.changed"

# Seconds a status page waits before reloading while an export may be mid-rewrite.
RETRY_RELOAD_S = 2

MAX_CONNECTIONS = 64
MAX_EVENT_SUBSCRIBERS = 16
HEARTBEAT_INTERVAL_S = 15.0
SHUTDOWN_GRACE_S = 2.0

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _package_version() -> Optional[str]:
    try:
        from importlib.metadata import version

        return version("autoware_system_designer_studio")
    except Exception:  # noqa: BLE001 - metadata is informational only
        return None


def _str_param(params: dict[str, Any], name: str, *, required: bool = False) -> Optional[str]:
    value = params.get(name)
    if value is None or value == "":
        if required:
            raise api.ApiError(api.INVALID_PARAMS, f"{name} is required")
        return None
    if not isinstance(value, str):
        raise api.ApiError(api.INVALID_PARAMS, f"{name} must be a string")
    return value


def _bool_param(params: dict[str, Any], name: str, default: bool = False) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise api.ApiError(api.INVALID_PARAMS, f"{name} must be a boolean")
    return value


def _int_param(params: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = params.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise api.ApiError(api.INVALID_PARAMS, f"{name} must be an integer within {low}..{high}")
    return value


def _status_page(status: HTTPStatus, title: str, message: str, *, reload_s: Optional[int] = None) -> Response:
    """HTML answer to a page navigation that has no page; it reloads itself when the cause is transient."""
    refresh = f'<meta http-equiv="refresh" content="{reload_s}">' if reload_s else ""
    links = f'<link rel="icon" type="image/svg+xml" href="/{assets.BUNDLE_DIR}/{assets.FAVICON}">' + "".join(
        f'<link rel="stylesheet" href="/{assets.BUNDLE_DIR}/{css}">' for css in assets.PAGE_STYLESHEETS
    )
    body = (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        f"<title>{html.escape(title)}</title>{refresh}{links}"
        f'<script src="/{assets.BUNDLE_DIR}/{assets.PAGE_SCRIPTS[0]}"></script></head>'
        '<body class="simple-page"><div class="page-container">'
        f'<h1>{html.escape(title)}</h1><p class="description">{html.escape(message)}</p>'
        '<div class="nav-links"><a href="/">Systems</a></div>'
        "</div><script>SD.theme.init();</script></body></html>"
    )
    headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")]
    return Response(status, headers, body.encode())


def _is_page(site_path: str) -> bool:
    return site_path.endswith(".html")


def _error_response(error: api.ApiError, request_id: Any = None, *, with_id: bool = False) -> Response:
    body = api.error_body(error, request_id) if with_id else api.error_body(error)
    headers = [("Allow", "GET, HEAD, POST")] if error.code == api.METHOD_NOT_ALLOWED else []
    return Response.json(error.status, body, headers)


class ServeDaemon:
    """One daemon instance; ``served_units`` None means every onboard unit is local to it.

    ``workspace`` is the colcon workspace builds run in, None when this daemon does not build.
    ``launch_env`` is the environment launch jobs run in (this process's when None; see launch.capture_env).
    ``restart_required`` turns true once a build rebuilt this package inside the served install tree:
    the running daemon keeps the code it started with.
    """

    def __init__(
        self,
        install_root: Path,
        *,
        port: int,
        served_units: Optional[Iterable[str]] = None,
        workspace: Optional[Path] = None,
        record_root: Optional[Path] = None,
        launch_env: Optional[Mapping[str, str]] = None,
        env_script: Optional[Path] = None,
        rviz_config: Optional[Path] = None,
        log_base: Optional[Path] = None,
        peer_specs: Iterable[peers.PeerSpec] = (),
        heartbeat_s: float = HEARTBEAT_INTERVAL_S,
        max_connections: int = MAX_CONNECTIONS,
        max_event_subscribers: int = MAX_EVENT_SUBSCRIBERS,
    ) -> None:
        self.install_root = install_root
        self.served_units = None if served_units is None else sorted(set(served_units))
        self.workspace = workspace
        self.record_root = record_root
        self.restart_required = False
        self.serves_own_install = exports.module_install_root() == install_root
        self._requested_port = port
        self._max_connections = max_connections
        self.heartbeat_s = heartbeat_s
        self.hub = events.EventHub(max_event_subscribers, heartbeat_s)
        self.peers = peers.PeerRegistry(peer_specs, self.hub, self._describe)
        if self.served_units is not None and set(self.served_units) & set(self.peers.units):
            both = ", ".join(sorted(set(self.served_units) & set(self.peers.units)))
            raise ValueError(f"compute unit(s) {both} are both local and bound to a peer")
        self.methods = api.MethodRegistry()
        self.methods.register("health", self._health, get_path="health")
        self.methods.register("systems.list", self._systems_list, get_path="systems")
        self.methods.register("jobs.list", self._jobs_list, get_path="jobs")
        self.methods.register("jobs.get", self._of_job("jobs.get", self._jobs_get))
        self.methods.register("jobs.cancel", self._of_job("jobs.cancel", self._jobs_cancel))
        self.methods.register("build.start", self._build_start)
        self.methods.register("launch.start", self._launch_start)
        self.methods.register("launch.status", self._launch_status, get_path="launch")
        for action in launch.MEMBER_ACTIONS:
            self._register_of_unit(f"launch.{action}", self._member_action(action))
        self._register_of_unit("launch.follow", self._launch_follow)
        self._register_of_unit("launch.unfollow", self._launch_unfollow)
        self.methods.register("launch.runs", self._launch_runs)
        for name, handler in (
            ("launch.run", self._launch_run),
            ("launch.run_tail", self._launch_run_tail),
            ("launch.run_rates", self._launch_run_rates),
        ):
            self.methods.register(name, self._of_run(name, handler))
        self._register_of_unit("launch.shutdown", self._launch_shutdown)
        for verb in launch.MEASURE_VERBS:
            self._register_of_unit(f"measure.{verb}", self._measure(verb))
        self._register_of_unit("monitor.follow", self._monitor_follow)
        self._register_of_unit("monitor.unfollow", self._monitor_unfollow)
        self._register_of_unit("monitor.sample", self._monitor_sample)
        self.methods.register("rviz.start", self._rviz_start)
        self.jobs = jobs.JobRunner(self.hub)
        self.launch = launch.LaunchManager(
            self.hub,
            self.jobs,
            install_root=install_root,
            cwd=workspace or install_root.parent,
            env=launch_env,
            env_script=env_script,
            rviz_config=rviz_config,
            log_base=log_base,
        )
        self.records = record_watch.RecordWatcher(self.hub, record_root)
        self._server: Optional[asyncio.AbstractServer] = None
        self._heartbeat: Optional[asyncio.Task] = None
        self._record_watch: Optional[asyncio.Task] = None
        self._connections: set[asyncio.Task] = set()
        self._started_at = time.time()
        self.port = 0

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> int:
        # The reader limit bounds the request head.
        self._server = await asyncio.start_server(
            self._on_connection, BIND_HOST, self._requested_port, limit=MAX_HEADER_BYTES
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self._heartbeat = asyncio.ensure_future(self.hub.run_heartbeat())
        self._record_watch = asyncio.ensure_future(self.records.run())
        self.peers.start()
        self._started_at = time.time()
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for task in (self._heartbeat, self._record_watch):
            if task is not None:
                task.cancel()
        await self.peers.stop()
        await self.jobs.shutdown()
        await self.launch.close()
        self.hub.close()
        if self._connections:
            _, pending = await asyncio.wait(set(self._connections), timeout=SHUTDOWN_GRACE_S)
            for task in pending:
                task.cancel()

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset(f"{name}:{self.port}" for name in LOOPBACK_HOST_NAMES)

    # -- peers -------------------------------------------------------------

    def _register_of_unit(self, name: str, handler: api.Handler) -> None:
        """Registers a method scoped by ``compute_unit``: forwarded when a peer serves the unit."""

        async def routed(params: dict[str, Any]) -> Any:
            link = self.peers.link_of(params.get("compute_unit"))
            return await handler(params) if link is None else await link.forward(name, params)

        self.methods.register(name, routed)

    def _of_job(self, name: str, handler: api.Handler) -> api.Handler:
        """A method scoped by ``job``: forwarded when the id is a peer's."""

        async def routed(params: dict[str, Any]) -> Any:
            target = self.peers.link_of_id(params.get("job"))
            return await handler(params) if target is None else await target[0].job_call(name, target[1], params)

        return routed

    def _of_run(self, name: str, handler: api.Handler) -> api.Handler:
        """A method scoped by ``run``: forwarded when the id is a peer's."""

        async def routed(params: dict[str, Any]) -> Any:
            target = self.peers.link_of_id(params.get("run"))
            return await handler(params) if target is None else await target[0].run_call(name, target[1], params)

        return routed

    async def _describe(self) -> dict[str, Any]:
        """What a peer is compared with: this daemon's version, methods and exported systems."""
        systems, _ = await asyncio.to_thread(exports.discover_systems, self.install_root)
        views = await asyncio.to_thread(lambda: [system.to_view(self.served_units, {}) for system in systems])
        return {"version": _package_version(), "methods": self.methods.names(), "systems": views}

    def _mode_conflict(self, system_id: str, mode: str) -> None:
        """Refuses a launch while a peer's unit runs another mode of the system, as last reported."""
        for session in self.peers.sessions():
            if not peers.PeerLink.session_active(session) or session.get("system") != system_id:
                continue
            if session.get("mode") != mode:
                raise api.ApiError(
                    api.CONFLICT,
                    f"{system_id} runs mode {session.get('mode')!r} on compute unit {session.get('compute_unit')!r}",
                )

    # -- methods -----------------------------------------------------------

    async def _health(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": _package_version(),
            "pid": os.getpid(),
            "started_at": self._started_at,
            "install_root": str(self.install_root),
            "served_units": self.served_units,
            "workspace": None if self.workspace is None else str(self.workspace),
            "record_root": str(self.records.record_root),
            "restart_required": self.restart_required,
            "heartbeat_s": self.heartbeat_s,
            "peers": self.peers.views(),
            "launch": self.launch.info(),
            "event_subscribers": self.hub.subscriber_count,
            "methods": self.methods.names(),
        }

    async def _systems_list(self, params: dict[str, Any]) -> dict[str, Any]:
        systems, errors = await asyncio.to_thread(exports.discover_systems, self.install_root)
        views = await asyncio.to_thread(
            lambda: [system.to_view(self.served_units, self._page_urls(system)) for system in systems]
        )
        for view in views:
            running = self.launch.running_units(view["id"])
            for unit in view["compute_units"]:
                link = self.peers.link_of(unit["name"])
                if link is not None and unit["kind"] == exports.KIND_ONBOARD:
                    unit["status"] = link.unit_status(view["id"], unit["name"])
                    unit["daemon"] = link.view()
                elif unit["name"] in running:
                    unit["status"] = exports.UNIT_RUNNING
        return {
            "install_root": str(self.install_root),
            "systems": views,
            "errors": [{"path": e.path, "message": e.message} for e in errors],
        }

    @staticmethod
    def _page_urls(system: exports.SystemExport) -> dict[str, str]:
        return {name: f"/{path}" for name, path in system_pages(system).items()}

    async def _find_system(self, system_id: str) -> exports.SystemExport:
        systems, _ = await asyncio.to_thread(exports.discover_systems, self.install_root)
        for system in systems:
            if system.id == system_id:
                return system
        raise api.ApiError(api.NOT_FOUND, f"no exported system {system_id!r}")

    async def _jobs_list(self, params: dict[str, Any]) -> dict[str, Any]:
        views = [job.to_view() for job in self.jobs.list()]
        views += await self.peers.gather(lambda link: link.job_views())
        views.sort(key=lambda job: job.get("created_at") or 0, reverse=True)
        return {"jobs": views}

    async def _jobs_get(self, params: dict[str, Any]) -> dict[str, Any]:
        since = _int_param(params, "since", 0, 0, 2**53)
        limit = _int_param(params, "limit", jobs.LOG_RING_LINES, 1, jobs.LOG_RING_LINES)
        job = self.jobs.get(_str_param(params, "job", required=True))
        first_seq, lines = job.log.since(since, limit)
        return {"job": job.to_view(), "log": {"first_seq": first_seq, "lines": lines}}

    async def _jobs_cancel(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.jobs.cancel(_str_param(params, "job", required=True)).to_view()

    async def _build_start(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.workspace is None:
            raise api.ApiError(api.UNAVAILABLE, "this daemon has no workspace to build in (see --workspace)")
        system = await self._find_system(_str_param(params, "system", required=True))
        target = _str_param(params, "target", required=True)
        compute_unit = _str_param(params, "compute_unit")
        if self.peers.link_of(compute_unit) is not None:
            raise api.ApiError(api.INVALID_PARAMS, f"compute unit {compute_unit!r} is built by its own daemon")
        if compute_unit is not None:
            view = system.to_view(self.served_units, {})
            status = next((u["status"] for u in view["compute_units"] if u["name"] == compute_unit), None)
            if status not in (None, exports.UNIT_AVAILABLE):
                raise api.ApiError(api.INVALID_PARAMS, f"compute unit {compute_unit!r} is {status} on this daemon")
        plan = await asyncio.to_thread(
            build.plan_build,
            system,
            self.install_root,
            target,
            mode=_str_param(params, "mode"),
            compute_unit=compute_unit,
        )

        def on_line(job: jobs.Job, line: str) -> None:
            package = build.started_package(line)
            if package is not None:
                job.detail["built_packages"].append(package)

        def on_exit(job: jobs.Job) -> None:
            built = job.detail["built_packages"]
            own_rebuilt = any(package in built for package in OWN_PACKAGES)
            if self.serves_own_install and own_rebuilt and not self.restart_required:
                self.restart_required = True
                self.hub.publish(TOPIC_DAEMON, {"restart_required": True})
            if system.package in built:
                self.hub.publish(TOPIC_SYSTEMS_CHANGED, {"job": job.id, "package": system.package})

        spec = jobs.JobSpec(
            key=build.BUILD_JOB_KIND,
            kind=build.BUILD_JOB_KIND,
            title=plan.title,
            argv=list(plan.argv),
            cwd=self.workspace,
            params=plan.params,
            detail={"packages": list(plan.packages), "built_packages": []},
            on_line=on_line,
            on_exit=on_exit,
        )
        return (await self.jobs.start(spec)).to_view()

    def _unit_status(self, system: exports.SystemExport, compute_unit: str) -> Optional[str]:
        view = system.to_view(self.served_units, {})
        return next((u["status"] for u in view["compute_units"] if u["name"] == compute_unit), None)

    async def _launch_start(self, params: dict[str, Any]) -> dict[str, Any]:
        system = await self._find_system(_str_param(params, "system", required=True))
        compute_unit = _str_param(params, "compute_unit", required=True)
        status = self._unit_status(system, compute_unit)
        if status is None:
            raise api.ApiError(api.INVALID_PARAMS, f"unknown compute unit {compute_unit!r} of {system.id}")
        mode = _str_param(params, "mode") or system.manifest.get("default_mode")
        if not isinstance(mode, str) or mode not in system.mode_keys():
            raise api.ApiError(api.INVALID_PARAMS, f"unknown mode {mode!r} of {system.id}")
        self._mode_conflict(system.id, mode)
        link = self.peers.link_of(compute_unit)
        if link is not None:
            self.launch.require_mode(system.id, mode)
            return await link.forward("launch.start", {**params, "mode": mode})
        if status != exports.UNIT_AVAILABLE:
            raise api.ApiError(api.INVALID_PARAMS, f"compute unit {compute_unit!r} is {status} on this daemon")
        if not system.structure_path(mode).is_file():
            raise api.ApiError(api.INVALID_PARAMS, f"mode {mode!r} of {system.id} has no system structure")
        deploy = _str_param(params, "deploy")
        if deploy is not None and deploy not in {v["name"] for v in system.deploy_variants()}:
            raise api.ApiError(api.INVALID_PARAMS, f"unknown deploy variant {deploy!r} of {system.id}")
        policy = _str_param(params, "policy") or launch.POLICIES[0]
        if policy not in launch.POLICIES:
            raise api.ApiError(api.INVALID_PARAMS, f"policy must be one of {', '.join(launch.POLICIES)}")
        session = await self.launch.start(
            system,
            compute_unit=compute_unit,
            mode=mode,
            deploy=deploy,
            args=launch.parse_args_param(params.get("args")),
            policy=policy,
            measure=_bool_param(params, "measure"),
            respawn=_bool_param(params, "respawn"),
        )
        return session.to_view()

    async def _launch_status(self, params: dict[str, Any]) -> dict[str, Any]:
        compute_unit = _str_param(params, "compute_unit")
        link = self.peers.link_of(compute_unit)
        if link is not None:
            return await link.forward("launch.status", params)
        sessions = self.launch.views(compute_unit)
        if compute_unit is None:
            sessions += await self.peers.gather(lambda link: link.status_views())
        return {"sessions": sessions}

    def _member_action(self, action: str) -> api.Handler:
        async def handler(params: dict[str, Any]) -> Any:
            compute_unit = _str_param(params, "compute_unit", required=True)
            pattern = _str_param(params, "pattern", required=True)
            exact = _bool_param(params, "exact")
            return await self.launch.call(compute_unit, action, {"pattern": pattern, "exact": exact})

        return handler

    async def _launch_follow(self, params: dict[str, Any]) -> dict[str, Any]:
        compute_unit = _str_param(params, "compute_unit", required=True)
        return await self.launch.follow(
            compute_unit,
            _str_param(params, "path", required=True),
            system_id=_str_param(params, "system"),
            mode=_str_param(params, "mode"),
        )

    async def _launch_unfollow(self, params: dict[str, Any]) -> dict[str, Any]:
        compute_unit = _str_param(params, "compute_unit", required=True)
        return await self.launch.unfollow(
            compute_unit,
            _str_param(params, "path"),
            system_id=_str_param(params, "system"),
            mode=_str_param(params, "mode"),
        )

    async def _launch_runs(self, params: dict[str, Any]) -> dict[str, Any]:
        system_id = _str_param(params, "system")
        system = None if system_id is None else await self._find_system(system_id)
        # A log base shared with a peer holds the runs of its units too; the peer lists those.
        runs = [run for run in self.launch.runs(system) if self.peers.link_of(run["compute_unit"]) is None]
        runs += await self.peers.gather(lambda link: link.run_views(params))
        runs.sort(key=lambda run: run.get("started") or 0, reverse=True)
        return {"runs": runs[: launch.MAX_RUNS]}

    async def _launch_run(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.launch.run_members(_str_param(params, "run", required=True))

    async def _launch_run_tail(self, params: dict[str, Any]) -> dict[str, Any]:
        run = _str_param(params, "run", required=True)
        return self.launch.run_tail(run, _str_param(params, "member", required=True))

    async def _launch_run_rates(self, params: dict[str, Any]) -> dict[str, Any]:
        sample = params.get("sample")
        start = params.get("start")
        span_s = params.get("span_s")
        for name, number in (("sample", sample), ("start", start)):
            if number is not None and (not isinstance(number, int) or isinstance(number, bool) or number < 0):
                raise api.ApiError(api.INVALID_PARAMS, f"{name} must be a sample number")
        if span_s is not None and (not isinstance(span_s, (int, float)) or isinstance(span_s, bool) or span_s <= 0):
            raise api.ApiError(api.INVALID_PARAMS, "span_s must be a positive number of seconds")
        run = _str_param(params, "run", required=True)
        return await self.launch.run_rates(run, sample=sample, start=start, span_s=span_s)

    async def _launch_shutdown(self, params: dict[str, Any]) -> dict[str, Any]:
        return await self.launch.shutdown(_str_param(params, "compute_unit", required=True))

    def _measure(self, verb: str) -> api.Handler:
        async def handler(params: dict[str, Any]) -> Any:
            compute_unit = _str_param(params, "compute_unit", required=True)
            return await self.launch.call(compute_unit, f"measure.{verb}", {})

        return handler

    async def _monitor_follow(self, params: dict[str, Any]) -> dict[str, Any]:
        compute_unit = _str_param(params, "compute_unit", required=True)
        return await self.launch.monitor_follow(compute_unit, _str_param(params, "client") or "")

    async def _monitor_unfollow(self, params: dict[str, Any]) -> dict[str, Any]:
        compute_unit = _str_param(params, "compute_unit", required=True)
        return await self.launch.monitor_unfollow(compute_unit, _str_param(params, "client") or "")

    async def _monitor_sample(self, params: dict[str, Any]) -> dict[str, Any]:
        compute_unit = _str_param(params, "compute_unit", required=True)
        interval = params.get("interval", 1.0)
        if (
            not isinstance(interval, (int, float))
            or isinstance(interval, bool)
            or not 0.1 <= interval <= launch.MONITOR_SAMPLE_MAX_S
        ):
            raise api.ApiError(
                api.INVALID_PARAMS, f"interval must be a number of seconds in [0.1, {launch.MONITOR_SAMPLE_MAX_S:g}]"
            )
        return await self.launch.call(compute_unit, "monitor.sample", {"interval": interval})

    async def _rviz_start(self, params: dict[str, Any]) -> dict[str, Any]:
        return (await self.launch.start_rviz()).to_view()

    # -- connection handling -----------------------------------------------

    async def _on_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        self._connections.add(task)
        try:
            if len(self._connections) > self._max_connections:
                await write_response(
                    writer, _error_response(api.ApiError(api.UNAVAILABLE, "too many open connections"))
                )
                return
            await self._serve_one(reader, writer)
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one connection must not take the daemon down
            logger.exception("unhandled error while serving a connection")
        finally:
            self._connections.discard(task)
            writer.close()

    async def _serve_one(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await read_request(reader)
        except HttpError as exc:
            error = api.ApiError(api.code_for_status(exc.status), exc.message)
            await write_response(writer, Response.json(exc.status, api.error_body(error)))
            return
        logger.debug("%s %s", request.method, request.path)
        try:
            self._check_host(request)
            if request.path == EVENTS_PATH and request.method == "GET":
                self._check_api_fetch(request)
                await self._stream_events(reader, writer)
                return
            response = await self._route(request)
        except api.ApiError as exc:
            response = _error_response(exc)
        await write_response(writer, response, head_only=request.method == "HEAD")

    def _check_host(self, request: Request) -> None:
        if request.header("host") not in self.allowed_hosts:
            raise api.ApiError(api.FORBIDDEN, "Host header does not name this daemon")

    def _check_api_fetch(self, request: Request) -> None:
        if request.header("sec-fetch-site") in ("cross-site", "same-site"):
            raise api.ApiError(api.FORBIDDEN, "cross-site API requests are refused")
        origin = request.header("origin")
        if origin and origin not in {f"http://{host}" for host in self.allowed_hosts}:
            raise api.ApiError(api.FORBIDDEN, f"origin {origin!r} is not allowed")

    async def _route(self, request: Request) -> Response:
        if request.path.startswith(API_PREFIX):
            self._check_api_fetch(request)
            if request.path == RPC_PATH:
                if request.method != "POST":
                    raise api.ApiError(api.METHOD_NOT_ALLOWED, f"{RPC_PATH} accepts POST")
                return await self._rpc(request)
            name = self.methods.method_for_get(request.path[len(API_PREFIX) :])
            if name is None:
                raise api.ApiError(api.NOT_FOUND, f"no API endpoint at {request.path}")
            if request.method not in ("GET", "HEAD"):
                raise api.ApiError(api.METHOD_NOT_ALLOWED, f"{request.path} accepts GET")
            return Response.json(HTTPStatus.OK, api.ok_body(await self._call(name, {})))

        if request.method not in ("GET", "HEAD"):
            raise api.ApiError(api.METHOD_NOT_ALLOWED, "static files accept GET")
        if request.path == "/favicon.ico":
            # Browsers ask here when a page names no icon; every page does, but not every client reads it.
            return await self._file_response(request, assets.web_file(assets.FAVICON))
        if request.path.startswith(BUNDLE_PREFIX):
            relative = request.path[len(BUNDLE_PREFIX) :]
            path = assets.web_file(relative) if relative.split("/")[0] in assets.BUNDLE_SUBDIRS else None
            return await self._file_response(request, path)
        site_path = request.path[1:]
        if site_path == "" or site_path.endswith("/"):
            site_path += INDEX_PAGE
        elif "." not in site_path.rsplit("/", 1)[-1]:
            # A directory named without its slash; its pages link relative to the directory.
            return Response(HTTPStatus.FOUND, [("Location", f"{request.path}/")])
        try:
            text = await asyncio.to_thread(self._render, site_path)
        except (OSError, ValueError) as exc:
            # An export read while a build rewrites it; the next read sees the finished files.
            logger.info("export unreadable for %s: %s", request.path, exc)
            if _is_page(site_path):
                return _status_page(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "Export is being rewritten",
                    "The system's exported files could not be read, as happens while a build rewrites them. "
                    "This page reloads until they are complete.",
                    reload_s=RETRY_RELOAD_S,
                )
            raise api.ApiError(api.UNAVAILABLE, f"export unreadable at {request.path}; retry") from exc
        if text is None:
            logger.info("nothing served at %s", request.path)
            if _is_page(site_path):
                building = self._build_running()
                message = (
                    "A build is running; the system may be absent until its export is written again. "
                    "This page reloads until it is."
                    if building
                    else "No exported system or page matches this address."
                )
                return _status_page(
                    HTTPStatus.NOT_FOUND,
                    "Page not found",
                    message,
                    reload_s=RETRY_RELOAD_S if building else None,
                )
            raise api.ApiError(api.NOT_FOUND, f"nothing served at {request.path}")
        content_type = _content_type(Path(site_path))
        return Response(HTTPStatus.OK, [("Content-Type", content_type), ("Cache-Control", "no-cache")], text.encode())

    def _build_running(self) -> bool:
        return any(job.spec.kind == build.BUILD_JOB_KIND and job.active for job in self.jobs.list())

    def _render(self, site_path: str) -> Optional[str]:
        """A site page drawn from the exports as they are now; each request sees the latest build."""
        systems, _ = exports.discover_systems(self.install_root)
        return Site(systems, record_root=self.record_root).render(site_path)

    async def _file_response(self, request: Request, path: Optional[Path]) -> Response:
        if path is None:
            raise api.ApiError(api.NOT_FOUND, f"nothing served at {request.path}")
        body = await asyncio.to_thread(path.read_bytes)
        return Response(HTTPStatus.OK, [("Content-Type", _content_type(path)), ("Cache-Control", "no-cache")], body)

    async def _rpc(self, request: Request) -> Response:
        media_type = request.header("content-type").split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise api.ApiError(api.UNSUPPORTED_MEDIA_TYPE, "requests must be application/json")
        if not request.header(CLIENT_HEADER):
            raise api.ApiError(api.FORBIDDEN, f"missing {CLIENT_HEADER} header")
        request_id: api.RequestId = None
        try:
            request_id, method, params = api.parse_rpc(request.body)
            result = await self._call(method, params)
        except api.ApiError as exc:
            return _error_response(exc, request_id, with_id=True)
        return Response.json(HTTPStatus.OK, api.ok_body(result, request_id))

    async def _call(self, name: str, params: dict[str, Any]) -> Any:
        try:
            return await self.methods.call(name, params)
        except api.ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported to the caller as an internal error
            logger.exception("API method %s failed", name)
            raise api.ApiError(api.INTERNAL, f"{name} failed: {exc}") from exc

    async def _stream_events(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        subscriber = self.hub.subscribe()
        if subscriber is None:
            await write_response(writer, _error_response(api.ApiError(api.UNAVAILABLE, "too many event streams")))
            return
        eof = asyncio.ensure_future(reader.read(1))
        try:
            writer.write(
                response_head(
                    HTTPStatus.OK,
                    [("Content-Type", "text/event-stream; charset=utf-8"), ("Cache-Control", "no-store")],
                )
            )
            hello = {"pid": os.getpid(), "started_at": self._started_at, "last_seq": self.hub.last_seq}
            writer.write(f"retry: {events.RECONNECT_DELAY_MS}\n\n".encode())
            writer.write(f"event: {events.TOPIC_HELLO}\ndata: {json.dumps(hello, separators=(',', ':'))}\n\n".encode())
            await writer.drain()
            while True:
                get = asyncio.ensure_future(subscriber.queue.get())
                done, _ = await asyncio.wait({get, eof}, return_when=asyncio.FIRST_COMPLETED)
                if get not in done:
                    get.cancel()
                    return
                event = get.result()
                if event is None:
                    return
                writer.write(event.encode())
                await writer.drain()
        finally:
            eof.cancel()
            self.hub.unsubscribe(subscriber)
            if self.hub.subscriber_count == 0:
                self.launch.release_follows()
                self.peers.release_follows()
