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

"""CLI entry: deterministic replay of a scope of a system_structure JSON from a rosbag.

The scope's members run as in a launch, each gated by the preloaded tracer. The
replay driver feeds the scope's boundary inputs from the bag, runs one callback
at a time in a fixed order, records the outputs in driver time and compares them
with a reference.

Exit codes: 0 the run completed (and matched its reference, if one exists), 1 the
outputs differ from the reference or ``--strict`` found order violations, 2 the
replay did not run to its end (pre-flight, startup or a blocked callback).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Collection, Mapping, Optional, Sequence

from ._impl.core import events as ev
from ._impl.core.config import ActorConfig
from ._impl.core.coordinator import ensure_output_dir
from ._impl.measure.node_graph import NodeGraph
from ._impl.measure.session import TRACE_SUBDIR
from ._impl.measure.tracer import TracerArming, locate_tracer
from ._impl.replay import reference as ref
from ._impl.replay.bag import (
    BagError,
    BagInfo,
    BagMessages,
    BagSink,
    LeadIn,
    read_bag_info,
    read_messages,
    remap_topics,
)
from ._impl.replay.config import ConfigError, ReplayConfig
from ._impl.replay.driver import DriverOptions, ReplayDriver, ReplayError, ReplayResult
from ._impl.replay.link import CLOCK_FREE, SOCKET_ENV, START_ENV, TIMER, WALL_TIMERS_ENV, Endpoint, LinkServer
from ._impl.replay.middleware import replay_middleware
from ._impl.replay.preflight import (
    ERROR,
    Problem,
    RealEndpoint,
    check_bag,
    check_endpoints,
    fed_topics,
    has_errors,
)
from ._impl.replay.recorder import OutputRecorder
from ._impl.replay.scope import (
    Boundary,
    Scope,
    ScopeError,
    compute_boundary,
    replay_structure,
    scope_launch_files,
    scope_node_fqns,
    select_scope,
    unconnected_ports,
)
from ._impl.replay.verdict import Verdict, compare_outputs, digest, group_by_topic, ros_deserialize
from .export_tree import resolve_structure_paths
from .replay_record import (
    BOUNDARY_FILE,
    EXIT_DIFFERS,
    EXIT_FAILED,
    EXIT_OK,
    OPTIONS_FILE,
    PHASE_POST,
    PHASE_PRE,
    PHASE_REPLAY,
    REPLAY_SUBDIR,
    RESULT_FILE,
    STEP_LOG_FILE,
    VERDICT_FILE,
    progress_message,
)
from .run_log import LOG_BASE, REPLAY_LOGS, write_run_manifest
from .special_topics import special_topics

logger = logging.getLogger("autoware_system_designer")

SOCKET_FILE = "driver.sock"
RESULT_FORMAT = "autoware_system_designer/replay_result/1"

_VIOLATION_NOUNS = {"publish": "publishes", "take": "takes", "timer": "timer fires"}


@dataclass
class ReplayOptions:
    scope: Sequence[str] = ()
    bag: Optional[Path] = None
    config: Optional[Path] = None
    # Recorded bag topic to the topic it is fed as, over the configuration's remap.
    remap: Mapping[str, str] = field(default_factory=dict)
    ecu: Optional[str] = None
    deploy: Optional[str] = None
    arguments: Mapping[str, str] = field(default_factory=dict)
    output_dir: Optional[Path] = None
    # Explicit reference directory; without it the keyed reference of the export is used.
    reference: Optional[Path] = None
    save_reference: bool = False
    # Stop after the boundary report and the bag pre-flight.
    check_only: bool = False
    # Launch the scope, compare its real endpoints with the design and stop; needs no bag.
    check_endpoints: bool = False
    allow_undeclared: bool = False
    duration: Optional[float] = None
    warmup: float = 0.0
    seed: Optional[int] = None
    strict: bool = False
    isolate: bool = True
    domain_id: Optional[int] = None
    free_wall_timers: bool = False
    # Write every step and every line of the gated processes to replay/steps.log.
    step_log: bool = False
    startup_timeout: float = 60.0
    startup_settle: float = 2.0
    callback_timeout: float = 30.0
    graceful_shutdown_timeout: float = 5.0


def _log_problems(problems: Sequence[Problem]) -> None:
    for problem in problems:
        (logger.error if problem.level == ERROR else logger.warning)("replay: %s", problem.message)


def _log_boundary(scope: Scope, boundary: Boundary) -> None:
    logger.info("replay scope: %d node(s), %d container(s)", len(scope.nodes), len(scope.containers))
    for entry in boundary.inputs:
        logger.info(
            "  input  %s (%s) <- %s", entry.topic, entry.msg_type, ", ".join(entry.publishers) or "no publisher"
        )
    for entry in boundary.outputs:
        if entry.is_boundary:
            logger.info("  output %s (%s)", entry.topic, entry.msg_type)
    outside = [edge for edge in boundary.services if not edge.scope_peers]
    if outside:
        logger.info("  %d service port(s) leave the scope", len(outside))


def _progress(phase: str, step: str = "", fraction: Optional[float] = None) -> None:
    logger.info("%s", progress_message(phase, step, fraction))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    os.replace(partial, path)


def _plain(value: Any) -> Any:
    """*value* with its paths as strings, for a JSON file."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _real_endpoints(
    endpoints: Sequence[Endpoint], members: "_Members", launch_files: Collection[str]
) -> list[RealEndpoint]:
    real = []
    for ep in endpoints:
        member = members.name(ep.link.pid)
        real.append(
            RealEndpoint(
                kind=ep.kind,
                node=ep.node,
                topic=ep.topic,
                period_ns=ep.period_ns,
                process_nodes=tuple(ep.link.nodes),
                launch_file=member if member in launch_files else "",
            )
        )
    return real


class _Members:
    """Rank of a traced process in the tie order: the index of the member whose process group holds it."""

    def __init__(self) -> None:
        self._names: list[str] = []
        self._by_pgid: dict[int, str] = {}
        self._rank: dict[int, int] = {}

    def set_names(self, names: Sequence[str]) -> None:
        self._names = list(names)

    def on_state(self, event: object) -> None:
        if isinstance(event, ev.Started):
            self._by_pgid[event.pid] = event.name

    def name(self, pid: int) -> str:
        """The member whose process group holds *pid*, empty once the process is gone."""
        try:
            return self._by_pgid.get(os.getpgid(pid), "")
        except OSError:
            return ""

    def rank(self, pid: int) -> int:
        if pid not in self._rank:
            name = self.name(pid)
            self._rank[pid] = self._names.index(name) if name in self._names else len(self._names)
        return self._rank[pid]


def _compare(
    out_dir: Path,
    reference_dir: Path,
    types: Mapping[str, str],
    config: ReplayConfig,
    since_ns: int,
) -> Verdict:
    recorded = group_by_topic(read_messages(out_dir / ref.OUTPUTS_DIR), since_ns=since_ns)
    expected = group_by_topic(read_messages(reference_dir / ref.OUTPUTS_DIR), since_ns=since_ns)
    return compare_outputs(expected, recorded, types, config, ros_deserialize)


def _save_reference(out_dir: Path, reference_dir: Path, inputs: Mapping[str, Any]) -> None:
    if reference_dir.exists():
        shutil.rmtree(reference_dir)
    reference_dir.mkdir(parents=True)
    shutil.copytree(out_dir / ref.OUTPUTS_DIR, reference_dir / ref.OUTPUTS_DIR)
    _write_json(reference_dir / ref.REFERENCE_FILE, {"saved": time.time(), "key": inputs})


def replay_from_json(json_path: str, options: ReplayOptions) -> int:
    from .system_runner import _bind_deploy_arguments

    _progress(PHASE_PRE, "selecting the scope")
    try:
        config = ReplayConfig.load(options.config)
        with open(json_path) as handle:
            payload = json.load(handle)
        payload = resolve_structure_paths(payload, json_path)
        payload = _bind_deploy_arguments(payload, json_path, deploy=options.deploy, overrides=options.arguments)
        patterns = list(options.scope) + [p for p in config.scope if p not in options.scope]
        scope = select_scope(payload["data"], patterns, ecu=options.ecu)
        boundary = compute_boundary(payload, scope)
    except (ConfigError, ScopeError, RuntimeError, OSError, ValueError) as exc:
        logger.error("replay: %s", exc)
        return EXIT_FAILED

    structure = replay_structure(payload["data"], scope)
    full_graph = NodeGraph.from_system_structure(payload)
    mode = full_graph.mode or Path(json_path).stem
    fqns = scope_node_fqns(payload, scope)
    launch_files = scope_launch_files(payload, scope)
    unconnected = unconnected_ports(payload, scope)
    node_order = {fqns[path]: index for index, path in enumerate(scope.nodes) if path in fqns}
    recorded_types = boundary.boundary_outputs()
    for topic in config.outputs:
        if topic not in boundary.output_types():
            logger.error("replay: output %s of the configuration is no output of the scope", topic)
            return EXIT_FAILED
        recorded_types[topic] = boundary.output_types()[topic]
    for topic in config.skip_outputs:
        if topic not in boundary.output_types():
            logger.error("replay: skipped output %s of the configuration is no output of the scope", topic)
            return EXIT_FAILED
        recorded_types.pop(topic, None)

    out_root = options.output_dir or ensure_output_dir(LOG_BASE / REPLAY_LOGS)
    out_dir = out_root / REPLAY_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("logs: %s", out_root)
    _write_json(
        out_dir / OPTIONS_FILE,
        {
            "system_structure": str(Path(json_path).resolve()),
            "options": _plain(asdict(options)),
            "config": asdict(config),
        },
    )
    _log_boundary(scope, boundary)

    problems: list[Problem] = []
    bag: Optional[BagInfo] = None
    fed: dict[str, str] = {}
    if not options.check_endpoints:
        if options.bag is None:
            logger.error("replay: no bag given")
            return EXIT_FAILED
        _progress(PHASE_PRE, "checking the bag")
        try:
            bag = remap_topics(read_bag_info(options.bag), {**config.remap, **options.remap})
        except BagError as exc:
            logger.error("replay: %s", exc)
            return EXIT_FAILED
        problems = check_bag(boundary, bag, extra_inputs=config.inputs, accept=config.accept)
        fed = fed_topics(boundary, bag, config.inputs)
        for topic in fed:
            if bag.topics[topic].recorded_name:
                logger.info("  remap  %s -> %s", bag.topics[topic].recorded_name, topic)
        from ._impl.replay.ros_io import unknown_types

        for topic, reason in unknown_types({**fed, **recorded_types}).items():
            problems.append(Problem(ERROR, "type_unknown", f"{topic}: message type not in the workspace ({reason})"))
    _write_json(
        out_dir / BOUNDARY_FILE,
        {
            "scope": {"patterns": scope.patterns, "nodes": scope.nodes, "containers": scope.containers},
            **boundary.as_dict(),
            "fed": fed,
            "remap": {topic: bag.topics[topic].source for topic in fed if bag.topics[topic].recorded_name},
            "recorded": recorded_types,
            "problems": [p.as_dict() for p in problems],
        },
    )
    _log_problems(problems)
    if has_errors(problems):
        logger.error("replay: pre-flight failed, see %s", out_dir / BOUNDARY_FILE)
        return EXIT_FAILED
    if options.check_only:
        logger.info("replay: pre-flight passed")
        _progress(PHASE_PRE, "pre-flight passed", 1.0)
        return EXIT_OK

    key_inputs: dict[str, Any] = {}
    reference_dir = options.reference
    if bag is not None:
        key_inputs = ref.key_inputs(
            structure,
            scope=scope.patterns,
            bag_fingerprint=bag.fingerprint,
            extra={
                "inputs": {topic: bag.topics[topic].source for topic in sorted(fed)},
                "outputs": sorted(recorded_types),
                "duration": options.duration,
                "seed": options.seed,
                "free_wall_timers": options.free_wall_timers,
            },
        )
        if reference_dir is None:
            reference_dir = ref.reference_dir(Path(json_path), mode, ref.reference_key(key_inputs))

    try:
        arming = TracerArming(locate_tracer(), out_root / TRACE_SUBDIR)
    except FileNotFoundError as exc:
        logger.error("replay: the tracer gates the scope and is not installed: %s", exc)
        return EXIT_FAILED

    middleware = replay_middleware()
    if options.isolate:
        os.environ.update(middleware.isolation_environment)
    if options.domain_id is not None and middleware.domain_id_variable:
        os.environ[middleware.domain_id_variable] = str(options.domain_id)

    start_ns = bag.start_ns if bag is not None else time.time_ns()
    end_ns = start_ns + int(options.duration * 1e9) if (bag is not None and options.duration is not None) else None
    # Driver time the bag is stepped through, for the progress of the replay phase.
    span_ns = 0
    if bag is not None:
        span_ns = (bag.end_ns if end_ns is None else min(end_ns, bag.end_ns)) - start_ns

    stepping: list[bool] = []

    def on_progress(now_ns: int) -> None:
        if not stepping:
            stepping.append(True)
            _progress(PHASE_PRE, "the scope is up", 1.0)
        done = max(now_ns - start_ns, 0)
        _progress(
            PHASE_REPLAY,
            f"{done / 1e9:.1f} of {span_ns / 1e9:.1f} s",
            done / span_ns if span_ns > 0 else 1.0,
        )

    # A Unix socket path holds about a hundred characters, fewer than a log directory may have.
    socket_dir = Path(tempfile.mkdtemp(prefix="asd_replay_"))
    socket_path = socket_dir / SOCKET_FILE
    outcome: dict[str, Any] = {"error": None, "endpoint_problems": []}
    result_holder: list[ReplayResult] = []
    actor_config = ActorConfig(
        respawn_enabled=False, output_dir=out_root, graceful_shutdown_timeout=options.graceful_shutdown_timeout
    )

    async def _run() -> int:
        from ._impl.replay.ros_io import RosReplayIO
        from ._impl.ros2.builder import populate_builder

        builder, worker = populate_builder(structure, ecu=None, config=actor_config)
        arming.install(builder)
        members = _Members()
        builder.add_state_hook(members.on_state)
        # rosbag2 writes into a directory it creates.
        shutil.rmtree(out_dir / ref.OUTPUTS_DIR, ignore_errors=True)
        sink = BagSink(out_dir / ref.OUTPUTS_DIR, recorded_types, bag.storage_id) if bag is not None else None
        recorder = OutputRecorder(recorded_types if bag is not None else {}, sink)

        def on_scope_up(endpoints: list[Endpoint]) -> None:
            found = check_endpoints(
                full_graph,
                fqns,
                _real_endpoints(endpoints, members, launch_files),
                accept=config.accept,
                # Without a bag, the ambient inputs a run would feed stand in for the fed topics.
                fed=[*fed, *config.inputs, *(special_topics().ambient_inputs if bag is None else ())],
                unconnected=unconnected,
            )
            if options.allow_undeclared:
                found = [Problem("warning", p.code, p.message) if p.level == ERROR else p for p in found]
            free = [ep for ep in endpoints if ep.kind == TIMER and ep.clock == CLOCK_FREE]
            for ep in free:
                logger.warning("replay: %s runs on a wall clock, outside the driver's time", ep.label())
            outcome["endpoint_problems"] = [p.as_dict() for p in found]
            _log_problems(found)
            if has_errors(found):
                raise ReplayError("the scope's endpoints do not match the design; --allow-undeclared runs anyway")

        io = RosReplayIO(worker, fed, bag, recorder, on_scope_up=on_scope_up) if bag is not None else None

        class _NoIO:
            fed_topics: dict[str, str] = {}

            async def prepare(self, driver: ReplayDriver) -> None:
                on_scope_up(driver.endpoints())

            async def publish(self, topic: str, data: bytes) -> None:
                return None

            async def subscription_count(self, topic: str) -> int:
                return 0

        class _NoSource:
            def peek(self):
                return None

            def pop(self):
                raise StopIteration

        source = BagMessages(bag, sorted(fed)) if bag is not None else _NoSource()
        if bag is not None and config.lead_in > 0:
            latched = {t for t in fed if bag.topics[t].transient_local}
            source = LeadIn(source, start_ns + int(config.lead_in * 1e9), special_topics().ambient_inputs, latched)
        steps = open(out_dir / STEP_LOG_FILE, "w") if options.step_log else None
        started = time.monotonic()

        def log_step(line: str) -> None:
            steps.write(f"{time.monotonic() - started:10.6f} {line}\n")

        server = LinkServer(socket_path, lambda link, kind, fields: driver.handle(link, kind, fields))
        driver = ReplayDriver(
            server,
            source,
            io if io is not None else _NoIO(),
            recorder,
            DriverOptions(
                start_ns=start_ns,
                end_ns=end_ns,
                seed=options.seed,
                startup_timeout=options.startup_timeout,
                startup_settle=options.startup_settle,
                callback_timeout=options.callback_timeout,
                startup_only=options.check_endpoints,
                step_log=log_step if steps is not None else None,
                progress=on_progress if bag is not None else None,
            ),
            node_order=node_order,
            expected_nodes=frozenset(fqns.values()),
            process_rank=members.rank,
        )

        async def pre_start(coord) -> None:
            members.set_names(coord.names())
            for spec in coord.specs():
                env = dict(spec.env if spec.env is not None else os.environ)
                env[SOCKET_ENV] = str(socket_path)
                env[START_ENV] = str(start_ns)
                if options.free_wall_timers:
                    env[WALL_TIMERS_ENV] = "free"
                spec.env = env
                cmd = list(spec.cmd)
                if "--ros-args" in cmd:
                    spec.cmd = cmd + middleware.parameter_arguments(cmd)

        async def drive(coord) -> None:
            try:
                await coord.launch_ready.wait()
                _progress(PHASE_PRE, "waiting for the scope to come up")
                result_holder.append(await driver.run())
                if bag is not None:
                    _progress(PHASE_REPLAY, "the bag is stepped through", 1.0)
            except ReplayError as exc:
                outcome["error"] = str(exc)
                result_holder.append(driver.result)
                logger.error("replay: %s", exc)
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = f"{type(exc).__name__}: {exc}"
                result_holder.append(driver.result)
                logger.exception("replay driver failed")
            finally:
                _progress(PHASE_POST, "shutting down the scope")
                coord.request_shutdown()

        async def post_start(coord) -> None:
            coord.schedule_task(drive(coord))

        builder.add_pre_start_hook(pre_start)
        builder.add_post_start_hook(post_start)
        coord = builder.build()
        try:
            write_run_manifest(
                out_root,
                json_path=json_path,
                data=payload,
                ecu=options.ecu,
                deploy=options.deploy,
                members=coord.names(),
            )
        except OSError as exc:
            logger.warning("cannot write the run manifest: %s", exc)
        # The link is up before the first member starts: a process that finds no driver runs ungated.
        await server.start()
        try:
            await worker.start()
            return await coord.run()
        finally:
            await server.close()
            if steps is not None:
                steps.close()
            if sink is not None:
                sink.close()
            await worker.stop()

    _progress(PHASE_PRE, "launching the scope")
    try:
        run_code = asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("replay interrupted")
        return 130
    finally:
        shutil.rmtree(socket_dir, ignore_errors=True)

    result = result_holder[0] if result_holder else ReplayResult(start_ns=start_ns)
    error = outcome["error"]
    if error is None and not result_holder:
        error = "the scope stopped before the replay started"
    elif error is None and run_code != 0:
        error = "a member of the scope failed"
    summary: dict[str, Any] = {
        "format": RESULT_FORMAT,
        "system_structure": str(Path(json_path).resolve()),
        "mode": mode,
        "scope": scope.patterns,
        "bag": str(bag.path) if bag is not None else None,
        "completed": error is None,
        "error": error,
        "endpoint_problems": outcome["endpoint_problems"],
        **result.as_dict(),
    }

    code = EXIT_OK if error is None else EXIT_FAILED
    if error is None and bag is not None:
        logger.info(
            "replay: %.3f s of ROS time in %.1f s: %d messages in, %d callbacks, %d timer fires, %d outputs",
            (result.end_ns - result.start_ns) / 1e9,
            result.wall_s,
            result.messages,
            result.callbacks,
            result.timer_fires,
            result.outputs,
        )
        for violation in result.violations:
            logger.warning(
                "replay: %s of %s on %s outside the granted callback: %d",
                _VIOLATION_NOUNS.get(violation["kind"], violation["kind"]),
                violation["node"],
                violation["endpoint"],
                violation["count"],
            )
        _progress(PHASE_POST, "saving the reference" if options.save_reference else "comparing the outputs")
        since_ns = start_ns + int(options.warmup * 1e9)
        summary["outputs_digest"] = digest(
            read_messages(out_dir / ref.OUTPUTS_DIR), recorded_types, ros_deserialize, since_ns=since_ns
        )
        if options.strict and result.violations:
            code = EXIT_DIFFERS
        if options.save_reference:
            if reference_dir is None:
                logger.error("replay: no export to key the reference by; give --reference DIR")
                code = EXIT_FAILED
            else:
                _save_reference(out_dir, reference_dir, key_inputs)
                summary["reference"] = {"path": str(reference_dir), "saved": True}
                logger.info("replay: reference saved to %s", reference_dir)
        elif reference_dir is not None and (reference_dir / ref.OUTPUTS_DIR).is_dir():
            verdict = _compare(out_dir, reference_dir, recorded_types, config, since_ns)
            _write_json(out_dir / VERDICT_FILE, {**verdict.as_dict(), "reference": str(reference_dir)})
            summary["reference"] = {"path": str(reference_dir), "passed": verdict.passed}
            for topic in verdict.topics:
                if topic.difference is not None:
                    logger.error("replay: %s differs: %s", topic.topic, topic.difference.get("detail"))
            if verdict.passed:
                logger.info("replay: outputs match the reference %s", reference_dir)
            else:
                logger.error("replay: outputs differ from the reference, see %s", out_dir / VERDICT_FILE)
                code = EXIT_DIFFERS
        elif options.reference is not None:
            logger.error("replay: reference %s holds no outputs", options.reference)
            code = EXIT_FAILED
        else:
            summary["reference"] = None
            logger.info("replay: no reference for this scope, bag and parameters; --save-reference stores this run")
    _write_json(out_dir / RESULT_FILE, summary)
    logger.info("replay: result in %s", out_dir / RESULT_FILE)
    _progress(PHASE_POST, "result written", 1.0)
    return code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay a scope of a system from a rosbag, one callback at a time in a fixed order, "
        "and compare the recorded outputs with a reference."
    )
    parser.add_argument("json_file", help="Path to system_structure JSON (e.g. .../system_structure/default.json)")
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="PATH",
        help="Node or module path of the design to replay; every node at or under it is selected, with the "
        "containers of the selected composable nodes. Repeatable.",
    )
    parser.add_argument("--bag", type=Path, default=None, help="rosbag2 directory or file holding the boundary inputs.")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="FILE",
        help="Replay configuration (YAML): further scope paths, bag topics fed beside the boundary inputs, "
        "accepted unfed inputs and services, further recorded outputs, comparison rules per topic.",
    )
    parser.add_argument(
        "--remap",
        action="append",
        default=None,
        metavar="RECORDED:=TOPIC",
        help="Feed the bag's topic RECORDED as TOPIC, over the configuration's remap. Repeatable.",
    )
    parser.add_argument("--compute-unit", default=None, help="Limit the scope to the nodes of this compute unit.")
    parser.add_argument("--deploy", default=None, help="Deploy variant whose arguments bind the system arguments.")
    parser.add_argument(
        "--arg", action="append", metavar="NAME=VALUE", default=None, help="Bind one system argument. Repeatable."
    )
    parser.add_argument(
        "--log-dir", type=Path, default=None, help="Run directory; the replay files go to <dir>/replay."
    )
    checks = parser.add_argument_group("checks")
    checks.add_argument("--check", action="store_true", help="Report the boundary, check the bag against it and stop.")
    checks.add_argument(
        "--check-endpoints",
        action="store_true",
        help="Launch the scope, compare its real subscriptions, publishers and timers with the design and stop; "
        "needs no bag.",
    )
    checks.add_argument(
        "--allow-undeclared",
        action="store_true",
        help="Run although a member subscribes to a topic its design does not declare.",
    )
    run = parser.add_argument_group("run")
    run.add_argument("--duration", type=float, default=None, metavar="S", help="Replay the first S seconds of the bag.")
    run.add_argument(
        "--tie-break-seed",
        type=int,
        default=None,
        metavar="N",
        help="Order callbacks that are ready together by a draw seeded with N instead of design order.",
    )
    run.add_argument(
        "--free-wall-timers",
        action="store_true",
        help="Leave timers created on a wall clock on that clock instead of the driver's time.",
    )
    run.add_argument(
        "--step-log",
        action="store_true",
        help="Write every step of the driver and every event of the gated processes to replay/steps.log.",
    )
    run.add_argument("--no-isolate", action="store_true", help="Do not restrict discovery to this host.")
    run.add_argument("--domain-id", type=int, default=None, help="Middleware domain of the replay.")
    run.add_argument("--startup-timeout", type=float, default=60.0, metavar="S")
    run.add_argument(
        "--startup-settle",
        type=float,
        default=2.0,
        metavar="S",
        help="The scope is up once no endpoint was created for S seconds (default: 2).",
    )
    run.add_argument(
        "--callback-timeout",
        type=float,
        default=30.0,
        metavar="S",
        help="Fail when a granted callback does not return within S seconds (default: 30).",
    )
    verdict = parser.add_argument_group("verdict")
    verdict.add_argument(
        "--reference",
        type=Path,
        default=None,
        metavar="DIR",
        help="Reference directory. Default: the export's reference keyed by scope, bag, arguments and parameters, "
        "under the record root.",
    )
    verdict.add_argument("--save-reference", action="store_true", help="Store this run's outputs as the reference.")
    verdict.add_argument(
        "--warmup", type=float, default=0.0, metavar="S", help="Leave the first S seconds out of the comparison."
    )
    verdict.add_argument(
        "--strict", action="store_true", help="Exit 1 when a publish, take or timer fire left the granted order."
    )
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def _parse_remaps(items: Optional[Sequence[str]]) -> dict[str, str]:
    remap: dict[str, str] = {}
    for item in items or []:
        source, sep, target = item.partition(":=")
        if not sep or not source or not target:
            raise ValueError(f"--remap {item!r}: expected RECORDED:=TOPIC")
        remap[source] = target
    return remap


def main() -> None:
    from .system_runner import _parse_arg_overrides, _setup_logging

    parser = _build_parser()
    args = parser.parse_args()
    if args.bag is None and not args.check_endpoints:
        parser.error("--bag is required unless --check-endpoints is given")
    try:
        remap = _parse_remaps(args.remap)
    except ValueError as exc:
        parser.error(str(exc))
    _setup_logging(args.log_level)
    sys.exit(
        replay_from_json(
            args.json_file,
            ReplayOptions(
                scope=args.scope,
                bag=args.bag,
                config=args.config,
                remap=remap,
                ecu=args.compute_unit,
                deploy=args.deploy,
                arguments=_parse_arg_overrides(args.arg),
                output_dir=args.log_dir,
                reference=args.reference,
                save_reference=args.save_reference,
                check_only=args.check,
                check_endpoints=args.check_endpoints,
                allow_undeclared=args.allow_undeclared,
                duration=args.duration,
                warmup=args.warmup,
                seed=args.tie_break_seed,
                strict=args.strict,
                isolate=not args.no_isolate,
                domain_id=args.domain_id,
                free_wall_timers=args.free_wall_timers,
                step_log=args.step_log,
                startup_timeout=args.startup_timeout,
                startup_settle=args.startup_settle,
                callback_timeout=args.callback_timeout,
            ),
        )
    )


if __name__ == "__main__":
    main()
