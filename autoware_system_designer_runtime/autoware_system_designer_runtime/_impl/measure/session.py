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
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..core.coordinator import Coordinator, CoordinatorBuilder
from .chains import trace_chains
from .node_graph import NodeGraph
from .node_stats import NS_PER_S, analyze
from .probe import ProbeResult, attach_probes, detach_probes, probe_topics
from .trace_reader import read_trace_dir
from .writer import build_latency_file, build_report, write_latency_file, write_report

logger = logging.getLogger(__name__)

TRACER_PACKAGE = "autoware_system_designer_tracer"
TRACER_LIB_ENV = "ASD_TRACER_LIB"
TRACE_DIR_ENV = "ASD_TRACE_DIR"
TRACE_CAPACITY_ENV = "ASD_TRACE_CAPACITY"
TRACE_SUBDIR = "trace"
LATENCY_DIR_NAME = "latency"
SYSTEM_STRUCTURE_DIR = "system_structure"
VISUALIZATION_DIR = "visualization"


@dataclass
class MeasureOptions:
    duration: Optional[float] = None
    settle: float = 5.0
    probe: bool = True
    keep_running: bool = False
    latency_out: Optional[Path] = None
    report_out: Optional[Path] = None


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
    ``<export>/visualization/web/data/``; the diagram renderer fetches
    ``data/<Mode>_latency.json`` from there.
    """
    json_path = Path(json_path).resolve()
    if json_path.parent.name != SYSTEM_STRUCTURE_DIR:
        return None
    web_dir = json_path.parent.parent / VISUALIZATION_DIR / "web"
    if not web_dir.is_dir():
        return None
    return web_dir / "data"


def default_latency_out(mode: str, json_path: Path, log_dir: Path) -> Path:
    """``<Mode>_latency.json`` in the export's visualization bundle, else under the log dir."""
    data_dir = export_bundle_data_dir(json_path)
    base = data_dir if data_dir is not None else log_dir / LATENCY_DIR_NAME
    return base / f"{mode}_latency.json"


def report_path_for(latency_out: Path) -> Path:
    stem = latency_out.stem
    if stem.endswith("_latency"):
        stem = stem[: -len("_latency")]
    return latency_out.with_name(f"{stem}_measure_report.md")


def analyze_traces(
    trace_dir: Path,
    graph: NodeGraph,
    *,
    window_start_ns: Optional[int],
    window_end_ns: Optional[int],
    mode: Optional[str],
    probe: Optional[bool],
    latency_out: Path,
    report_out: Path,
    started_ns: Optional[int] = None,
) -> dict[str, Any]:
    """Blocking: read the trace directory, analyze, write the latency file and the report."""
    trace_set = read_trace_dir(trace_dir)
    span_start, span_end = trace_set.time_span()
    t0 = window_start_ns if window_start_ns is not None else span_start
    t1 = window_end_ns if window_end_ns is not None else span_end
    analysis = analyze(trace_set, graph, t0, t1)
    chains = trace_chains(analysis)
    data, diffs = build_latency_file(graph, analysis, chains, mode=mode, probe=bool(probe), started_ns=started_ns)
    if probe is None:
        data["run"]["probe"] = None
    write_latency_file(data, latency_out)
    write_report(build_report(graph, analysis, data, diffs, chains), report_out)
    return data


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
        self._report_out = options.report_out or report_path_for(latency_out)
        self._mode = mode
        self._tracer = locate_tracer()
        self._state = "armed"
        self._started_ns: Optional[int] = None
        self._stopped_ns: Optional[int] = None
        self._probe_result: Optional[ProbeResult] = None
        self._lock = asyncio.Lock()
        self._result: Optional[dict[str, Any]] = None

    # ---- wiring -------------------------------------------------------------

    def install(self, builder: CoordinatorBuilder) -> None:
        builder.add_pre_start_hook(self._pre_start)
        builder.add_post_start_hook(self._post_start)
        builder.add_shutdown_hook(self._on_shutdown)

    async def _pre_start(self, coord: Coordinator) -> None:
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        for spec in coord.specs():
            spec.env = tracer_env(self._tracer, self._trace_dir, spec.env)
        logger.info("measure: tracer %s armed, traces in %s", self._tracer, self._trace_dir)

    async def _post_start(self, coord: Coordinator) -> None:
        coord.schedule_task(self._auto(coord))

    async def _auto(self, coord: Coordinator) -> None:
        await coord.launch_ready.wait()
        if coord.shutdown_event.is_set():
            return
        logger.info(self._log_prefix() + await self.start())
        if self._options.duration is None:
            return
        total = self._options.settle + self._options.duration
        try:
            await asyncio.wait_for(coord.shutdown_event.wait(), timeout=total)
            return  # shutdown fired first; the shutdown hook finalizes
        except asyncio.TimeoutError:
            pass
        logger.info(self._log_prefix() + await self.stop())
        if not self._options.keep_running:
            coord.request_shutdown()

    async def _on_shutdown(self, coord: Coordinator) -> None:
        if self._state == "running":
            logger.info(self._log_prefix() + await self.stop())

    # ---- control ------------------------------------------------------------

    async def start(self) -> str:
        async with self._lock:
            if self._state == "running":
                return "measurement already running"
            if self._state in ("analyzing", "done"):
                return f"measurement {self._state}; one window per launch"
            self._started_ns = time.time_ns()
            self._state = "running"
            if self._options.probe:
                topics = probe_topics(self._graph)
                self._probe_result = await attach_probes(self._worker, topics)
                probe_note = f", {len(self._probe_result.attached)}/{len(topics)} probe subscriptions"
            else:
                probe_note = ", probes off"
            return f"measurement started (settle {self._options.settle:g} s{probe_note})"

    async def stop(self) -> str:
        async with self._lock:
            if self._state != "running":
                return f"measurement is {self._state}, nothing to stop"
            self._stopped_ns = time.time_ns()
            self._state = "analyzing"
            if self._options.probe:
                await detach_probes(self._worker)
            window_start = self._started_ns + int(self._options.settle * NS_PER_S)
            if window_start >= self._stopped_ns:
                window_start = self._started_ns
            loop = asyncio.get_running_loop()
            try:
                self._result = await loop.run_in_executor(
                    None,
                    lambda: analyze_traces(
                        self._trace_dir,
                        self._graph,
                        window_start_ns=window_start,
                        window_end_ns=self._stopped_ns,
                        mode=self._mode,
                        probe=self._options.probe,
                        latency_out=self._latency_out,
                        report_out=self._report_out,
                        started_ns=self._started_ns,
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._state = "failed"
                logger.exception("measure: analysis failed")
                return f"analysis failed: {exc}"
            self._state = "done"
            run = self._result["run"]
            return (
                f"measurement done: {run['window_s']} s window, {len(self._result['nodes'])} nodes, "
                f"{len(self._result['links'])} links, {len(self._result['chains'])} chains -> "
                f"{self._latency_out} (report {self._report_out})"
            )

    def status(self) -> str:
        parts = [f"state={self._state}", f"traces={self._trace_dir}"]
        if self._started_ns is not None:
            elapsed = (time.time_ns() - self._started_ns) / NS_PER_S
            parts.append(f"started {elapsed:.1f} s ago")
        if self._probe_result is not None:
            parts.append(f"probes={len(self._probe_result.attached)} (failed {len(self._probe_result.failed)})")
        parts.append(f"out={self._latency_out}")
        return ", ".join(parts)

    @property
    def state(self) -> str:
        return self._state

    @property
    def trace_dir(self) -> Path:
        return self._trace_dir

    @property
    def latency_out(self) -> Path:
        return self._latency_out

    @staticmethod
    def _log_prefix() -> str:
        return "measure: "
