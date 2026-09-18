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

"""Measurement session: arms the tracer on every spawned process, owns the window, runs the analysis.

Tracing is armed at spawn (``LD_PRELOAD`` + ``ASD_TRACE_DIR``); the session only
decides which part of the recording is analyzed. Probe subscriptions are attached
when the window opens and removed when it closes.

Lifecycle: ``armed`` → ``running`` → ``closed`` → ``analyzing`` → ``done`` | ``failed``.
Every transition is logged; while the window is open a heartbeat reports the phase,
the time left and the trace counters.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..core import events as ev
from ..core.coordinator import Coordinator, CoordinatorBuilder
from .chains import trace_chains
from .clock import CLOCK_AUTO, clock_for
from .node_graph import NodeGraph
from .node_stats import NS_PER_S, ProcessExit, analyze
from .probe import ProbeResult, attach_probes, detach_probes, probe_topics
from .trace_reader import read_trace_dir, trace_dir_progress
from .writer import build_latency_file, write_latency_file

logger = logging.getLogger(__name__)

TRACER_PACKAGE = "autoware_system_designer_tracer"
TRACER_LIB_ENV = "ASD_TRACER_LIB"
TRACE_DIR_ENV = "ASD_TRACE_DIR"
TRACE_CAPACITY_ENV = "ASD_TRACE_CAPACITY"
TRACE_SUBDIR = "trace"
LATENCY_DIR_NAME = "latency"
SYSTEM_STRUCTURE_DIR = "system_structure"
VISUALIZATION_DIR = "visualization"
DEFAULT_HEARTBEAT_S = 10.0


@dataclass
class MeasureOptions:
    duration: Optional[float] = None
    settle: float = 5.0
    probe: bool = True
    keep_running: bool = False
    latency_out: Optional[Path] = None
    heartbeat: float = DEFAULT_HEARTBEAT_S
    # auto: ROS time when the traced processes ran on /clock, else wall time.
    clock: str = CLOCK_AUTO


def locate_tracer() -> Path:
    """The preload library, from ``ASD_TRACER_LIB`` or the ament index."""
    override = os.environ.get(TRACER_LIB_ENV)
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise FileNotFoundError(f"{TRACER_LIB_ENV}={override} is not a file")
    try:
        from ament_index_python.packages import PackageNotFoundError, get_package_prefix
    except ImportError as exc:  # pragma: no cover - ament is part of every ROS install
        raise FileNotFoundError("ament_index_python is not available") from exc
    try:
        prefix = Path(get_package_prefix(TRACER_PACKAGE))
    except PackageNotFoundError as exc:
        raise FileNotFoundError(f"package {TRACER_PACKAGE} is not installed; build it or set {TRACER_LIB_ENV}") from exc
    path = prefix / "lib" / f"lib{TRACER_PACKAGE}.so"
    if not path.is_file():
        raise FileNotFoundError(f"tracer library missing at {path}")
    return path


def tracer_env(tracer: Path, trace_dir: Path, base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Environment of a traced process: the preload prepended, the trace directory set."""
    env = dict(base if base is not None else os.environ)
    preload = str(tracer)
    existing = env.get("LD_PRELOAD", "")
    if existing and preload not in existing.split(":"):
        preload = f"{preload}:{existing}"
    elif existing:
        preload = existing
    env["LD_PRELOAD"] = preload
    env[TRACE_DIR_ENV] = str(trace_dir)
    return env


def export_bundle_data_dir(json_path: Path) -> Optional[Path]:
    """The visualization bundle's data directory of the export a system_structure JSON belongs to.

    An export lays out ``<export>/system_structure/<Mode>.json`` beside
    ``<export>/visualization/web/data/``; the diagram renderer loads
    ``data/<Mode>_latency.js`` from there.
    """
    json_path = Path(json_path).resolve()
    if json_path.parent.name != SYSTEM_STRUCTURE_DIR:
        return None
    web_dir = json_path.parent.parent / VISUALIZATION_DIR / "web"
    if not web_dir.is_dir():
        return None
    return web_dir / "data"


def default_latency_out(mode: str, json_path: Path, log_dir: Path) -> Path:
    """``<Mode>_latency.js`` in the export's visualization bundle, else under the log dir."""
    data_dir = export_bundle_data_dir(json_path)
    base = data_dir if data_dir is not None else log_dir / LATENCY_DIR_NAME
    return base / f"{mode}_latency.js"


def analyze_traces(
    trace_dir: Path,
    graph: NodeGraph,
    *,
    window_start_ns: Optional[int],
    window_end_ns: Optional[int],
    mode: Optional[str],
    probe: Optional[bool],
    latency_out: Path,
    started_ns: Optional[int] = None,
    clock: str = CLOCK_AUTO,
    exits: Optional[dict[int, ProcessExit]] = None,
    settle_ns: int = 0,
) -> dict[str, Any]:
    """Blocking: read the trace directory, analyze, write the latency file.

    ``settle_ns`` is skipped after the window start, whether given or the first record.
    """
    trace_set = read_trace_dir(trace_dir)
    span_start, span_end = trace_set.time_span()
    t0 = (window_start_ns if window_start_ns is not None else span_start) + settle_ns
    t1 = window_end_ns if window_end_ns is not None else span_end
    time_base = clock_for(trace_set, clock)
    logger.info("measure: durations in %s", time_base.describe())
    analysis = analyze(trace_set, graph, t0, t1, clock=time_base, exits=exits)
    chains = trace_chains(analysis)
    data, _diffs = build_latency_file(graph, analysis, chains, mode=mode, probe=bool(probe), started_ns=started_ns)
    if probe is None:
        data["run"]["probe"] = None
    write_latency_file(data, latency_out)
    return data


def _clock(t_s: float) -> str:
    return datetime.fromtimestamp(t_s).strftime("%H:%M:%S")


class MeasureSession:
    """One measurement of one launched system."""

    def __init__(
        self,
        graph: NodeGraph,
        worker,
        options: MeasureOptions,
        *,
        log_dir: Path,
        latency_out: Path,
        mode: Optional[str],
    ) -> None:
        self._graph = graph
        self._worker = worker
        self._options = options
        self._trace_dir = log_dir / TRACE_SUBDIR
        self._latency_out = latency_out
        self._mode = mode
        self._tracer = locate_tracer()
        self._state = "armed"
        self._started_ns: Optional[int] = None
        self._stopped_ns: Optional[int] = None
        self._probe_result: Optional[ProbeResult] = None
        self._lock = asyncio.Lock()
        self._result: Optional[dict[str, Any]] = None
        # Actor lifecycle by pid, so a process that ends inside the window reaches the latency file.
        self._pids: dict[str, int] = {}
        self._exits: dict[int, ProcessExit] = {}

    # ---- wiring -------------------------------------------------------------

    def install(self, builder: CoordinatorBuilder) -> None:
        builder.add_pre_start_hook(self._pre_start)
        builder.add_post_start_hook(self._post_start)
        builder.add_shutdown_hook(self._on_shutdown)
        builder.add_state_hook(self.on_state_event)

    async def _pre_start(self, coord: Coordinator) -> None:
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        for spec in coord.specs():
            spec.env = tracer_env(self._tracer, self._trace_dir, spec.env)
        logger.info("measure: tracer %s armed, traces in %s", self._tracer, self._trace_dir)

    async def _post_start(self, coord: Coordinator) -> None:
        coord.schedule_task(self._auto(coord))

    async def _auto(self, coord: Coordinator) -> None:
        """Opens the window at launch_ready, heartbeats, closes it on the deadline or at shutdown."""
        await coord.launch_ready.wait()
        if coord.shutdown_event.is_set():
            logger.info("measure: shutdown before launch_ready, the window never opened")
            return
        logger.info("measure: %s", await self.start())
        while self._state == "running":
            timeout = self._options.heartbeat
            remaining = self._remaining_s()
            if remaining is not None:
                timeout = min(timeout, max(remaining, 0.0))
            try:
                await asyncio.wait_for(coord.shutdown_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
            else:
                logger.info("measure: %s", await self.close("shutdown requested"))
                return  # the shutdown hook analyzes once the actors are down
            if self._state != "running":
                return  # closed from the console
            if remaining is not None and self._remaining_s() <= 0:
                break
            logger.info("measure: %s", self.status())
        logger.info("measure: %s", await self.close("duration elapsed"))
        if self._options.keep_running:
            logger.info("measure: %s", await self.analyze())
            return
        logger.info(
            "measure: --measure-duration elapsed, shutting the system down; "
            "the analysis runs once the actors have terminated (--measure-keep-running keeps it up)"
        )
        coord.request_shutdown()

    def on_state_event(self, event) -> None:
        if isinstance(event, ev.Started):
            self._pids[event.name] = event.pid
        elif isinstance(event, ev.Exited):
            pid = self._pids.pop(event.name, None)
            if pid is None:
                return
            self._exits[pid] = ProcessExit(pid=pid, t_ns=time.time_ns(), exit_code=event.exit_code, actor=event.name)
            if self._state == "running":
                logger.warning("measure: [%s] exited code=%s inside the window", event.name, event.exit_code)

    @property
    def exits(self) -> dict[int, ProcessExit]:
        return dict(self._exits)

    async def _on_shutdown(self, coord: Coordinator) -> None:
        if self._state == "running":
            logger.info("measure: %s", await self.close("shutdown"))
        if self._state == "closed":
            logger.info("measure: %s", await self.analyze())

    # ---- control ------------------------------------------------------------

    async def start(self) -> str:
        async with self._lock:
            if self._state == "running":
                return "measurement already running"
            if self._state != "armed":
                return f"measurement {self._state}; one window per launch"
            self._started_ns = time.time_ns()
            self._state = "running"
            if self._options.probe:
                topics = probe_topics(self._graph)
                self._probe_result = await attach_probes(self._worker, topics)
                probe_note = f", {len(self._probe_result.attached)}/{len(topics)} probe subscriptions"
            else:
                probe_note = ", probes off"
            duration = self._options.duration
            if duration is None:
                plan = "closes at shutdown or on 'measure stop'"
            else:
                plan = f"record {duration:g} s, closes at {_clock(self._deadline_s())}"
            return f"window open: settle {self._options.settle:g} s, {plan}{probe_note}"

    async def close(self, reason: str) -> str:
        """Ends the recording window; the analysis is a separate step."""
        async with self._lock:
            if self._state != "running":
                return f"measurement is {self._state}, nothing to close"
            self._stopped_ns = time.time_ns()
            self._state = "closed"
            if self._options.probe:
                await detach_probes(self._worker)
            window_s = (self._stopped_ns - (self._window_start_ns() or self._stopped_ns)) / NS_PER_S
            progress = trace_dir_progress(self._trace_dir)
            return f"window closed ({reason}): {window_s:.1f} s recorded, {progress.describe()}"

    async def analyze(self) -> str:
        async with self._lock:
            if self._state != "closed":
                return f"measurement is {self._state}, nothing to analyze"
            self._state = "analyzing"
            progress = trace_dir_progress(self._trace_dir)
            logger.info("measure: analyzing %s from %s", progress.describe(), self._trace_dir)
            t_begin = time.monotonic()
            loop = asyncio.get_running_loop()
            try:
                self._result = await loop.run_in_executor(
                    None,
                    lambda: analyze_traces(
                        self._trace_dir,
                        self._graph,
                        window_start_ns=self._window_start_ns(),
                        window_end_ns=self._stopped_ns,
                        mode=self._mode,
                        probe=self._options.probe,
                        latency_out=self._latency_out,
                        started_ns=self._started_ns,
                        clock=self._options.clock,
                        exits=self._exits,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._state = "failed"
                logger.exception("measure: analysis failed")
                return f"analysis failed: {exc}; traces kept in {self._trace_dir}"
            self._state = "done"
            run = self._result["run"]
            summary = self._result["summary"]
            return (
                f"analysis done in {time.monotonic() - t_begin:.1f} s: {run['window_s']} s window "
                f"({run['clock']['base']} time), "
                f"{summary['observed_nodes']}/{summary['design_nodes']} design nodes observed, "
                f"{len(self._result['links'])} links, {len(self._result['chains'])} chains -> {self._latency_out}"
            )

    def status(self) -> str:
        if self._state == "armed":
            return f"armed, waiting for launch_ready; traces in {self._trace_dir}"
        if self._state == "running":
            elapsed = (time.time_ns() - self._started_ns) / NS_PER_S
            settle = self._options.settle
            if elapsed < settle:
                phase = f"settling {elapsed:.1f}/{settle:g} s"
            elif self._options.duration is None:
                phase = f"recording {elapsed - settle:.1f} s, open until shutdown or 'measure stop'"
            else:
                phase = f"recording {elapsed - settle:.1f}/{self._options.duration:g} s"
            parts = [phase, trace_dir_progress(self._trace_dir).describe()]
            if self._probe_result is not None and self._probe_result.failed:
                parts.append(f"{len(self._probe_result.failed)} probe subscriptions failed")
            return ", ".join(parts)
        if self._state == "done":
            return f"done -> {self._latency_out}"
        return f"{self._state}; traces in {self._trace_dir}"

    @property
    def state(self) -> str:
        return self._state

    @property
    def trace_dir(self) -> Path:
        return self._trace_dir

    @property
    def latency_out(self) -> Path:
        return self._latency_out

    # ---- window arithmetic ----------------------------------------------------

    def _window_start_ns(self) -> Optional[int]:
        """Start of the analyzed window; a window shorter than the settle time keeps everything."""
        if self._started_ns is None:
            return None
        start = self._started_ns + int(self._options.settle * NS_PER_S)
        if self._stopped_ns is not None and start >= self._stopped_ns:
            return self._started_ns
        return start

    def _deadline_s(self) -> Optional[float]:
        if self._started_ns is None or self._options.duration is None:
            return None
        return self._started_ns / NS_PER_S + self._options.settle + self._options.duration

    def _remaining_s(self) -> Optional[float]:
        deadline = self._deadline_s()
        return None if deadline is None else deadline - time.time()
