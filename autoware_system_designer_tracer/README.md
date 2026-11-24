# autoware_system_designer_tracer

`LD_PRELOAD` recorder of the `rcl` C API for the runtime's live rates and latency measurement. It interposes `rcl_publish*`, `rcl_take*`, `rcl_timer_call*`, `rcl_set_ros_time_override` and the publisher, subscription and timer init functions through `dlsym(RTLD_NEXT)`, so it reaches rclcpp and rclpy processes alike without a rebuild, on Humble and Jazzy. It records; every analysis lives in `autoware_system_designer_runtime/_impl/measure/`. Linked to a replay driver it also gates callbacks and sets ROS time (see [Replay](#replay)).

Two tiers share the hooks:

- **Counters**, always on: one 64-byte slot per publisher, subscription and timer, bumped with atomic adds; the hook finds the slot through a lock-free map from the rcl handle. A few tens of nanoseconds per call, no message is copied and nothing subscribes.
- **Events**, while the control file says so: one 64-byte record per publish, take, timer fire and ROS time override, for the measurement window's analysis.

Two more counters load the nodes and follow the control file, off by default:

- **Sizes**: the first publish of each publisher and then one per second has its serialized size computed from the type's introspection (`src/size_probe.cpp`): the CDR layout is walked over the message's field lengths, so nothing is serialized or copied and a `uint8[]` or a sequence of fixed-size messages costs the same at any length (0.2 µs for a 10 MB `PointCloud2`). The size matches `rmw_serialize` of Cyclone DDS and Fast DDS byte for byte (`test/test_size_probe.cpp`). The first sample of a publisher looks up the type's introspection library. A serialized publish (`rcl_publish_serialized_message`, bridges and bag players) adds its exact size on every call, switch or not.
- **CPU time**: a take or timer fire opens a window on its thread (`CLOCK_THREAD_CPUTIME_ID`), closed by the thread's next take, timer fire, service or client take (`rcl_take_request*`, `rcl_take_response*`) or `rcl_wait`; the thread CPU time in between is added to the take's or timer's slot. Two clock reads per callback (about 0.13 µs each). A take of a subscription no executor has waited on (`rcl_wait_set_add_subscription`), such as a polling subscriber read inside a timer callback, opens no window, so the callback's time stays with the timer. An intra-process subscription's callback makes no rcl call, so its time goes to the window open on its thread: the take or timer before it in the same executor cycle, or none after `rcl_wait`.

## Files

Per traced process, in `$ASD_TRACE_DIR`, opened on the first rcl call it makes (a process that never reaches rcl leaves no file):

- `<pid>.stats`: a 64-byte header followed by fixed 64-byte counter slots in a `MAP_SHARED` mmap. A slot holds the count, the wall time of the latest call and a latency sum and count: for a take, take time minus `message_info.source_timestamp` (communication); for a publish, publish entry minus the thread's latest take or timer fire (callback time). A take or timer slot also holds the slot of the latest publish it led to on its thread, which places a timer under the node that publishes from it, and its callbacks' CPU time; a publisher slot holds the measured bytes and the publishes they were measured on. The slot of a finalized endpoint serves the next one created, with its counters as they stand and the bytes and CPU time back at zero.
- `<pid>.names`: tab-separated lines joining handles and counter slots to node names, topics, publisher gids and timer periods. A line ends with the counters its slot held when the endpoint was created, the base of that endpoint's own counts.
- `<pid>.trace`, created on the first event: a 64-byte header followed by fixed 64-byte records, written into a `MAP_SHARED` mmap with an atomic index and no locks, so the file survives `SIGKILL`. The records are a ring: past the capacity the oldest record is overwritten, so the file holds the latest ones and never grows. Times are `CLOCK_REALTIME` nanoseconds and compare with DDS source timestamps. A take records the thread, the subscription handle, `message_info.source_timestamp`, the publisher gid and `from_intra_process`; a timer fire the thread and timer handle; a publish the thread, the publisher handle and the entry and exit time of the call (the DDS source timestamp lies in between); a clock record the wall time at which a ROS time override was set and the ROS time it was set to, one per distinct value plus the last sighting of a value held longer than twice the gap before it (a paused `/clock` keeps publishing its frozen value), so a process on `/clock` (`use_sim_time`) leaves the wall→ROS mapping, plateaus included, in its trace.

In the directory itself, `control` (written by the runtime, mapped read-only by every traced process) switches event recording, size sampling and CPU time; without it events are always recorded and the other two are off.

The layout is `include/autoware_system_designer_tracer/trace_format.h`; the Python readers mirror it field for field.

## Environment

| Variable                     | Role                                                                                                     |
| ---------------------------- | -------------------------------------------------------------------------------------------------------- |
| `ASD_TRACE_DIR`              | arms the tracer; the directory the files go to                                                           |
| `ASD_TRACE_CAPACITY`         | event records per process file, a ring of the latest ones (default 4194304, at most 16777216)            |
| `ASD_STATS_SLOTS`            | counter slots per process (default 1024, at most 65536); live endpoints past it are counted, not tracked |
| `ASD_REPLAY_SOCKET`          | arms the replay gate; the Unix socket of the replay driver                                               |
| `ASD_REPLAY_START_NS`        | ROS time every clock holds until the driver's first clock step                                           |
| `ASD_REPLAY_WALL_TIMERS`     | `free` leaves timers of a steady or system clock on that clock, ungated                                  |
| `ASD_REPLAY_MEMBER`          | name of the member that started the process, sent in its `hello`                                         |
| `ASD_REPLAY_GATE_HOLD_NS`    | longest stay of an executor in `rcl_wait` with ready work and no grant (default 2000000)                 |
| `ASD_REPLAY_TAKE_TIMEOUT_NS` | time a take waits for a message in flight (default 2000000000)                                           |
| `ASD_REPLAY_GRANTS`          | threads of the process that hold a grant at once (default 4, at most 64)                                 |

The runtime sets `LD_PRELOAD` and `ASD_TRACE_DIR` on every process it spawns and locates the library through the ament index or `ASD_TRACER_LIB`.

## Replay

A process started with `ASD_REPLAY_SOCKET` connects to the replay driver of `autoware-system-designer-replay` on its first rcl call and is gated from then on (`src/replay_link.c` holds the link and lists its lines at its top, `replay_table.c` the endpoints, `replay_clock.c` the clocks, `replay_gate.c` the gate):

- `rcl_wait` reports the subscriptions and timers that have work, hides them from the executor and returns the one the driver granted. The grant ends when `rclcpp::Executor::execute_any_executable` returns, else at the thread's next `rcl_wait`.
- Publishes, takes and timer calls are reported with whether their thread holds the grant; a take that finds nothing while a published message is in flight waits for it.
- ROS time is the driver's: every ROS clock has its override enabled from `rcl_clock_init` on and set by the link's reader thread, `rcl_set_ros_time_override` from elsewhere and `rcl_disable_ros_time_override` do nothing, and `rcl_timer_init` puts a timer of a steady or system clock on a driver-timed clock of the tracer. Each timer's next call time is reported after every call, cancel, reset and clock step.
- `rclcpp::NodeOptions::use_intra_process_comms()` returns false: an intra-process delivery makes no rcl call. An endpoint whose own options enable it is not covered.
- The two rclcpp functions are hooked by their mangled names. The `hello` of a process names the hooks whose function did not resolve in its rclcpp (`intra_process`, `execute`), `ok` when both did, `-` for a process without rclcpp.
- Quality of service policies of an endpoint are sent by the names of `rmw/qos_string_conversions.h`.

Without the socket these hooks only forward. When the driver closes the link or sends `free`, the process runs ungated on the clocks as they stand.

## A new rcl entry point

The hooks are listed per ROS distribution at the top of `src/tracer.c`. A new entry point gets a forward type, a hook that forwards through `FORWARD` and calls the matching `observe_*`, and a row in that list; an entry point that rcl itself reaches from a hooked one only forwards while the outer hook runs (see `tls_in_timer_hook`).

## Manual use

```bash
ASD_TRACE_DIR=/tmp/trace LD_PRELOAD=$(ros2 pkg prefix autoware_system_designer_tracer)/lib/libautoware_system_designer_tracer.so \
    ros2 run demo_nodes_cpp talker
```
