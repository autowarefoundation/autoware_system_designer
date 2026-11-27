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
import contextlib
import json
import logging
import os
import shutil
import struct
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Collection, Mapping, Optional, Sequence

from ._impl.core.config import ActorConfig
from ._impl.core.coordinator import ensure_output_dir
from ._impl.measure.node_graph import NodeGraph
from ._impl.measure.session import TRACE_SUBDIR
from ._impl.measure.tracer import TracerArming, locate_tracer
from ._impl.replay import reference as ref
from ._impl.replay.bag import (
    BagClock,
    BagError,
    BagInfo,
    BagMessages,
    BagSink,
    ClockStamped,
    LeadIn,
    on_clock,
    read_bag_clock,
    read_bag_info,
    read_messages,
    remap_topics,
)
from ._impl.replay.config import SECONDS, ConfigError, ReplayConfig, driver_settings, parse_driver
from ._impl.replay.driver import DRIVER_OPTION_NAMES, DriverOptions, ReplayDriver, ReplayError, ReplayResult
from ._impl.replay.link import (
    CLOCK_FREE,
    GATE_HOLD_ENV,
    GRANTS_ENV,
    MEMBER_ENV,
    SOCKET_ENV,
    START_ENV,
    TAKE_TIMEOUT_ENV,
    TIMER,
    WALL_TIMERS_ENV,
    Endpoint,
    LinkServer,
)
from ._impl.replay.middleware import ReplayMiddleware, replay_middleware
from ._impl.replay.preflight import (
    ERROR,
    WARNING,
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
    add_external_containers,
    compute_boundary,
    replay_structure,
    scope_launch_files,
    scope_node_fqns,
    select_scope,
    unconnected_ports,
)
from ._impl.replay.verdict import Verdict, compare_outputs, digest, group_by_topic, ros_deserialize
from ._impl.ros2.common.substitutions import bind_variables
from .export_tree import read_manifest, resolve_structure_paths
from .replay_record import (
    BOUNDARY_FILE,
    EXIT_DIFFERS,
    EXIT_FAILED,
    EXIT_INTERRUPTED,
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
from .run_log import LOG_BASE, REPLAY_LOGS, member_dir_name, tail_lines, write_run_manifest
from .special_topics import special_topics
from .system_runner import (
    bind_deploy_arguments,
    deploy_arguments,
    parse_arg_overrides,
    setup_logging,
    subsystem_record,
)

logger = logging.getLogger("autoware_system_designer")

SOCKET_FILE = "driver.sock"
RESULT_FORMAT = "autoware_system_designer/replay_result/1"
# Stream of a member a failed run quotes, and the lines it quotes at most.
MEMBER_LOG_STREAM = "err"
MEMBER_LOG_LINES = 20

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
    # Seconds from the bag's start the replay begins at.
    start: float = 0.0
    duration: Optional[float] = None
    warmup: float = 0.0
    seed: Optional[int] = None
    strict: bool = False
    isolate: bool = True
    domain_id: Optional[int] = None
    free_wall_timers: bool = False
    # Write every step and every line of the gated processes to replay/steps.log.
    step_log: bool = False
    # Driver settings by name, over the configuration's.
    driver: Mapping[str, float] = field(default_factory=dict)


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


def _real_endpoints(endpoints: Sequence[Endpoint], launch_files: Collection[str]) -> list[RealEndpoint]:
    return [
        RealEndpoint(
            kind=ep.kind,
            node=ep.node,
            topic=ep.topic,
            period_ns=ep.period_ns,
            process_nodes=tuple(ep.link.nodes),
            launch_file=ep.link.member if ep.link.member in launch_files else "",
        )
        for ep in endpoints
    ]


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


# Entries a reference directory is made of; a partly written one holds no others.
_REFERENCE_ENTRIES = frozenset({ref.OUTPUTS_DIR, ref.REFERENCE_FILE})


class ReferenceSaveError(Exception):
    pass


def _replaceable(path: Path) -> bool:
    """Whether *path* is absent, a saved reference, or a directory holding nothing but a reference's
    entries."""
    if not path.exists() and not path.is_symlink():
        return True
    if path.is_symlink() or not path.is_dir():
        return False
    if (path / ref.REFERENCE_FILE).is_file():
        return True
    return all(entry.name in _REFERENCE_ENTRIES for entry in path.iterdir())


def _save_reference(out_dir: Path, reference_dir: Path, inputs: Mapping[str, Any]) -> None:
    if not reference_dir.name or not _replaceable(reference_dir):
        raise ReferenceSaveError(f"{reference_dir} holds files other than a reference; not replaced")
    # Built beside its place and renamed into it: a reference is whole or absent.
    staging = reference_dir.with_name(reference_dir.name + ".new")
    retired = reference_dir.with_name(reference_dir.name + ".old")
    for path in (staging, retired):
        if not _replaceable(path):
            raise ReferenceSaveError(f"{path} holds files other than a reference; not replaced")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    shutil.copytree(out_dir / ref.OUTPUTS_DIR, staging / ref.OUTPUTS_DIR)
    _write_json(staging / ref.REFERENCE_FILE, {"saved": time.time(), "key": inputs})
    shutil.rmtree(retired, ignore_errors=True)
    if reference_dir.exists():
        os.replace(reference_dir, retired)
    os.replace(staging, reference_dir)
    shutil.rmtree(retired, ignore_errors=True)


class _Stop(Exception):
    """The replay ends before its scope is launched, with this exit code."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _fail(message: str, *args: Any) -> _Stop:
    logger.error("replay: " + message, *args)
    return _Stop(EXIT_FAILED)


@dataclass
class _Plan:
    """What the launch of a scope and its verdict read: the scope, its boundary, the bag and the run's files."""

    json_path: str
    payload: Mapping[str, Any]
    config: ReplayConfig
    scope: Scope
    structure: Mapping[str, Any]
    full_graph: NodeGraph
    mode: str
    # Design path of each node of the scope to its fully qualified name.
    fqns: Mapping[str, str]
    launch_files: Collection[str]
    unconnected: Any
    recorded_types: dict[str, str]
    out_root: Path
    out_dir: Path
    # Span in the time of the bag clock, when the bag has one.
    bag: Optional[BagInfo]
    # Clock the bag recorded; None when it recorded none and its messages keep their recorded time.
    bag_clock: Optional[BagClock]
    fed: dict[str, str]
    key_inputs: dict[str, Any]
    reference_dir: Optional[Path]
    start_ns: int
    end_ns: Optional[int]

    @property
    def node_order(self) -> dict[str, int]:
        return {self.fqns[path]: index for index, path in enumerate(self.scope.nodes) if path in self.fqns}


@dataclass
class _Outcome:
    result: ReplayResult
    # The driver ran, to its end or to an error.
    driven: bool = False
    error: Optional[str] = None
    # Member the error names, None when it names none.
    error_member: Optional[str] = None
    # A signal stopped the run.
    interrupted: bool = False
    # Members launched, by path.
    members: list[str] = field(default_factory=list)
    endpoint_problems: list[dict[str, Any]] = field(default_factory=list)


def _recorded_types(config: ReplayConfig, boundary: Boundary) -> dict[str, str]:
    """Topic and type of every recorded output: the boundary's, with the configuration's added and skipped."""
    recorded = boundary.boundary_outputs()
    outputs = boundary.output_types()
    for topic in config.outputs:
        if topic not in outputs:
            raise _fail("output %s of the configuration is no output of the scope", topic)
        recorded[topic] = outputs[topic]
    for topic in config.skip_outputs:
        if topic not in outputs:
            raise _fail("skipped output %s of the configuration is no output of the scope", topic)
        recorded.pop(topic, None)
    return recorded


def _check_bag(
    options: ReplayOptions, config: ReplayConfig, boundary: Boundary, recorded_types: Mapping[str, str]
) -> tuple[BagInfo, dict[str, str], list[Problem]]:
    """The bag with its topics as they are fed, the fed topics and the pre-flight problems."""
    from ._impl.replay.ros_io import unknown_types

    if options.bag is None:
        raise _fail("no bag given")
    _progress(PHASE_PRE, "checking the bag")
    try:
        bag = remap_topics(read_bag_info(options.bag), {**config.remap, **options.remap})
    except BagError as exc:
        raise _fail("%s", exc) from exc
    problems = check_bag(boundary, bag, extra_inputs=config.inputs, accept=config.accept)
    fed = fed_topics(boundary, bag, config.inputs)
    for topic in fed:
        if bag.topics[topic].recorded_name:
            logger.info("  remap  %s -> %s", bag.topics[topic].recorded_name, topic)
    for topic, reason in unknown_types({**fed, **recorded_types}).items():
        problems.append(Problem(ERROR, "type_unknown", f"{topic}: message type not in the workspace ({reason})"))
    return bag, fed, problems


def _clock_topic(bag: BagInfo) -> Optional[str]:
    """The clock topic a bag recorded messages of, None when it recorded none."""
    recorded = (name for name in sorted(special_topics().clock) if name in bag.topics and bag.topics[name].count)
    return next(recorded, None)


def _bag_clock(bag: BagInfo, topic: str) -> BagClock:
    """The clock a bag recorded on *topic*."""
    _progress(PHASE_PRE, "reading the bag clock")
    try:
        clock = read_bag_clock(bag, topic)
    except (BagError, struct.error, IndexError) as exc:
        raise _fail("clock of the bag on %s: %s", topic, exc) from exc
    logger.info(
        "replay: time follows %s of the bag, %+.3f s from its recorded time at the start",
        topic,
        (clock.at(bag.start_ns) - bag.start_ns) / 1e9,
    )
    return clock


def _external_containers(json_path: str, options: ReplayOptions) -> list[dict[str, Any]]:
    """Containers the subsystem of *json_path* expects from its launching side, their names bound."""
    record = subsystem_record(read_manifest(Path(json_path)) or {})
    arguments = deploy_arguments(json_path, deploy=options.deploy, overrides=options.arguments)
    return bind_variables([c for c in record.get("external_containers", []) if isinstance(c, dict)], arguments)


def _prepare(json_path: str, options: ReplayOptions) -> _Plan:
    """Selects the scope, checks the bag against its boundary and writes the boundary report."""
    _progress(PHASE_PRE, "selecting the scope")
    try:
        config = ReplayConfig.load(options.config)
        config.driver.update(options.driver)
        with open(json_path) as handle:
            payload = json.load(handle)
        payload = resolve_structure_paths(payload, json_path)
        payload = bind_deploy_arguments(payload, json_path, deploy=options.deploy, overrides=options.arguments)
        add_external_containers(payload["data"], _external_containers(json_path, options))
        patterns = list(options.scope) + [p for p in config.scope if p not in options.scope]
        scope = select_scope(payload["data"], patterns, ecu=options.ecu)
        boundary = compute_boundary(payload, scope)
    except (ConfigError, ScopeError, RuntimeError, OSError, ValueError) as exc:
        raise _fail("%s", exc) from exc
    recorded_types = _recorded_types(config, boundary)
    full_graph = NodeGraph.from_system_structure(payload)

    out_root = options.output_dir or ensure_output_dir(LOG_BASE / REPLAY_LOGS)
    out_dir = out_root / REPLAY_SUBDIR
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info("logs: %s", out_root)
    _write_json(
        out_dir / OPTIONS_FILE,
        {
            "system_structure": str(Path(json_path).resolve()),
            "options": asdict(options),
            "config": asdict(config),
        },
    )
    _log_boundary(scope, boundary)

    problems: list[Problem] = []
    bag: Optional[BagInfo] = None
    fed: dict[str, str] = {}
    if not options.check_endpoints:
        bag, fed, problems = _check_bag(options, config, boundary, recorded_types)
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
        raise _fail("pre-flight failed, see %s", out_dir / BOUNDARY_FILE)
    if options.check_only:
        logger.info("replay: pre-flight passed")
        _progress(PHASE_PRE, "pre-flight passed", 1.0)
        raise _Stop(EXIT_OK)

    structure = replay_structure(payload["data"], scope)
    mode = full_graph.mode or Path(json_path).stem
    key_inputs: dict[str, Any] = {}
    # Absolute, so the directory has a name its siblings are made from.
    reference_dir = options.reference.expanduser().resolve() if options.reference is not None else None
    clock_topic = _clock_topic(bag) if bag is not None else None
    if bag is not None:
        key_inputs = ref.key_inputs(
            structure,
            scope=scope.patterns,
            bag_fingerprint=bag.fingerprint,
            extra={
                "inputs": {topic: bag.topics[topic].source for topic in sorted(fed)},
                "outputs": sorted(recorded_types),
                "duration": options.duration,
                **({"start": options.start} if options.start else {}),
                "lead_in": config.lead_in,
                "seed": options.seed,
                "free_wall_timers": options.free_wall_timers,
                **({"clock": bag.topics[clock_topic].source} if clock_topic else {}),
            },
        )
        if reference_dir is None:
            reference_dir = ref.reference_dir(Path(json_path), mode, ref.reference_key(key_inputs))
    bag_clock = _bag_clock(bag, clock_topic) if clock_topic else None
    if bag_clock is not None:
        bag = on_clock(bag, bag_clock)
    start_ns = bag.start_ns + int(options.start * 1e9) if bag is not None else time.time_ns()
    return _Plan(
        json_path=json_path,
        payload=payload,
        config=config,
        scope=scope,
        structure=structure,
        full_graph=full_graph,
        mode=mode,
        fqns=scope_node_fqns(payload, scope),
        launch_files=scope_launch_files(payload, scope),
        unconnected=unconnected_ports(payload, scope),
        recorded_types=recorded_types,
        out_root=out_root,
        out_dir=out_dir,
        bag=bag,
        bag_clock=bag_clock,
        fed=fed,
        key_inputs=key_inputs,
        reference_dir=reference_dir,
        start_ns=start_ns,
        end_ns=start_ns + int(options.duration * 1e9) if (bag is not None and options.duration is not None) else None,
    )


def _middleware_environment(options: ReplayOptions, middleware: ReplayMiddleware) -> dict[str, str]:
    """Variables that place the replay on the middleware: its discovery range and its domain."""
    environment = middleware.isolation(os.environ) if options.isolate else {}
    if options.domain_id is not None and middleware.domain_id_variable:
        environment[middleware.domain_id_variable] = str(options.domain_id)
    return environment


def _gate_environment(plan: _Plan, options: ReplayOptions, socket_path: Path) -> dict[str, str]:
    """Variables that link a member's processes to the driver and set their gate."""
    driver = plan.config.driver
    environment = {
        SOCKET_ENV: str(socket_path),
        START_ENV: str(plan.start_ns),
        GATE_HOLD_ENV: str(int(driver["gate_hold"] * 1e9)),
        TAKE_TIMEOUT_ENV: str(int(driver["take_timeout"] * 1e9)),
        GRANTS_ENV: str(driver["process_grants"]),
    }
    if options.free_wall_timers:
        environment[WALL_TIMERS_ENV] = "free"
    return environment


class _NoIO:
    """Driver endpoints of a run without a bag: nothing is fed or recorded."""

    fed_topics: dict[str, str] = {}

    def __init__(self, on_scope_up) -> None:
        self._on_scope_up = on_scope_up

    async def prepare(self, driver: ReplayDriver) -> None:
        self._on_scope_up(driver.endpoints())

    async def publish(self, topic: str, data: bytes) -> None:
        return None

    async def subscription_count(self, topic: str) -> int:
        return 0


class _NoSource:
    def peek(self):
        return None

    def pop(self):
        raise IndexError("no message")

    def close(self) -> None:
        return None


class _Progress:
    """Reports the driver time of a stepped bag as the progress of the replay phase."""

    def __init__(self, plan: _Plan) -> None:
        bag = plan.bag
        self._start_ns = plan.start_ns
        self._span_ns = 0
        if bag is not None:
            self._span_ns = (bag.end_ns if plan.end_ns is None else min(plan.end_ns, bag.end_ns)) - plan.start_ns
        self._stepping = False

    def __call__(self, now_ns: int) -> None:
        if not self._stepping:
            self._stepping = True
            _progress(PHASE_PRE, "the scope is up", 1.0)
        done = max(now_ns - self._start_ns, 0)
        _progress(
            PHASE_REPLAY,
            f"{done / 1e9:.1f} of {self._span_ns / 1e9:.1f} s",
            done / self._span_ns if self._span_ns > 0 else 1.0,
        )


def _source(plan: _Plan):
    """The bag messages a run feeds, in feeding order."""
    bag, fed = plan.bag, plan.fed
    if bag is None:
        return _NoSource()
    source = BagMessages(bag, sorted(fed))
    if plan.bag_clock is not None:
        source = ClockStamped(source, plan.bag_clock)
    latched = {t for t in fed if bag.topics[t].transient_local}
    if plan.start_ns > bag.start_ns:
        # Of the span before the replay's start, what a latched topic still holds at it.
        source = LeadIn(source, plan.start_ns, latched.intersection(special_topics().ambient_inputs), latched)
    if plan.config.lead_in > 0:
        lead_in_end = plan.start_ns + int(plan.config.lead_in * 1e9)
        return LeadIn(source, lead_in_end, special_topics().ambient_inputs, latched)
    return source


async def _run_scope(plan: _Plan, options: ReplayOptions, socket_path: Path, outcome: _Outcome) -> int:
    """Launches the scope's members and steps them through the bag; returns the coordinator's exit code."""
    from ._impl.replay.ros_io import RosReplayIO
    from ._impl.ros2.builder import populate_builder

    bag, config, out_dir = plan.bag, plan.config, plan.out_dir
    middleware = replay_middleware()
    actor_config = ActorConfig(respawn_enabled=False, output_dir=plan.out_root)
    builder, worker = populate_builder(plan.structure, ecu=None, config=actor_config)
    TracerArming(locate_tracer(), plan.out_root / TRACE_SUBDIR).install(builder)
    member_names: list[str] = []

    def member_rank(member: str) -> int:
        return member_names.index(member) if member in member_names else len(member_names)

    def on_scope_up(endpoints: list[Endpoint]) -> None:
        found = check_endpoints(
            plan.full_graph,
            plan.fqns,
            _real_endpoints(endpoints, plan.launch_files),
            accept=config.accept,
            # Without a bag, the ambient inputs a run would feed stand in for the fed topics.
            fed=[*plan.fed, *config.inputs, *(special_topics().ambient_inputs if bag is None else ())],
            unconnected=plan.unconnected,
        )
        if options.allow_undeclared:
            found = [Problem(WARNING, p.code, p.message) if p.level == ERROR else p for p in found]
        for ep in endpoints:
            if ep.kind == TIMER and ep.clock == CLOCK_FREE:
                logger.warning("replay: %s runs on a wall clock, outside the driver's time", ep.label())
        outcome.endpoint_problems = [p.as_dict() for p in found]
        _log_problems(found)
        if has_errors(found):
            raise ReplayError("the scope's endpoints do not match the design; --allow-undeclared runs anyway")

    # Bag reader, output writer and step log, closed however the run ends.
    with contextlib.ExitStack() as resources:
        # rosbag2 writes into a directory it creates.
        shutil.rmtree(out_dir / ref.OUTPUTS_DIR, ignore_errors=True)
        sink = BagSink(out_dir / ref.OUTPUTS_DIR, plan.recorded_types, bag.storage_id) if bag is not None else None
        if sink is not None:
            resources.callback(sink.close)
        recorder = OutputRecorder(plan.recorded_types if bag is not None else {}, sink)
        io = RosReplayIO(worker, plan.fed, bag, recorder, on_scope_up=on_scope_up) if bag is not None else None
        source = _source(plan)
        resources.callback(source.close)
        steps = resources.enter_context(open(out_dir / STEP_LOG_FILE, "w")) if options.step_log else None
        started = time.monotonic()

        def log_step(line: str) -> None:
            steps.write(f"{time.monotonic() - started:10.6f} {line}\n")

        server = LinkServer(socket_path, lambda link, kind, fields: driver.handle(link, kind, fields))
        driver = ReplayDriver(
            server,
            source,
            io if io is not None else _NoIO(on_scope_up),
            recorder,
            DriverOptions(
                start_ns=plan.start_ns,
                end_ns=plan.end_ns,
                seed=options.seed,
                startup_only=options.check_endpoints,
                step_log=log_step if steps is not None else None,
                progress=_Progress(plan) if bag is not None else None,
                **{name: config.driver[name] for name in DRIVER_OPTION_NAMES},
            ),
            node_order=plan.node_order,
            expected_nodes=frozenset(plan.fqns.values()),
            member_rank=member_rank,
        )
        gate = _gate_environment(plan, options, socket_path)

        async def pre_start(coord) -> None:
            member_names[:] = coord.names()
            for spec in coord.specs():
                base = spec.env if spec.env is not None else os.environ
                spec.env = {**base, **gate, MEMBER_ENV: spec.name}
                cmd = list(spec.cmd)
                if "--ros-args" in cmd:
                    spec.cmd = cmd + middleware.parameter_arguments(cmd)

        async def drive(coord) -> None:
            try:
                await coord.launch_ready.wait()
                _progress(PHASE_PRE, "waiting for the scope to come up")
                outcome.driven = True
                await driver.run()
                if bag is not None:
                    _progress(PHASE_REPLAY, "the bag is stepped through", 1.0)
            except ReplayError as exc:
                outcome.error = str(exc)
                outcome.error_member = exc.member
                logger.error("replay: %s", exc)
            except asyncio.CancelledError:
                outcome.error = outcome.error or "the driver was cancelled before the replay ended"
                raise
            except Exception as exc:  # noqa: BLE001
                outcome.error = f"{type(exc).__name__}: {exc}"
                logger.exception("replay driver failed")
            finally:
                outcome.result = driver.result
                _progress(PHASE_POST, "shutting down the scope")
                coord.request_shutdown()

        async def post_start(coord) -> None:
            coord.schedule_task(drive(coord))

        builder.add_pre_start_hook(pre_start)
        builder.add_post_start_hook(post_start)
        coord = builder.build()
        outcome.members = list(coord.names())
        try:
            write_run_manifest(
                plan.out_root,
                json_path=plan.json_path,
                data=plan.payload,
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
            try:
                return await coord.run()
            finally:
                outcome.interrupted = coord.stopped_by is not None
        finally:
            await server.close()
            await worker.stop()


def _run(plan: _Plan, options: ReplayOptions) -> _Outcome:
    """Runs the scope under the driver; the outcome holds what the driver counted and why it stopped."""
    try:
        locate_tracer()
    except FileNotFoundError as exc:
        raise _fail("the tracer gates the scope and is not installed: %s", exc) from exc
    # The driver's own endpoints read the process environment, the members inherit it.
    os.environ.update(_middleware_environment(options, replay_middleware()))
    # A Unix socket path holds about a hundred characters, fewer than a log directory may have.
    socket_dir = Path(tempfile.mkdtemp(prefix="asd_replay_"))
    outcome = _Outcome(result=ReplayResult(start_ns=plan.start_ns))
    _progress(PHASE_PRE, "launching the scope")
    try:
        run_code = asyncio.run(_run_scope(plan, options, socket_dir / SOCKET_FILE, outcome))
    finally:
        shutil.rmtree(socket_dir, ignore_errors=True)
    if outcome.interrupted:
        outcome.error = "interrupted by a signal"
    elif outcome.error is None and not outcome.driven:
        outcome.error = "the scope stopped before the replay started"
    elif outcome.error is None and run_code != 0:
        outcome.error = "a member of the scope failed"
    return outcome


def _judge(plan: _Plan, options: ReplayOptions, result: ReplayResult, summary: dict[str, Any]) -> int:
    """Digests the recorded outputs, then saves them as the reference or compares them with it."""
    out_dir, reference_dir = plan.out_dir, plan.reference_dir
    code = EXIT_DIFFERS if options.strict and result.violations else EXIT_OK
    _progress(PHASE_POST, "saving the reference" if options.save_reference else "comparing the outputs")
    since_ns = plan.start_ns + int(options.warmup * 1e9)
    summary["outputs_digest"] = digest(
        read_messages(out_dir / ref.OUTPUTS_DIR), plan.recorded_types, ros_deserialize, since_ns=since_ns
    )
    if options.save_reference:
        if reference_dir is None:
            logger.error("replay: no export to key the reference by; give --reference DIR")
            return EXIT_FAILED
        try:
            _save_reference(out_dir, reference_dir, plan.key_inputs)
        except ReferenceSaveError as exc:
            summary["reference"] = {"path": str(reference_dir), "saved": False, "error": str(exc)}
            logger.error("replay: %s", exc)
            return EXIT_FAILED
        summary["reference"] = {"path": str(reference_dir), "saved": True}
        logger.info("replay: reference saved to %s", reference_dir)
    elif reference_dir is not None and (reference_dir / ref.OUTPUTS_DIR).is_dir():
        verdict = _compare(out_dir, reference_dir, plan.recorded_types, plan.config, since_ns)
        _write_json(out_dir / VERDICT_FILE, {**verdict.as_dict(), "reference": str(reference_dir)})
        summary["reference"] = {"path": str(reference_dir), "passed": verdict.passed}
        for topic in verdict.topics:
            if topic.difference is not None:
                logger.error("replay: %s differs: %s", topic.topic, topic.difference.get("detail"))
        if verdict.passed:
            logger.info("replay: outputs match the reference %s", reference_dir)
        else:
            logger.error("replay: outputs differ from the reference, see %s", out_dir / VERDICT_FILE)
            return EXIT_DIFFERS
    elif options.reference is not None:
        logger.error("replay: reference %s holds no outputs", options.reference)
        return EXIT_FAILED
    else:
        summary["reference"] = None
        logger.info("replay: no reference for this scope, bag and parameters; --save-reference stores this run")
    return code


def _member_logs(plan: _Plan, outcome: _Outcome) -> list[dict[str, Any]]:
    """Last stderr lines of the member a failed run's error names, else of every member that wrote some."""
    members = [outcome.error_member] if outcome.error_member in outcome.members else outcome.members
    logs = []
    for member in members:
        path = plan.out_root / member_dir_name(member) / MEMBER_LOG_STREAM
        lines, _ = tail_lines(path)
        if lines:
            logs.append({"member": member, "path": str(path), "lines": lines[-MEMBER_LOG_LINES:]})
    return logs


def _conclude(plan: _Plan, options: ReplayOptions, outcome: _Outcome) -> int:
    """Logs the run's counts, judges its outputs and writes the result file; returns the exit code."""
    result, bag = outcome.result, plan.bag
    summary: dict[str, Any] = {
        "format": RESULT_FORMAT,
        "system_structure": str(Path(plan.json_path).resolve()),
        "mode": plan.mode,
        "scope": plan.scope.patterns,
        "bag": str(bag.path) if bag is not None else None,
        "completed": outcome.error is None,
        "error": outcome.error,
        "endpoint_problems": outcome.endpoint_problems,
        **result.as_dict(),
    }
    code = EXIT_OK if outcome.error is None else EXIT_INTERRUPTED if outcome.interrupted else EXIT_FAILED
    if outcome.error is not None and not outcome.interrupted:
        summary["member_logs"] = _member_logs(plan, outcome)
        for log in summary["member_logs"]:
            logger.error("replay: last stderr lines of %s (%s):", log["member"], log["path"])
            for line in log["lines"]:
                logger.error("replay:   %s", line)
    if outcome.error is None and bag is not None:
        logger.info(
            "replay: %.3f s of ROS time in %.1f s: %d messages in, %d callbacks, %d timer fires, %d outputs",
            (result.end_ns - result.start_ns) / 1e9,
            result.wall_s,
            result.messages,
            result.callbacks,
            result.timer_fires,
            result.outputs,
        )
        if result.lost_outputs:
            logger.warning("replay: %d publish(es) on recorded topics were never heard", result.lost_outputs)
        for violation in result.violations:
            logger.warning(
                "replay: %s of %s on %s outside the granted callback: %d",
                _VIOLATION_NOUNS.get(violation["kind"], violation["kind"]),
                violation["node"],
                violation["endpoint"],
                violation["count"],
            )
        code = _judge(plan, options, result, summary)
    _write_json(plan.out_dir / RESULT_FILE, summary)
    logger.info("replay: result in %s", plan.out_dir / RESULT_FILE)
    _progress(PHASE_POST, "result written", 1.0)
    return code


def replay_from_json(json_path: str, options: ReplayOptions) -> int:
    """Runs a replay; every way it ends maps to an exit code, and only a judged difference is EXIT_DIFFERS."""
    try:
        plan = _prepare(json_path, options)
        outcome = _run(plan, options)
        return _conclude(plan, options, outcome)
    except _Stop as stop:
        return stop.code
    except KeyboardInterrupt:
        logger.info("replay interrupted")
        return EXIT_INTERRUPTED
    except Exception:  # noqa: BLE001
        logger.exception("replay failed")
        return EXIT_FAILED


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
    run.add_argument(
        "--start",
        type=float,
        default=0.0,
        metavar="S",
        help="Begin S seconds after the bag's start; the last message of each latched topic before it is fed.",
    )
    run.add_argument("--duration", type=float, default=None, metavar="S", help="Replay S seconds from the start.")
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
    run.add_argument(
        "--driver",
        action="append",
        default=None,
        metavar="NAME=VALUE",
        help="Set one driver setting, over the configuration's. Repeatable. "
        + " ".join(
            f"{setting['name']} ({setting['default']:g}{' s' if setting['unit'] == SECONDS else ''}): "
            f"{setting['description']}"
            for setting in driver_settings()
        ),
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


def _parse_driver(items: Optional[Sequence[str]]) -> dict[str, float]:
    values: dict[str, Any] = {}
    for name, text in parse_arg_overrides(items).items():
        try:
            values[name] = int(text)
        except ValueError:
            values[name] = float(text)
    return parse_driver(values, "--driver")


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    if args.bag is None and not args.check_endpoints:
        parser.error("--bag is required unless --check-endpoints is given")
    if args.start < 0:
        parser.error("--start takes zero or more seconds")
    try:
        remap = _parse_remaps(args.remap)
        driver = _parse_driver(args.driver)
    except ValueError as exc:
        parser.error(str(exc))
    setup_logging(args.log_level)
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
                arguments=parse_arg_overrides(args.arg),
                output_dir=args.log_dir,
                reference=args.reference,
                save_reference=args.save_reference,
                check_only=args.check,
                check_endpoints=args.check_endpoints,
                allow_undeclared=args.allow_undeclared,
                start=args.start,
                duration=args.duration,
                warmup=args.warmup,
                seed=args.tie_break_seed,
                strict=args.strict,
                isolate=not args.no_isolate,
                domain_id=args.domain_id,
                free_wall_timers=args.free_wall_timers,
                step_log=args.step_log,
                driver=driver,
            ),
        )
    )


if __name__ == "__main__":
    main()
