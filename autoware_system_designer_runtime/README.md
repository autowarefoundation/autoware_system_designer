# `autoware_system_designer_runtime` — Actor-based system supervisor

A Python actor runtime that **runs** an Autoware system_structure JSON directly: each node (regular, container, composable, ros2_launch_file include) becomes a supervised subprocess with its own state machine. Inspired by [play_launch](https://github.com/tier4/play_launch), reimplemented in Python with no play_launch import and no external binary.

Each member is a first-class task: you can inspect, stop, and restart nodes individually without tearing down the whole system.

---

## Why

`launch.LaunchService` hides every spawned process behind a single event loop.
You cannot tell which node crashed, kill one without killing all, or restart a single component. This runtime mirrors play_launch's actor pattern so each member is a first-class task with:

- explicit state (`Pending → Running → Stopped | Respawning | Failed → Done`)
- per-member control queue (`Stop`, `Restart`, `Kill`, `ToggleRespawn`)
- per-member state-event stream (`Started`, `Exited`, `Failed`, …)
- a dedicated log directory (`out`, `err`, `pid`, `cmdline`)
- graceful shutdown: `SIGINT` to the process group, 5s grace, then `SIGKILL`

---

## Quickstart

### 1. Build & source the workspace

```bash
cd ~/workspace/awf/autoware
colcon build --packages-up-to autoware_system_designer autoware_system_designer_demo_systems autoware_sample_designs
source install/setup.bash
```

### 2. Launch a system_structure JSON

```bash
autoware-system-designer-launch \
    /path/to/system_structure/MySystem.json --deploy my_vehicle
```

System arguments (`$(var vehicle_id)` and friends) are bound at launch time from the
deploy variant named by `--deploy`, read from the `deployment.json` beside
`system_structure/`; `--arg NAME=VALUE` binds or overrides one argument directly.
A system that declares no arguments needs neither.

Each command of this package is installed twice: as a console script under
`lib/<package>/`, reached with `ros2 run autoware_system_designer_runtime <command>`, and under
`install/autoware_system_designer_runtime/bin/`, which `install/setup.bash` puts on `PATH`. The
`bin/` copy locates its own Python package, so it also runs by absolute path from a shell that has
not sourced the workspace (the launcher still needs ROS 2 sourced for `rclpy`).

`Ctrl+C` triggers graceful shutdown: the coordinator sets the shutdown event every
actor waits on, each actor `SIGINT`s its process group, waits 5s, then `SIGKILL`s.
rclcpp and rclpy leave the ROS graph on `SIGINT`; a glog failure handler takes `SIGTERM` as a crash.
A member is its whole process group: it has stopped once its leader has exited and
no process of the group is left, so the children of a launch file that ends before
them are waited for and killed the same way. A member whose leader exits by itself
takes what is left of its group with it.

### 3. Useful flags

```bash
autoware-system-designer-launch SYSTEM.json \
    --compute-unit main_ecu \           # only launch nodes with compute_unit=main_ecu
    --deploy my_vehicle \               # deploy variant whose arguments bind $(var ...)
    --arg map_path=/data/map \          # bind/override one system argument (repeatable)
    --log-dir /tmp/run1 \               # per-node logs land in /tmp/run1/<member>/
    --launch-id 20260521_153012-ab12 \  # launch this run belongs to, recorded in run.json
    --respawn \                         # restart any node that exits abnormally (non-zero)
    --respawn-delay 1.0 \
    --max-respawn-attempts 5 \
    --graceful-shutdown-timeout 10 \    # seconds SIGINT→SIGKILL grace
    --interactive \                     # enable stdin REPL (see below)
    --control /run/user/1000/asd.sock \ # control channel on a Unix socket (see below)
    --control-policy guard \            # guard (default) | open: interlock on the operation mode
    --no-tracer \                       # no rate counters and no measurement (see Live rates)
    --log-level DEBUG
```

### 4. Interactive control (`--interactive`)

With `--interactive`, the launcher reads commands from stdin while running:

```text
autoware runtime [? for help] > status
  /perception/object_recognition/detector_a1/node_filter  running
  /perception/object_recognition/node_tracker  running
  /pointcloud_container  running
autoware runtime [? for help] > stop /perception/object_recognition/detector_a1
[console] stop -> /perception/object_recognition/detector_a1/node_filter
autoware runtime [? for help] > restart /pointcloud_container
[console] restart -> /pointcloud_container
autoware runtime [? for help] > quit
```

Commands: `status`, `stop <name>`, `restart <name>`, `kill <name>`, `measure start|stop|status`
(see [Latency measurement](#latency-measurement)), `rates [name]` (see [Live rates](#live-rates)),
`quit`, `help` / `?`.

A member is named by its structural path in the design; a node without one is named
`<fully qualified name>#<launch state>`.

`<name>` matches the member named exactly so, else as a **substring of the member name**, so namespace prefixes are natural targets (`stop /perception` stops everything under that subtree).

### 5. Control channel (`--control SOCKET`)

`--control` serves the same command set on a Unix socket (mode `0600`, removed on exit), as JSON
lines, for a supervising process such as the studio daemon. The runtime sends events; a client sends
requests and gets one response each:

```text
→ {"event":"hello","pid":4242,"methods":[...],"policy":"guard","measure_armed":true,"monitor":true,
   "log_dir":"/tmp/autoware_system_designer_logs/20260521_153012",
   "members":[{"path":"/sensing/lidar/driver","state":"pending","detail":{},"time":0,"restarts":0,"respawns":0}],
   "launch_ready":false,"shutting_down":false}
→ {"event":"member","path":"/sensing/lidar/driver","state":"running","detail":{"pid":4310},"time":1793930000.1,"restarts":0,"respawns":0}
→ {"event":"member.log","path":"/sensing/lidar/driver","stream":"out","lines":["[INFO] ready"]}
→ {"event":"member.activity","window":2.0,"rates":{"/sensing/lidar/driver":5120}}
→ {"event":"monitor","time_ns":1793930002000000000,"interval_s":2.0,"processes":12,"untracked_endpoints":0,
   "nodes":[{"node":"/sensing/lidar/driver","inputs":[],
             "outputs":[{"topic":"/sensing/lidar/points","hz":10.0,"callback_ms":3.1}],
             "timers":[{"period_ms":100.0,"hz":10.0}]}]}
← {"id":1,"method":"stop","params":{"pattern":"/perception"}}
→ {"id":1,"ok":true,"result":{"members":["/perception/detector"]}}
← {"id":2,"method":"shutdown"}
→ {"id":2,"ok":false,"error":{"code":"refused","message":"shutdown refused: operation mode is autonomous"}}
```

| Method                                            | Params              | Result                                                                       |
| ------------------------------------------------- | ------------------- | ---------------------------------------------------------------------------- |
| `status`                                          | -                   | `{"members": [record], "launch_ready", "shutting_down"}`                     |
| `stop`, `restart`, `kill`                         | `pattern`, `exact`? | `{"members": [matched names]}`; `exact` names one member, no substring match |
| `log.follow`                                      | `path`              | `{"path", "out": [lines], "err": [lines]}`, the tails                        |
| `log.unfollow`                                    | `path`?             | `{"released": [paths]}` (every follow without `path`)                        |
| `measure.start`, `measure.stop`, `measure.status` | -                   | `{"state", "message"}` (needs the tracer)                                    |
| `monitor.sample`                                  | `interval`?         | rates over `interval` seconds (default 1), the `monitor` event's body        |
| `monitor.follow`                                  | -                   | `{"interval_s"}`; `monitor` events every interval until unfollowed           |
| `monitor.unfollow`                                | -                   | `{"released": bool}`                                                         |
| `shutdown`                                        | -                   | `{"shutting_down": true}`                                                    |

A member record is `{"path", "state", "detail", "time", "restarts", "respawns"}`; `path` is the
member name (the node's structural path), `state` one of `pending`, `running`, `stopped`,
`respawning`, `failed`, `done`, `loading`, `loaded`, `detail` carries the state's data (`pid`,
`exit_code`, `attempt`, `error`, `unique_id`), `restarts` counts the starts after the first within
the run and `respawns` the respawns among them. Member output stays in the per-member `out` and `err` files; a client that follows a
member (`log.follow`) gets the files' current tails in the reply and new lines as `member.log`
events until it unfollows or disconnects. Unfollowed members cost the channel nothing. While a
client is connected, `member.activity` reports once per window (2 s) the bytes per second each
member's files grew by, leaving quiet members out; a window without growth is reported once. A
client that follows the monitor gets per-node rates (see [Live rates](#live-rates)) every 2 s; the
counters are read only while some client follows them. The
hello's `log_dir` names the run's log directory, where the files outlive the runtime. Error codes
are stable strings: `bad_request`, `invalid_params`, `unknown_method`, `not_found`,
`unavailable`, `refused`, `payload_too_large`, `internal`.

**Safe-state interlock.** With `--control-policy guard` (the default, also for `--interactive`), the
runtime subscribes to `/api/operation_mode/state` and refuses `stop`, `restart`, `kill` and
`shutdown` while the operation mode is autonomous or in transition. A report stamped more than 10 s
from its publication time is a replayed recording or comes from a system on simulated time, so it
is ignored: a logging simulation replaying an autonomous drive is not interlocked. A system that
never reports a live operation mode is not interlocked, and neither is a signal sent to the runtime
process itself. `--control-policy open` disables the interlock.

---

## Logs

Each member gets its own directory under `--log-dir`:

```text
/tmp/autoware_system_designer_logs/20260521_153012/
├── run.json           the run: system, mode, compute unit, deploy, launch, structure path, start time, member paths
├── rates.jsonl        counters per node and topic over the run (see Live rates)
├── trace/             tracer files of the run's processes
└── <member>/          the member name with every character outside letters, digits, _ - . as _
    ├── cmdline        full argv used to spawn the process
    ├── pid            the process group leader's PID
    ├── out            stdout
    └── err            stderr
```

The default base path is `/tmp/autoware_system_designer_logs/`; a timestamped
subdirectory is created per run unless `--log-dir` overrides it. `run.json` names what the
directory holds, so a reader can map a member directory back to its member path. `--launch-id`
sets its `launch`: the runs of a system's compute units launched together carry one id, each in its
own directory on its own unit. A restarted or respawned member appends to its `out` and `err`.
`autoware_system_designer_runtime.run_log` (no ROS import) holds this layout for other tools: the
log base, the manifest, the member directory name and the tail of a member file.

---

## Architecture

```text
                      ┌────────────────────────────────────────┐
                      │  Coordinator (asyncio main loop)       │
                      │   - signal handlers (SIGINT → shutdown)│
                      │   - state_queue (fan-in)               │
                      │   - shutdown_event / launch_ready      │
                      └───────────┬────────────────────────────┘
                                  │
        ┌─────────────────────────┼────────────────────────────────────────────┐
        │                         │                    │                       │
   RegularNodeActor       RegularNodeActor     RegularNodeActor    ComposableNodeActor
   (single_node)          (node_container)     (ros2_launch_file)  (loaded into container)
       │                          │                    │                       │
       │ cmdline from             │ cmdline from       │ cmdline from          │ awaits container.ready
       │ single_node.py           │ container.py       │ launch_file.py        │ call_async(LoadNode)
       │                          │                    │                       │
       ▼                          ▼                    ▼                       │
   direct binary or         direct binary or       python3 -m …                │
   ros2 run pkg exe         ros2 run pkg ctn       launch_file.py              │
                                                  (LaunchService)              ▼
                                                                          RosWorker
                                                                          (rclpy node on
                                                                           worker thread)
```

| File                                  | Role                                                                                                  |
| ------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `__init__.py`                         | Public names (`populate_builder`, `ActorConfig`, `Coordinator`, …), loaded on first use               |
| `system_runner.py`                    | CLI entry point (`autoware-system-designer-launch`)                                                   |
| `measure_analyze.py`                  | CLI entry point (`autoware-system-designer-measure-analyze`)                                          |
| `export_tree.py`                      | Layout of an export tree: manifest, system structure paths (no ROS import)                            |
| `records.py`                          | Layout of the measurement records (no ROS import)                                                     |
| `rates_record.py`                     | Rates record of a run: writer, reader, rates between two samples (no ROS import)                      |
| `run_log.py`                          | Layout of a run's log directory: log base, manifest, member directory, file tail (no ROS import)      |
| `control_protocol.py`                 | Member actions, measure verbs and interlock policies of the control surfaces (no ROS import)          |
| `_impl/core/config.py`                | `ActorConfig` (respawn, output dir, shutdown timeout)                                                 |
| `_impl/core/events.py`                | Control commands (`Stop`, `Restart`, `KillSignal`, `ToggleRespawn`) and state events of the actors    |
| `_impl/core/state.py`                 | State dataclasses: `NodePending/Running/Respawning/Stopped/Done/Failed`, `Composable*`, `BlockReason` |
| `_impl/core/process.py`               | `spawn_pgrp()`, `graceful_kill()` and the end of a process group                                      |
| `_impl/core/aio.py`                   | asyncio helpers: first of several waits, cancel and wait, future resolved from another thread         |
| `_impl/core/regular_actor.py`         | One asyncio task per regular node / container / launch-file include                                   |
| `_impl/core/coordinator.py`           | `CoordinatorBuilder`, `Coordinator`, `MemberHandle`                                                   |
| `_impl/core/stdin_console.py`         | Optional stdin REPL                                                                                   |
| `_impl/core/commands.py`              | Command surface shared by the REPL and the control channel; member run states from the state hook     |
| `_impl/core/control_channel.py`       | Unix socket serving member states, logs and the command set as JSON lines                             |
| `_impl/ros2/operation_mode.py`        | Interlock on the ADAPI operation mode                                                                 |
| `_impl/ros2/builder.py`               | system_structure JSON → populated `CoordinatorBuilder` (ROS 2)                                        |
| `_impl/ros2/launchers/single_node.py` | Cmdline builder for `single_node` launch type (`node_cmdline`)                                        |
| `_impl/ros2/launchers/container.py`   | Cmdline builder for `node_container` (`container_cmdline`) + `RosWorker`                              |
| `_impl/ros2/launchers/composable.py`  | `ComposableSpec`, `ComposableNodeActor`; YAML param flattening for `LoadNode`                         |
| `_impl/ros2/launchers/launch_file.py` | Cmdline builder + subprocess runner for `ros2_launch_file` includes                                   |
| `_impl/ros2/common/params.py`         | `resolve_value`, `build_cmd`, `parameter_layers`, `remap_pairs`                                       |
| `_impl/ros2/common/namespace.py`      | `parent_namespace`, `node_fqn`, `join_fqn`, `unique_node_name`                                        |
| `_impl/ros2/common/substitutions.py`  | Launch-time substitutions and the binding of system arguments                                         |
| `_impl/measure/tracer.py`             | Arming of the tracer: preload library, trace directory, control word                                  |
| `_impl/measure/stats_reader.py`       | Reader of the tracer's counters (`<pid>.stats`)                                                       |
| `_impl/measure/monitor.py`            | Live rates from two reads of the counters; recorder of the rates record                               |
| `_impl/measure/session.py`            | Measurement window: settle, record, close, analyze                                                    |
| `_impl/measure/trace_reader.py`       | Reader of the tracer's event records (`<pid>.trace`) and name tables                                  |
| `_impl/measure/clock.py`              | Mapping of wall time onto ROS time                                                                    |
| `_impl/measure/node_graph.py`         | Nodes, ports and topic links of the design the analysis matches against                               |
| `_impl/measure/node_stats.py`         | Per-node rates, process time and response from the records                                            |
| `_impl/measure/detect.py`             | Trigger detection and the diff against the declared process events                                    |
| `_impl/measure/chains.py`             | Event chains across nodes                                                                             |
| `_impl/measure/probe.py`              | Probe subscriptions on intra-process-only topics                                                      |
| `_impl/measure/writer.py`             | Latency file                                                                                          |

---

## Library use

The CLI in `system_runner.py` is one consumer of the package.
For programmatic use:

```python
import asyncio
import json
from autoware_system_designer_runtime import (
    populate_builder, ActorConfig, ensure_output_dir,
)

async def main():
    with open("system_structure/MySystem.json") as f:
        data = json.load(f)

    config = ActorConfig(
        respawn_enabled=True,
        respawn_delay=1.0,
        output_dir=ensure_output_dir(),
    )

    builder, worker = populate_builder(data["data"], ecu="main_ecu", config=config)
    coord = builder.build()
    await worker.start()            # rclpy node on worker thread (async)
    try:
        return await coord.run()    # blocks until SIGINT or all actors terminate
    finally:
        await worker.stop()         # async

asyncio.run(main())
```

### Sending commands to a member

```python
handle = coord.handle("/pointcloud_container")
await handle.stop()                # graceful shutdown
await handle.restart()             # stop + respawn
await handle.kill(signal.SIGKILL)  # immediate kill on pgid
await handle.set_respawn(False)    # disable auto-respawn for this one
```

### Consuming state events

The coordinator drives its own state-event loop. A sidecar that follows the stream (e.g. to feed a UI) registers a callback with `builder.add_state_hook(hook)` before `build()`; the loop calls it with every actor state event. `coord.state_queue` is the queue the actors write to, read by the coordinator alone.

---

## Comparison with `play_launch`

| Concept                   | play_launch (Rust)                                            | this runtime (Python)                                                           |
| ------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Actor                     | `RegularNodeActor` / `ContainerActor` / `ComposableNodeActor` | `RegularNodeActor` (also for containers) / `ComposableNodeActor`, asyncio tasks |
| Coordinator               | `MemberCoordinator{Builder,Runner,Handle}`                    | `Coordinator{Builder,Handle}`                                                   |
| Process kill              | SIGTERM → 5s → SIGKILL on pgid                                | `process.py::graceful_kill`: SIGINT → 5s → SIGKILL on pgid                      |
| Composable load           | direct LoadNode service call                                  | identical, via `composition_interfaces/srv/LoadNode`                            |
| YAML param inlining       | `LoadNodeRecord` builder strips params_files                  | `composable.py::flatten_for_fqn`                                                |
| Launch file include       | `ros2 launch` CLI subprocess                                  | `launch_file.py` runner (direct LaunchService, with global param injection)     |
| Input format              | `record.json`                                                 | `system_structure.json` (Autoware-native)                                       |
| Web UI / SSE / REST       | yes                                                           | in `autoware_system_designer_studio`, over the control channel                  |
| Resource monitoring       | yes (CSV per-node)                                            | **no**; topic rates and latency instead (see Live rates)                        |
| LD_PRELOAD interception   | yes                                                           | `autoware_system_designer_tracer`, for rates and latency                        |
| Container isolation modes | yes (`stock`/`observable`/`isolated`)                         | **no** (uses whatever container is in the JSON)                                 |

The two systems share the design pattern but not code. When play_launch's parser-side behavior is needed, run that tool directly; this runtime exists to keep autoware_system_designer's runtime layer self-contained.

---

## Caveats

- **Composable params** are flattened from YAML in the actor; rclpy is on a worker thread and the LoadNode service wait and call are unbounded as in launch_ros, with a warning past 30 s each
  (`service_wait_warn_s`, `load_call_warn_s` of `ComposableNodeActor.__init__`).
- **`ros2_launch_file` entities** are wrapped as a single Python subprocess that runs `LaunchService` directly (not the `ros2 launch` CLI). Global parameters (`vehicle_info.param.yaml`) are injected as `SetParameter` actions before the include so all nodes in the subtree receive them — even though each wrapper runs in a separate process from the `global_parameter_loader`. You lose per-leaf-node visibility _inside_ the include — by design.
- **Empty / placeholder nodes** (single_node entries whose `executable` is empty) are silently skipped during build; check the log at startup.
- **Direct binary spawn**: `build_cmd` resolves the executable path via `ros2run.api.get_executable_path` to deliver `SIGINT` directly to rclcpp rather than through a Python wrapper. Falls back to `ros2 run <pkg> <exe>` with a warning if resolution fails.
- **`$(command '...')` values** run the command as an argument list without a shell, as ROS 2 launch's `Command` substitution does; pipes, redirections, globs and `$VAR` expansion are not interpreted.
- **Namespace data convention**: system_structure stores `namespace` as the full path of the entity (including its own name as the last segment when the entity sits under a same-named module). The builder strips that last segment if it equals `name`. See `common/namespace.py::parent_namespace`.

---

## Live rates

Every launch preloads a small C recorder (`autoware_system_designer_tracer`, an `LD_PRELOAD` interposer over the `rcl` C API) into every spawned process. It keeps one counter slot per publisher, subscription and timer in a shared-memory file (`<log-dir>/trace/<pid>.stats`), bumped with atomic adds on each publish, take and timer fire; nothing subscribes to a topic and no message is copied. A process that never calls rcl leaves no file, and the slot of a finalized endpoint serves the next one created, so a node that keeps creating and destroying endpoints stays counted. The measured cost is tens of nanoseconds per publish or take. `--no-tracer` launches without it (and without measurement).

The runtime turns two reads of the counters into per-node rates: input topics with take rate and mean communication time (take time minus the DDS source timestamp), output topics with publish rate and mean callback time (publish minus the thread's latest take or timer fire), and timers with fire rate. A timer has a row of its own; it is listed under its process until a fire of it leads to a publish, from then on under the node of that publish with the fires since. A topic whose rate is zero carries `silent_s`, the time since its last message. Nodes are keyed by design path, else by their fully qualified name. Rates are in wall time.

```text
[console] rates /sensing                    # --interactive; over one second
rates over 1 s, 12 traced processes
/sensing/lidar/driver
  -> /sensing/lidar/points                                         10.00 Hz  callback 3.100 ms
  timer 100 ms                                                     10.00 Hz
```

Over the control channel: `monitor.sample` once, `monitor.follow` for a `monitor` event every 2 s. A sample is answered after its interval; the requests behind it are answered meanwhile.

Every run also keeps its rates: `<log-dir>/rates.jsonl` records the counters per node and topic every 10 s and once more at shutdown, cumulative over the run (a process that ends or respawns keeps its counts), so the rates over any span of a past run are the difference of two samples. A sample line lists only the counters that changed since the previous one. `autoware_system_designer_runtime.rates_record` (no ROS import) reads it back: `RatesRecord.load(path).rates(until_ns=..., span_s=...)`, or `rates(start=..., sample=...)` between two sample numbers, the whole run by default; `density()` gives the counted events per second over each sample interval. A publish whose every matched reader is intra-process does not reach `rcl_publish`, so it is not counted; the measurement window's probe subscriptions cover that case.

## Latency measurement

The same tracer records one event per publish, take, timer fire and ROS time override while a measurement window is open (a `control` word in the trace directory, read by every traced process, switches recording), and the analysis turns the recording into per-node process time, topic communication time and event-chain end-to-end time, measured separately and never derived from one another. No CARET, no rclcpp fork, no rebuild of Autoware.

```bash
autoware-system-designer-launch SYSTEM.json \
    --measure --measure-duration 60      # settle 5 s, record 60 s, shut down, analyze
    [--measure-settle S]                 # seconds after launch_ready left out of the window (default 5)
    [--measure-keep-running]             # analyze with the system still up and keep it running
    [--no-probe]                         # do not add probe subscriptions on intra-process-only topics
    [--measure-clock auto|wall|ros]      # time base of durations and rates (default auto: ros when the system runs on /clock)
    [--latency-out FILE]                 # JSON; default: a new record of the export (below), else <log-dir>/latency/<Mode>_latency.json
```

The session logs every step under the `measure:` prefix: the window opening (with the wall-clock time it closes), a heartbeat every 10 s with the phase (`settling` / `recording x/y s`) and the trace counters, the window closing with its reason, the shutdown it requests, and the analysis start and result. With `--measure-duration` the system is shut down as soon as the window closes; the analysis reads the trace files after the actors have terminated. A shutdown from elsewhere (signal, actor failure) closes the window at that moment and analyzes what was recorded.

Without `--measure-duration` the window opens at `launch_ready` and closes at shutdown or on the console verb `measure stop` (`--interactive`: `measure start | stop | status`; `status` prints the heartbeat line on demand). Without `--measure` the window opens on `measure start` (console or control channel); one window per launch. A window is 30 min at most: a longer `--measure-duration` is cut to it, and of a recording that ran longer the last 30 min are analyzed. Each process keeps its events in a ring (`ASD_TRACE_CAPACITY` records, 256 MiB by default), so a process busier than about 2300 events/s holds less than 30 min and the window starts where every process still holds its records. Trace files stay under `<log-dir>/trace/` and can be re-analyzed offline:

```bash
autoware-system-designer-measure-analyze <log-dir>/trace SYSTEM.json -o FILE
```

### What is measured

The unit of analysis is the node. Ports and inter-node topic links come from the design and are trusted; the design's process events are a claim that the analysis diffs against what was observed, output by output (`declared_diff`).

A system on `/clock` (`use_sim_time`, every simulation mode) declares its rates and runs its timers in ROS time, so its durations and rates are counted there: the tracer records every ROS time override a process receives, the analysis maps wall time onto that curve, and `run.clock` names the base (`ros` with the rate against wall time and the sample count, or `wall`). Records stay ordered and matched in wall time; only durations change. A paused bag keeps publishing its frozen `/clock` value; the tracer flags the last sighting of a value held longer than twice the gap before it, the curve stays flat across it, and nothing inside the pause counts. `--measure-clock wall` measures a simulation in wall time; `auto` falls back to wall time when the trace holds no `/clock` samples.

| Quantity                  | Definition                                                                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Rates                     | takes, publishes and timer fires per second, per topic or timer                                                                                                                            |
| Process time (`exec`)     | trigger → publish on the same thread; the trigger is the take, timer fire or (intra-process) upstream publish immediately preceding on that thread                                         |
| Response                  | `publish − latest take of input i` on any thread of the node: the age the node adds when it samples `i` instead of being triggered by it                                                   |
| Trigger                   | the dominant class of what preceded an output over the run: `timer(period)`, `input(topic)`, `input(topic, intra_process)`, `unknown`                                                      |
| Communication (`links[]`) | `take − source_timestamp` per topic, publisher node and subscriber node; a take is matched to its publish by topic and a source timestamp inside the publish call                          |
| Chain (`chains[]`)        | per instance: detected timer fire → same-thread publish → matched takes → the publishes they trigger → … → terminal publish; where a node samples, the chain continues at its next publish |

Intra-process communication stays on. rclcpp skips `rcl_publish` when every matched reader is intra-process, so the runtime's own rclpy node adds one best-effort **probe subscription** to each such topic during the window; the publish is then recorded and the hop is folded into the downstream node's process time (`links[]` marks it `intra_process`, the inter-process duplicate the subscription still receives is dropped from every statistic). Approximations are recorded, not hidden: a publish from a thread that never took or fired has an `unknown` trigger, a node that never publishes has no `exec`, dropped records are counted in `run`, design nodes without records and traced nodes outside the design are listed (`unobserved_nodes`, `unmatched_nodes`), and `summary` holds the run-level counts. Each node record also says what became of its process (`process.state`: `running`, `exited` with the exit the runtime saw inside the window, or `not_initialized` when the process created only the endpoints every node owns), and its notes separate a declared input the node never subscribed to from one it subscribed to and never received. The latency file is the only output; it is meant to be consumed by tooling (the studio, diagnostics), not read.

The latency file (`autoware_system_designer/latency/2`) is documented in [doc/latency_view.md](../autoware_system_designer/doc/latency_view.md#measurement-file). The file is one JSON object. By default each run adds a **record** of the export the JSON belongs to:

```text
<record root>/<package>/<system>/latency/<Mode>/<YYYYmmdd-HHMMSS>.json
```

The record root is `$AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR`, else `$ROS_HOME/autoware_system_designer/records` (`~/.ros/...` when `ROS_HOME` is unset): outside the install tree, which every rebuild replaces, and outside the per-run log directories. A JSON outside an export tree writes `<log-dir>/latency/<Mode>_latency.json` instead. The studio shows the newest record of a mode, else the reference measurement the export carries (`latency/<Mode>_latency.json` beside the system definition file, copied into `exports/<system>/latency/` by the build). `autoware_system_designer_runtime.records` holds this layout for other tools.
