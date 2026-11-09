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

"""CLI entry: launch a system_structure JSON via the actor runtime.

The XML LaunchService backend and ``build_launch_description`` have been
replaced by the actor coordinator in
:mod:`autoware_system_designer_runtime`. Each member of the system runs
as its own supervised subprocess, with composable nodes loaded directly
via the ``composition_interfaces/srv/LoadNode`` service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._impl.core.commands import MemberStates, RuntimeCommands
from ._impl.core.config import ActorConfig
from ._impl.core.control_channel import ControlChannel
from ._impl.core.coordinator import ensure_output_dir
from ._impl.core.stdin_console import run_console
from ._impl.measure.clock import CLOCK_AUTO, CLOCK_CHOICES
from ._impl.measure.node_graph import NodeGraph
from ._impl.measure.session import MeasureOptions, MeasureSession, default_latency_out
from ._impl.ros2.builder import populate_builder
from ._impl.ros2.common.substitutions import bind_variables, resolve_argument_values, unresolved_var_names
from ._impl.ros2.operation_mode import POLICIES, OperationModeGuard
from .export_tree import manifest_path, read_manifest, resolve_structure_paths

logger = logging.getLogger("autoware_system_designer")


_NODE_PREFIX = re.compile(r"^\[([^\]]+)\] (.*)", re.DOTALL)


def _deploy_arguments(
    manifest: Mapping[str, Any],
    deploy: Optional[str],
    overrides: Mapping[str, str],
) -> dict[str, str]:
    """Argument bindings for one deploy variant, with ``--arg`` values layered on top."""
    variants = {v.get("name"): v for v in manifest.get("deploy_variants", []) if isinstance(v, dict)}
    arguments: dict[str, str] = {}
    if deploy is not None:
        variant = variants.get(deploy)
        if variant is None:
            known = ", ".join(sorted(n for n in variants if n)) or "none"
            raise RuntimeError(f"Unknown deploy variant {deploy!r}; available: {known}")
        for item in variant.get("arguments", variant.get("variables", [])):
            if isinstance(item, dict) and item.get("name"):
                arguments[item["name"]] = str(item.get("value", ""))
    arguments.update({k: str(v) for k, v in overrides.items()})
    return resolve_argument_values(arguments)


def _bind_deploy_arguments(
    data: Any,
    json_path: str,
    *,
    deploy: Optional[str],
    overrides: Mapping[str, str],
) -> Any:
    """Resolve the system arguments a structure leaves as ``$(var ...)``; every one must be bound."""
    manifest = read_manifest(Path(json_path))
    if deploy is not None and manifest is None:
        raise RuntimeError(f"--deploy {deploy!r} needs the export manifest {manifest_path(Path(json_path))}, not found")
    arguments = _deploy_arguments(manifest or {}, deploy, overrides)
    if arguments:
        logger.info("deploy arguments: %s", ", ".join(f"{k}={v}" for k, v in sorted(arguments.items())))
    resolved = bind_variables(data, arguments)
    missing = unresolved_var_names(resolved)
    if missing:
        variants = [v.get("name") for v in (manifest or {}).get("deploy_variants", []) if isinstance(v, dict)]
        hint = f"--deploy <{'|'.join(n for n in variants if n)}>" if variants else "--arg NAME=VALUE"
        raise RuntimeError(
            f"Unbound system argument(s) in {json_path}: {', '.join(sorted(missing))}; bind them with {hint}."
        )
    return resolved


def _parse_arg_overrides(items: Optional[Sequence[str]]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep or not name:
            raise SystemExit(f"--arg expects NAME=VALUE, got {item!r}")
        overrides[name] = value
    return overrides


class _ShortNameFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        m = _NODE_PREFIX.match(msg)
        if m:
            record.short_name = f"Autoware Runtime {m.group(1)}"
            record.msg = m.group(2)
            record.args = ()
        else:
            record.short_name = "Autoware Runtime"
        return True


RUN_MANIFEST = "run.json"


def write_run_manifest(
    out_dir: Path,
    *,
    json_path: str,
    data: Mapping[str, Any],
    ecu: Optional[str],
    deploy: Optional[str],
    members: Sequence[str],
) -> Path:
    """Records what a run's log directory holds: the system structure and unit it ran and the member
    paths whose directories lie beside it."""
    metadata = data.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    manifest = {
        "system": metadata.get("system_name"),
        "mode": metadata.get("mode") or Path(json_path).stem,
        "compute_unit": ecu,
        "deploy": deploy,
        "system_structure": str(Path(json_path).resolve()),
        "started": time.time(),
        "members": list(members),
    }
    path = out_dir / RUN_MANIFEST
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


def launch_from_json(
    json_path: str,
    *,
    ecu: Optional[str] = None,
    deploy: Optional[str] = None,
    arguments: Optional[Mapping[str, str]] = None,
    output_dir: Optional[Path] = None,
    respawn: bool = False,
    respawn_delay: float = 1.0,
    max_respawn_attempts: Optional[int] = None,
    graceful_shutdown_timeout: float = 5.0,
    interactive: bool = False,
    measure: Optional[MeasureOptions] = None,
    control: Optional[Path] = None,
    control_policy: str = "guard",
) -> int:
    with open(json_path) as f:
        data = json.load(f)
    data = resolve_structure_paths(data, json_path)
    data = _bind_deploy_arguments(data, json_path, deploy=deploy, overrides=arguments or {})

    out_dir = output_dir or ensure_output_dir()
    logger.info("logs: %s", out_dir)

    graph: Optional[NodeGraph] = None
    latency_out: Optional[Path] = None
    if measure is not None:
        graph = NodeGraph.from_system_structure(data, ecu=ecu)
        mode = graph.mode or Path(json_path).stem
        latency_out = measure.latency_out or default_latency_out(mode, Path(json_path), out_dir)

    config = ActorConfig(
        respawn_enabled=respawn,
        respawn_delay=respawn_delay,
        max_respawn_attempts=max_respawn_attempts,
        output_dir=out_dir,
        graceful_shutdown_timeout=graceful_shutdown_timeout,
    )

    async def _run() -> int:
        builder, worker = populate_builder(data["data"], ecu=ecu, config=config)
        session: Optional[MeasureSession] = None
        if measure is not None and graph is not None and latency_out is not None:
            session = MeasureSession(
                graph,
                worker,
                measure,
                log_dir=out_dir,
                latency_out=latency_out,
                mode=graph.mode or Path(json_path).stem,
            )
            session.install(builder)
        states = MemberStates()
        states.install(builder)
        coord = builder.build()
        try:
            write_run_manifest(out_dir, json_path=json_path, data=data, ecu=ecu, deploy=deploy, members=coord.names())
        except OSError as exc:
            logger.warning("cannot write %s: %s", out_dir / RUN_MANIFEST, exc)
        console_task = None
        channel: Optional[ControlChannel] = None
        try:
            await worker.start()
            guard = None
            if (interactive or control is not None) and control_policy == "guard":
                guard = OperationModeGuard(worker)
                await guard.install()
            commands = RuntimeCommands(coord, states, measure=session, guard=guard)
            if control is not None:
                channel = ControlChannel(commands, states, socket_path=control, log_dir=out_dir, policy=control_policy)
                await channel.start()
            if interactive:
                console_task = asyncio.ensure_future(run_console(commands))
            return await coord.run()
        finally:
            if console_task is not None and not console_task.done():
                console_task.cancel()
                try:
                    await asyncio.wait_for(console_task, timeout=0.5)
                except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    pass
            if channel is not None:
                await channel.close()
            await worker.stop()

    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("shutdown via KeyboardInterrupt (signal arrived before actor runtime was ready)")
        return 130


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch an Autoware system from a system_structure JSON file " "under per-process supervision."
    )
    parser.add_argument(
        "json_file",
        help="Path to system_structure JSON " "(e.g. .../system_structure/LoggingSimulation.json)",
    )
    parser.add_argument(
        "--compute-unit",
        default=None,
        help="Only launch nodes whose compute_unit matches this value "
        "(e.g. main_ecu, dummy_ecu). When omitted, all nodes are launched.",
    )
    parser.add_argument(
        "--deploy",
        default=None,
        help="Deploy variant from the export's deployment.json whose arguments bind "
        "the system arguments ($(var ...)).",
    )
    parser.add_argument(
        "--arg",
        action="append",
        metavar="NAME=VALUE",
        default=None,
        help="Bind one system argument directly; overrides the deploy variant's value. Repeatable.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=None,
        help="Base directory for per-node log output. Default: a fresh "
        "timestamped folder under /tmp/autoware_system_designer_logs.",
    )
    parser.add_argument(
        "--respawn",
        action="store_true",
        help="Restart processes that exit non-cleanly.",
    )
    parser.add_argument(
        "--respawn-delay",
        type=float,
        default=1.0,
        help="Seconds to wait before respawning (default: 1.0).",
    )
    parser.add_argument(
        "--max-respawn-attempts",
        type=int,
        default=None,
        help="Cap on consecutive respawn attempts (default: unlimited).",
    )
    parser.add_argument(
        "--graceful-shutdown-timeout",
        type=float,
        default=5.0,
        help="Seconds to wait after SIGTERM before escalating to SIGKILL " "(default: 5.0).",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Read commands from stdin while running: status, stop <name>, " "restart <name>, kill <name>, quit.",
    )
    parser.add_argument(
        "--control",
        type=Path,
        default=None,
        metavar="SOCKET",
        help="Serve the control channel on this Unix socket: member states and logs out, "
        "the console command set in, as JSON lines.",
    )
    parser.add_argument(
        "--control-policy",
        default="guard",
        choices=POLICIES,
        help="guard refuses stop, restart, kill and shutdown while the operation mode is autonomous "
        "(default); open does not interlock.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    measure_group = parser.add_argument_group(
        "latency measurement",
        "Preload the rcl tracer into every process and analyze node process time, "
        "topic communication and event chains into a latency file.",
    )
    measure_group.add_argument(
        "--measure",
        action="store_true",
        help="Arm the tracer and open the measurement window once every actor has started.",
    )
    measure_group.add_argument(
        "--measure-duration",
        type=float,
        default=None,
        metavar="S",
        help="Close the window after S seconds (after the settle time), shut the system down "
        "and analyze once the actors have terminated.",
    )
    measure_group.add_argument(
        "--measure-settle",
        type=float,
        default=5.0,
        metavar="S",
        help="Seconds after launch_ready excluded from the window (default: 5).",
    )
    measure_group.add_argument(
        "--measure-keep-running",
        action="store_true",
        help="With --measure-duration, keep the system running after the analysis.",
    )
    measure_group.add_argument(
        "--no-probe",
        action="store_true",
        help="Do not add probe subscriptions on intra-process-only topics.",
    )
    measure_group.add_argument(
        "--measure-clock",
        default=CLOCK_AUTO,
        choices=CLOCK_CHOICES,
        help="Time base of durations and rates: ros when the system runs on /clock (use_sim_time), "
        "wall otherwise; auto picks by the trace (default).",
    )
    measure_group.add_argument(
        "--latency-out",
        type=Path,
        default=None,
        metavar="FILE",
        help="Latency file to write, JSON. Default: a new record latency/<Mode>/<stamp>.json of the export "
        "under the record root ($AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR, else $ROS_HOME/autoware_system_designer/records), "
        "else <log-dir>/latency/<Mode>_latency.json.",
    )
    args = parser.parse_args()

    measure: Optional[MeasureOptions] = None
    if args.measure or args.measure_duration is not None:
        measure = MeasureOptions(
            duration=args.measure_duration,
            settle=args.measure_settle,
            probe=not args.no_probe,
            keep_running=args.measure_keep_running,
            latency_out=args.latency_out,
            clock=args.measure_clock,
        )

    short_name_filter = _ShortNameFilter()
    handler = logging.StreamHandler()
    handler.addFilter(short_name_filter)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(short_name)s - %(message)s", datefmt="%H:%M:%S"))
    logging.root.setLevel(getattr(logging, args.log_level))
    logging.root.addHandler(handler)

    sys.exit(
        launch_from_json(
            args.json_file,
            ecu=args.compute_unit,
            deploy=args.deploy,
            arguments=_parse_arg_overrides(args.arg),
            output_dir=args.log_dir,
            respawn=args.respawn,
            respawn_delay=args.respawn_delay,
            max_respawn_attempts=args.max_respawn_attempts,
            graceful_shutdown_timeout=args.graceful_shutdown_timeout,
            interactive=args.interactive,
            measure=measure,
            control=args.control,
            control_policy=args.control_policy,
        )
    )
