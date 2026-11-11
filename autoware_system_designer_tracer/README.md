# autoware_system_designer_tracer

`LD_PRELOAD` recorder of the `rcl` C API for the runtime's live rates and latency measurement. It interposes `rcl_publish*`, `rcl_take*`, `rcl_timer_call*`, `rcl_set_ros_time_override` and the publisher, subscription and timer init functions through `dlsym(RTLD_NEXT)`, so it reaches rclcpp and rclpy processes alike without a rebuild, on Humble and Jazzy. It records; every analysis lives in `autoware_system_designer_runtime/_impl/measure/`.

Two tiers share the hooks:

- **Counters**, always on: one 64-byte slot per publisher, subscription and timer, bumped with atomic adds; the hook finds the slot through a lock-free map from the rcl handle. A few tens of nanoseconds per call, no message is copied and nothing subscribes.
- **Events**, while the control file says so: one 64-byte record per publish, take, timer fire and ROS time override, for the measurement window's analysis.

## Files

Per traced process, in `$ASD_TRACE_DIR`, opened on the first rcl call it makes (a process that never reaches rcl leaves no file):

- `<pid>.stats`: a 64-byte header followed by fixed 64-byte counter slots in a `MAP_SHARED` mmap. A slot holds the count, the wall time of the latest call and a latency sum and count: for a take, take time minus `message_info.source_timestamp` (communication); for a publish, publish entry minus the thread's latest take or timer fire (callback time). A take or timer slot also holds the slot of the latest publish it led to on its thread, which places a timer under the node that publishes from it. The slot of a finalized endpoint serves the next one created, with its counters as they stand.
- `<pid>.names`: tab-separated lines joining handles and counter slots to node names, topics, publisher gids and timer periods. A line ends with the counters its slot held when the endpoint was created, the base of that endpoint's own counts.
- `<pid>.trace`, created on the first event: a 64-byte header followed by fixed 64-byte records, appended into a `MAP_SHARED` mmap with an atomic index and no locks, so the file survives `SIGKILL`. Times are `CLOCK_REALTIME` nanoseconds and compare with DDS source timestamps. A take records the thread, the subscription handle, `message_info.source_timestamp`, the publisher gid and `from_intra_process`; a timer fire the thread and timer handle; a publish the thread, the publisher handle and the entry and exit time of the call (the DDS source timestamp lies in between); a clock record the wall time at which a ROS time override was set and the ROS time it was set to, one per distinct value plus the last sighting of a value held longer than twice the gap before it (a paused `/clock` keeps publishing its frozen value), so a process on `/clock` (`use_sim_time`) leaves the wall→ROS mapping, plateaus included, in its trace.

In the directory itself, `control` (written by the runtime, mapped read-only by every traced process) switches event recording; without it events are always recorded.

The layout is `include/autoware_system_designer_tracer/trace_format.h`; the Python readers mirror it field for field.

## Environment

| Variable             | Role                                                                                                      |
| -------------------- | --------------------------------------------------------------------------------------------------------- |
| `ASD_TRACE_DIR`      | arms the tracer; the directory the files go to                                                            |
| `ASD_TRACE_CAPACITY` | event records per process file (default 4194304, at most 67108864); claims past it are counted as dropped |
| `ASD_STATS_SLOTS`    | counter slots per process (default 1024, at most 65536); live endpoints past it are counted, not tracked  |

The runtime sets `LD_PRELOAD` and `ASD_TRACE_DIR` on every process it spawns and locates the library through the ament index or `ASD_TRACER_LIB`.

## A new rcl entry point

The hooks are listed per ROS distribution at the top of `src/tracer.c`. A new entry point gets a forward type, a hook that forwards through `FORWARD` and calls the matching `observe_*`, and a row in that list; an entry point that rcl itself reaches from a hooked one only forwards while the outer hook runs (see `tls_in_timer_hook`).

## Manual use

```bash
ASD_TRACE_DIR=/tmp/trace LD_PRELOAD=$(ros2 pkg prefix autoware_system_designer_tracer)/lib/libautoware_system_designer_tracer.so \
    ros2 run demo_nodes_cpp talker
```
