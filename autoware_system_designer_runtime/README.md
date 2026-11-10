# `runtime/` — Actor-based system supervisor

A Python actor runtime that **runs** an Autoware system_structure JSON directly: each node (regular, container, composable, ros2_launch_file include) becomes a supervised subprocess with its own state machine. Inspired by [play_launch](https://github.com/tier4/play_launch), reimplemented in Python with no play_launch import and no external binary.

Each member is a first-class task: you can inspect, stop, and restart nodes individually without tearing down the whole system.

---

## Why

`launch.LaunchService` hides every spawned process behind a single event loop.
You cannot tell which node crashed, kill one without killing all, or restart a single component. This runtime mirrors play_launch's actor pattern so each member is a first-class task with:

- explicit state (`Pending → Running → Stopped | Failed | Respawning`)
- per-member control queue (`Stop`, `Restart`, `Kill`, `ToggleRespawn`)
- per-member state-event stream (`Started`, `Exited`, `Failed`, …)
- a dedicated log directory (`out`, `err`, `pid`, `cmdline`)
- graceful shutdown: `SIGTERM` to the process group, 5s grace, then `SIGKILL`

---

## Quickstart

### 1. Build & source the workspace

```bash
cd ~/workspace/awf/autoware
colcon build --packages-up-to autoware_system_designer autoware_system_design_examples autoware_sample_designs
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

`Ctrl+C` triggers graceful shutdown: the coordinator broadcasts `Stop` to every
member, each actor `SIGTERM`s its process group, waits 5s, then `SIGKILL`s.

### 3. Useful flags

```bash
autoware-system-designer-launch SYSTEM.json \
    --compute-unit main_ecu \           # only launch nodes with compute_unit=main_ecu
    --deploy my_vehicle \               # deploy variant whose arguments bind $(var ...)
    --arg map_path=/data/map \          # bind/override one system argument (repeatable)
    --log-dir /tmp/run1 \               # per-node logs land in /tmp/run1/<member>/
    --respawn \                         # restart any node that exits abnormally (non-zero)
    --respawn-delay 1.0 \
    --max-respawn-attempts 5 \
    --graceful-shutdown-timeout 10 \    # seconds SIGTERM→SIGKILL grace
    --interactive \                     # enable stdin REPL (see below)
    --control /run/user/1000/asd.sock \ # control channel on a Unix socket (see below)
    --control-policy guard \            # guard (default) | open: interlock on the operation mode
    --log-level DEBUG
```

### 4. Interactive control (`--interactive`)

With `--interactive`, the launcher reads commands from stdin while running:

```text
autoware runtime [? for help] > status
  /perception/object_recognition/detector_a1/node_filter#single_node
  /perception/object_recognition/node_tracker#single_node
  /pointcloud_container#node_container
autoware runtime [? for help] > stop /perception/object_recognition/detector_a1
[console] stop -> /perception/object_recognition/detector_a1/node_filter#single_node
autoware runtime [? for help] > restart /pointcloud_container
[console] restart -> /pointcloud_container#node_container
autoware runtime [? for help] > quit
```

Commands: `status`, `stop <name>`, `restart <name>`, `kill <name>`, `quit`, `help` / `?`.

`<name>` matches the member named exactly so, else as a **substring of the member name**, so namespace prefixes are natural targets (`stop /perception` stops everything under that subtree).

### 5. Control channel (`--control SOCKET`)

`--control` serves the same command set on a Unix socket (mode `0600`, removed on exit), as JSON
lines, for a supervising process such as the studio daemon. The runtime sends events; a client sends
requests and gets one response each:

```text
→ {"event":"hello","pid":4242,"methods":[...],"policy":"guard","measure_armed":false,
   "log_dir":"/tmp/autoware_system_designer_logs/20260521_153012","members":[{"path":"/sensing/lidar/driver","state":"pending","detail":{},"time":0}],
   "launch_ready":false,"shutting_down":false}
→ {"event":"member","path":"/sensing/lidar/driver","state":"running","detail":{"pid":4310},"time":1793930000.1}
→ {"event":"member.log","path":"/sensing/lidar/driver","stream":"out","lines":["[INFO] ready"]}
→ {"event":"member.activity","window":2.0,"rates":{"/sensing/lidar/driver":5120}}
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
| `measure.start`, `measure.stop`, `measure.status` | -                   | `{"state", "message"}` (needs `--measure`)                                   |
| `shutdown`                                        | -                   | `{"shutting_down": true}`                                                    |

A member record is `{"path", "state", "detail", "time"}`; `path` is the member name (the node's
structural path), `state` one of `pending`, `running`, `stopped`, `respawning`, `failed`, `done`,
`loading`, `loaded`, and `detail` carries the state's data (`pid`, `exit_code`, `attempt`, `error`,
`unique_id`). Member output stays in the per-member `out` and `err` files; a client that follows a
member (`log.follow`) gets the files' current tails in the reply and new lines as `member.log`
events until it unfollows or disconnects. Unfollowed members cost the channel nothing. While a
client is connected, `member.activity` reports once per window (2 s) the bytes per second each
member's files grew by, leaving quiet members out; a window without growth is reported once. The
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
├── run.json           the run: system, mode, compute unit, deploy, structure path, start time, member paths
└── <member-slug>/
    ├── cmdline        full argv used to spawn the process
    ├── pid            the process group leader's PID
    ├── out            stdout
    └── err            stderr
```

The default base path is `/tmp/autoware_system_designer_logs/`; a timestamped
subdirectory is created per run unless `--log-dir` overrides it. `run.json` names what the
directory holds, so a reader can map a member directory back to its member path.

---

## Architecture

```text
                      ┌────────────────────────────────────────┐
                      │  Coordinator (asyncio main loop)       │
                      │   - signal handlers (SIGINT → Stop)    │
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

| File                                  | Role                                                                                                                                  |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `__init__.py`                         | Public re-exports (`populate_builder`, `ActorConfig`, `Coordinator`, …)                                                               |
| `system_runner.py`                    | CLI entry point (`autoware-system-designer-launch`)                                                                                   |
| `_impl/core/config.py`                | `ActorConfig` (respawn, output dir, shutdown timeout)                                                                                 |
| `_impl/core/events.py`                | `ControlEvent` / `StateEvent` dataclasses                                                                                             |
| `_impl/core/state.py`                 | State dataclasses: `NodePending/Running/Respawning/Stopped/Failed`, `ComposableBlocked/Unloaded/Loading/Loaded/Failed`, `BlockReason` |
| `_impl/core/process.py`               | `spawn_pgrp()` + `graceful_kill()` (pgid-aware)                                                                                       |
| `_impl/core/regular_actor.py`         | One asyncio task per regular node / container / launch-file include                                                                   |
| `_impl/core/coordinator.py`           | `CoordinatorBuilder`, `Coordinator`, `MemberHandle`                                                                                   |
| `_impl/core/stdin_console.py`         | Optional stdin REPL                                                                                                                   |
| `_impl/core/commands.py`              | Command surface shared by the REPL and the control channel; member run states from the state hook                                     |
| `_impl/core/control_channel.py`       | Unix socket serving member states, logs and the command set as JSON lines                                                             |
| `_impl/ros2/operation_mode.py`        | Interlock on the ADAPI operation mode                                                                                                 |
| `_impl/ros2/builder.py`               | system_structure JSON → populated `CoordinatorBuilder` (ROS 2)                                                                        |
| `_impl/ros2/launchers/single_node.py` | Cmdline builder for `single_node` launch type (`node_cmdline`)                                                                        |
| `_impl/ros2/launchers/container.py`   | Cmdline builder for `node_container` (`container_cmdline`) + `RosWorker`                                                              |
| `_impl/ros2/launchers/composable.py`  | `ComposableSpec`, `ComposableNodeActor`; YAML param flattening for `LoadNode`                                                         |
| `_impl/ros2/launchers/launch_file.py` | Cmdline builder + subprocess runner for `ros2_launch_file` includes                                                                   |
| `_impl/ros2/common/params.py`         | `resolve_value`, `build_cmd`, `_ros_args`, `parameter_files`, `remap_pairs`                                                           |
| `_impl/ros2/common/namespace.py`      | `parent_namespace`, `node_fqn`, `join_fqn`, `unique_node_name`                                                                        |

---

## Library use

The CLI in `runtime/system_runner.py` is one consumer of `runtime/`.
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
handle = coord.handle("/pointcloud_container#node_container")
await handle.stop()                # graceful shutdown
await handle.restart()             # stop + respawn
await handle.kill(signal.SIGKILL)  # immediate kill on pgid
await handle.set_respawn(False)    # disable auto-respawn for this one
```

### Consuming state events

The coordinator drives its own state-event loop, but if you want a sidecar that taps the stream (e.g., to feed a UI), spawn a task that reads from `coord.state_queue` — be aware you'll race the coordinator's reader, so the recommended pattern is to wrap the coordinator instead.

---

## Comparison with `play_launch`

| Concept                   | play_launch (Rust)                                            | this runtime (Python)                                                       |
| ------------------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Actor                     | `RegularNodeActor` / `ContainerActor` / `ComposableNodeActor` | same names, asyncio tasks                                                   |
| Coordinator               | `MemberCoordinator{Builder,Runner,Handle}`                    | `Coordinator{Builder,Handle}`                                               |
| Process kill              | SIGTERM → 5s → SIGKILL on pgid                                | `process.py::graceful_kill` (identical)                                     |
| Composable load           | direct LoadNode service call                                  | identical, via `composition_interfaces/srv/LoadNode`                        |
| YAML param inlining       | `LoadNodeRecord` builder strips params_files                  | `composable.py::flatten_for_fqn`                                            |
| Launch file include       | `ros2 launch` CLI subprocess                                  | `launch_file.py` runner (direct LaunchService, with global param injection) |
| Input format              | `record.json`                                                 | `system_structure.json` (Autoware-native)                                   |
| Web UI / SSE / REST       | yes                                                           | **no** (explicitly out of scope)                                            |
| Resource monitoring       | yes (CSV per-node)                                            | **no**                                                                      |
| LD_PRELOAD interception   | yes                                                           | **no**                                                                      |
| Container isolation modes | yes (`stock`/`observable`/`isolated`)                         | **no** (uses whatever container is in the JSON)                             |

The two systems share the design pattern but not code. When play_launch's parser-side behavior is needed, run that tool directly; this runtime exists to keep autoware_system_designer's runtime layer self-contained.

---

## Caveats

- **Composable params** are flattened from YAML in the actor; rclpy is on a worker thread and the LoadNode service has a 30 s default timeout per call.
  Slow containers will need a higher timeout (configurable in `ComposableNodeActor.__init__`).
- **`ros2_launch_file` entities** are wrapped as a single Python subprocess that runs `LaunchService` directly (not the `ros2 launch` CLI). Global parameters (`vehicle_info.param.yaml`) are injected as `SetParameter` actions before the include so all nodes in the subtree receive them — even though each wrapper runs in a separate process from the `global_parameter_loader`. You lose per-leaf-node visibility _inside_ the include — by design.
- **Empty / placeholder nodes** (single_node entries whose `executable` is empty) are silently skipped during build; check the log at startup.
- **Direct binary spawn**: `build_cmd` resolves the executable path via `ros2run.api.get_executable_path` to deliver `SIGTERM` directly to rclcpp rather than through a Python wrapper. Falls back to `ros2 run <pkg> <exe>` with a warning if resolution fails.
- **`$(command '...')` values** run the command as an argument list without a shell, as ROS 2 launch's `Command` substitution does; pipes, redirections, globs and `$VAR` expansion are not interpreted.
- **Namespace data convention**: system_structure stores `namespace` as the full path of the entity (including its own name as the last segment when the entity sits under a same-named module). The builder strips that last segment if it equals `name`. See `common/namespace.py::parent_namespace`.

---

## Latency measurement

`--measure` preloads a small C recorder (`autoware_system_designer_tracer`, an `LD_PRELOAD` interposer over the `rcl` C API) into every spawned process and analyzes the recording per node: process time, topic communication time and event-chain end-to-end time, measured separately and never derived from one another. No CARET, no rclcpp fork, no rebuild of Autoware.

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

Without `--measure-duration` the window opens at `launch_ready` and closes at shutdown or on the console verb `measure stop` (`--interactive`: `measure start | stop | status`; `status` prints the heartbeat line on demand). Trace files stay under `<log-dir>/trace/` and can be re-analyzed offline:

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
