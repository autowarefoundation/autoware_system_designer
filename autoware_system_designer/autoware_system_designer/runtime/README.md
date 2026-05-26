# `runtime/` — Actor-based system supervisor

A Python actor runtime that **runs** an Autoware system_structure JSON directly: each
node (regular, container, composable, ros2_launch_file include) becomes a supervised
subprocess with its own state machine. Inspired by
[play_launch](https://github.com/NEWSLabNTU/play_launch), reimplemented in Python with no
play_launch import and no external binary.

Each member is a first-class task: you can inspect, stop, and restart nodes
individually without tearing down the whole system.

---

## Why

`launch.LaunchService` hides every spawned process behind a single event loop.
You cannot tell which node crashed, kill one without killing all, or restart a
single component. This runtime mirrors play_launch's actor pattern so each
member is a first-class task with:

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
autoware-system-designer-launch /path/to/system_structure/MySystem.json
```

`Ctrl+C` triggers graceful shutdown: the coordinator broadcasts `Stop` to every
member, each actor `SIGTERM`s its process group, waits 5s, then `SIGKILL`s.

### 3. Useful flags

```bash
autoware-system-designer-launch SYSTEM.json \
    --ecu main_ecu \                    # only launch nodes with compute_unit=main_ecu
    --log-dir /tmp/run1 \               # per-node logs land in /tmp/run1/<member>/
    --respawn \                         # restart any node that exits for any reason
    --respawn-delay 2.0 \
    --max-respawn-attempts 5 \
    --graceful-shutdown-timeout 10 \    # seconds SIGTERM→SIGKILL grace
    --interactive \                     # enable stdin REPL (see below)
    --log-level DEBUG
```

### 4. Interactive control (`--interactive`)

With `--interactive`, the launcher reads commands from stdin while running:

```text
[console] type 'status', 'stop <name>', 'restart <name>', 'kill <name>', 'quit'
status
  /perception/object_recognition/detector_a1/node_filter#single_node
  /perception/object_recognition/node_tracker#single_node
  /pointcloud_container#node_container
stop /perception/object_recognition/detector_a1
[console] stop -> /perception/object_recognition/detector_a1/node_filter#single_node
restart /pointcloud_container
[console] restart -> /pointcloud_container#node_container
quit
```

`<name>` matches as a **substring of the member name** so namespace prefixes are
natural targets (`stop /perception` stops everything under that subtree).

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
                      │   - shutdown_event                     │
                      └───────────┬────────────────────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────────┐
        │                         │                             │
   RegularNodeActor          RegularNodeActor              ComposableNodeActor
   (single_node)             (node_container)              (loaded into container)
       │                         │                             │
       │ asyncio.subprocess      │ asyncio.subprocess          │ awaits container.ready
       │ state machine           │ state machine               │ wait_for_service
       │ control_q ──◄── handle  │ control_q ──◄── handle      │ call_async(LoadNode)
       ▼                         ▼                             ▼
   ros2 run pkg exec         ros2 run pkg container       (no subprocess —
   --ros-args …              --ros-args …                  load via service)
                                                                ▲
                                                                │
                                                          RosWorker
                                                          (rclpy node on
                                                           worker thread)
```

| File                        | Role                                                                             |
| --------------------------- | -------------------------------------------------------------------------------- |
| `__init__.py`               | Public re-exports (`populate_builder`, `ActorConfig`, `Coordinator`, …)          |
| `system_runner.py`          | CLI entry point (`autoware-system-designer-launch`)                              |
| `ros2_launch_runner.py`     | Subprocess entry point for `ros2_launch_file` wrapper units                      |
| `_impl/state.py`            | Enums for `NodeState`, `ComposableState`, `ContainerStatus`, `BlockReason`       |
| `_impl/events.py`           | `ControlEvent` / `StateEvent` dataclasses                                        |
| `_impl/config.py`           | `ActorConfig` (respawn, output dir, shutdown timeout)                            |
| `_impl/process.py`          | `spawn_pgrp()` + `graceful_kill()` (pgid-aware)                                  |
| `_impl/regular_actor.py`    | One asyncio task per regular node / container                                    |
| `_impl/composable_actor.py` | One task per composable; awaits container ready + calls `LoadNode`               |
| `_impl/container_actor.py`  | `RosWorker` — owns the shared rclpy node + service clients                       |
| `_impl/params.py`           | YAML param flattening (`/**`, FQN, `*` wildcards) → `rcl_interfaces.Parameter[]` |
| `_impl/coordinator.py`      | `CoordinatorBuilder`, `Coordinator`, `MemberHandle`                              |
| `_impl/builder.py`          | system_structure JSON → populated `CoordinatorBuilder`                           |
| `_impl/stdin_console.py`    | Optional stdin REPL                                                              |

---

## Library use

The CLI in `runtime/system_runner.py` is one consumer of `runtime/`.
For programmatic use:

```python
import asyncio
import json
from autoware_system_designer.runtime import (
    populate_builder, ActorConfig, ensure_output_dir,
)

async def main():
    with open("system_structure/MySystem.json") as f:
        data = json.load(f)

    config = ActorConfig(
        respawn_enabled=True,
        respawn_delay=2.0,
        output_dir=ensure_output_dir(),
    )

    builder, worker = populate_builder(data["data"], ecu="main_ecu", config=config)
    coord = builder.build()
    worker.start()                 # rclpy node on worker thread
    try:
        return await coord.run()   # blocks until SIGINT or all actors terminate
    finally:
        worker.stop()

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

The coordinator drives its own state-event loop, but if you want a sidecar
that taps the stream (e.g., to feed a UI), spawn a task that reads from
`coord.state_queue` — be aware you'll race the coordinator's reader, so the
recommended pattern is to wrap the coordinator instead.

---

## Comparison with `play_launch`

| Concept                   | play_launch (Rust)                                            | this runtime (Python)                                |
| ------------------------- | ------------------------------------------------------------- | ---------------------------------------------------- |
| Actor                     | `RegularNodeActor` / `ContainerActor` / `ComposableNodeActor` | same names, asyncio tasks                            |
| Coordinator               | `MemberCoordinator{Builder,Runner,Handle}`                    | `Coordinator{Builder,Handle}`                        |
| Process kill              | SIGTERM → 5s → SIGKILL on pgid                                | `process.py::graceful_kill` (identical)              |
| Composable load           | direct LoadNode service call                                  | identical, via `composition_interfaces/srv/LoadNode` |
| YAML param inlining       | `LoadNodeRecord` builder strips params_files                  | `params.flatten_for_fqn`                             |
| Input format              | `record.json`                                                 | `system_structure.json` (Autoware-native)            |
| Web UI / SSE / REST       | yes                                                           | **no** (explicitly out of scope)                     |
| Resource monitoring       | yes (CSV per-node)                                            | **no**                                               |
| LD_PRELOAD interception   | yes                                                           | **no**                                               |
| Container isolation modes | yes (`stock`/`observable`/`isolated`)                         | **no** (uses whatever container is in the JSON)      |

The two systems share the design pattern but not code. When play_launch's
parser-side behavior is needed, run that tool directly; this runtime exists
to keep autoware_system_designer's runtime layer self-contained.

---

## Caveats

- **Composable params** are flattened from YAML in the actor; rclpy is on a
  worker thread and the LoadNode service has a 30 s default timeout per call.
  Slow containers will need a higher timeout (configurable in
  `ComposableNodeActor.__init__`).
- **`ros2_launch_file` entities** are wrapped as a single `ros2 launch …`
  subprocess. You lose per-leaf-node visibility _inside_ the include — by
  design (mirrors play_launch's NodeRecord behavior).
- **Empty / placeholder nodes** (single_node entries whose `executable` is
  empty) are silently skipped during build; check the log at startup.
- **Namespace data convention**: system_structure stores `namespace` as the
  full path of the entity (including its own name as the last segment when
  the entity sits under a same-named module). The builder strips that last
  segment iff it equals `name`. See `builder.parent_namespace()`.
