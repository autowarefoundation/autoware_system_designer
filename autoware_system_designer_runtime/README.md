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
ros2 run autoware_system_designer_runtime autoware-system-designer-launch \
    /path/to/system_structure/MySystem.json --deploy my_vehicle
```

System arguments (`$(var vehicle_id)` and friends) are bound at launch time from the
deploy variant named by `--deploy`, read from the `deployment.json` beside
`system_structure/`; `--arg NAME=VALUE` binds or overrides one argument directly.
A system that declares no arguments needs neither.

The console script installs under `lib/<package>/`, so `ros2 run` is the way to reach it;
`install/setup.bash` does not put it on `PATH`.

`Ctrl+C` triggers graceful shutdown: the coordinator broadcasts `Stop` to every
member, each actor `SIGTERM`s its process group, waits 5s, then `SIGKILL`s.

### 3. Useful flags

```bash
ros2 run autoware_system_designer_runtime autoware-system-designer-launch SYSTEM.json \
    --ecu main_ecu \                    # only launch nodes with compute_unit=main_ecu
    --deploy my_vehicle \               # deploy variant whose arguments bind $(var ...)
    --arg map_path=/data/map \          # bind/override one system argument (repeatable)
    --log-dir /tmp/run1 \               # per-node logs land in /tmp/run1/<member>/
    --respawn \                         # restart any node that exits for any reason
    --respawn-delay 1.0 \
    --max-respawn-attempts 5 \
    --graceful-shutdown-timeout 10 \    # seconds SIGTERM→SIGKILL grace
    --interactive \                     # enable stdin REPL (see below)
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

`<name>` matches as a **substring of the member name** so namespace prefixes are natural targets (`stop /perception` stops everything under that subtree).

---

## Logs

Each member gets its own directory under `--log-dir`:

```text
/tmp/autoware_system_designer_logs/20260521_153012/
└── <member-slug>/
    ├── cmdline        full argv used to spawn the process
    ├── pid            the process group leader's PID
    ├── out            stdout
    └── err            stderr
```

The default base path is `/tmp/autoware_system_designer_logs/`; a timestamped
subdirectory is created per run unless `--log-dir` overrides it.

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
- **Namespace data convention**: system_structure stores `namespace` as the full path of the entity (including its own name as the last segment when the entity sits under a same-named module). The builder strips that last segment if it equals `name`. See `common/namespace.py::parent_namespace`.

---

## Latency measurement

`--measure` preloads a small C recorder (`autoware_system_designer_tracer`, an `LD_PRELOAD` interposer over the `rcl` C API) into every spawned process and analyzes the recording per node: process time, topic communication time and event-chain end-to-end time, measured separately and never derived from one another. No CARET, no rclcpp fork, no rebuild of Autoware.

```bash
ros2 run autoware_system_designer_runtime autoware-system-designer-launch SYSTEM.json \
    --measure --measure-duration 60      # settle 5 s, record 60 s, shut down, analyze
    [--measure-settle S]                 # seconds after launch_ready left out of the window (default 5)
    [--measure-keep-running]             # analyze with the system still up and keep it running
    [--no-probe]                         # do not add probe subscriptions on intra-process-only topics
    [--latency-out FILE]                 # default: <export>/visualization/web/data/<Mode>_latency.json, else <log-dir>/latency/
```

The session logs every step under the `measure:` prefix: the window opening (with the wall-clock time it closes), a heartbeat every 10 s with the phase (`settling` / `recording x/y s`) and the trace counters, the window closing with its reason, the shutdown it requests, and the analysis start and result. With `--measure-duration` the system is shut down as soon as the window closes; the analysis reads the trace files after the actors have terminated. A shutdown from elsewhere (signal, actor failure) closes the window at that moment and analyzes what was recorded.

Without `--measure-duration` the window opens at `launch_ready` and closes at shutdown or on the console verb `measure stop` (`--interactive`: `measure start | stop | status`; `status` prints the heartbeat line on demand). Trace files stay under `<log-dir>/trace/` and can be re-analyzed offline:

```bash
ros2 run autoware_system_designer_runtime autoware-system-designer-measure-analyze <log-dir>/trace SYSTEM.json -o FILE
```

### What is measured

The unit of analysis is the node. Ports and inter-node topic links come from the design and are trusted; the design's process events are a claim that the analysis diffs against what was observed, output by output (`declared_diff`).

| Quantity                  | Definition                                                                                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Rates                     | takes, publishes and timer fires per second, per topic or timer                                                                                                                            |
| Process time (`exec`)     | trigger → publish on the same thread; the trigger is the take, timer fire or (intra-process) upstream publish immediately preceding on that thread                                         |
| Response                  | `publish − latest take of input i` on any thread of the node: the age the node adds when it samples `i` instead of being triggered by it                                                   |
| Trigger                   | the dominant class of what preceded an output over the run: `timer(period)`, `input(topic)`, `input(topic, intra_process)`, `unknown`                                                      |
| Communication (`links[]`) | `take − source_timestamp` per topic, publisher node and subscriber node; a take is matched to its publish by topic and a source timestamp inside the publish call                          |
| Chain (`chains[]`)        | per instance: detected timer fire → same-thread publish → matched takes → the publishes they trigger → … → terminal publish; where a node samples, the chain continues at its next publish |

Intra-process communication stays on. rclcpp skips `rcl_publish` when every matched reader is intra-process, so the runtime's own rclpy node adds one best-effort **probe subscription** to each such topic during the window; the publish is then recorded and the hop is folded into the downstream node's process time (`links[]` marks it `intra_process`, the inter-process duplicate the subscription still receives is dropped from every statistic). Approximations are recorded, not hidden: a publish from a thread that never took or fired has an `unknown` trigger, a node that never publishes has no `exec`, dropped records are counted in `run`, design nodes without records and traced nodes outside the design are listed (`unobserved_nodes`, `unmatched_nodes`), and `summary` holds the run-level counts. The latency file is the only output; it is meant to be consumed by tooling (the visualizer, diagnostics), not read.

The latency file (`autoware_system_designer/latency/2`) is documented in [doc/latency_view.md](../autoware_system_designer/doc/latency_view.md#measurement-file). By default it is written into the export's visualization bundle (`<export>/visualization/web/data/`, beside the `system_structure/` directory the JSON came from), which is where the sequence diagram fetches `data/<Mode>_latency.json`; the next reload of the deployment overview shows it. A rebuild regenerates the bundle, so keep a copy as `latency/<Mode>_latency.json` beside the system definition file to have the build carry it over.
