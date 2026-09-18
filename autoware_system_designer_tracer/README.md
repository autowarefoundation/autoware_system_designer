# autoware_system_designer_tracer

`LD_PRELOAD` recorder of the `rcl` C API for the runtime's latency measurement. It interposes `rcl_publish*`, `rcl_take*`, `rcl_timer_call`, `rcl_set_ros_time_override` and the publisher, subscription and timer init functions through `dlsym(RTLD_NEXT)`, so it reaches rclcpp and rclpy processes alike without a rebuild. It records; every analysis lives in `autoware_system_designer_runtime/_impl/measure/`.

## Files

Per traced process, in `$ASD_TRACE_DIR`:

- `<pid>.trace`: a 64-byte header followed by fixed 64-byte records, appended into a `MAP_SHARED` mmap with an atomic index and no locks, so the file survives `SIGKILL`. Times are `CLOCK_REALTIME` nanoseconds and compare with DDS source timestamps. A take records the thread, the subscription handle, `message_info.source_timestamp`, the publisher gid and `from_intra_process`; a timer fire the thread and timer handle; a publish the thread, the publisher handle and the entry and exit time of the call (the DDS source timestamp lies in between); a clock record the wall time at which a ROS time override was set and the ROS time it was set to, one per distinct value, so a process on `/clock` (`use_sim_time`) leaves the wall→ROS mapping in its trace.
- `<pid>.names`: tab-separated lines joining handles to node names, topics, publisher gids and timer periods.

The layout is `include/autoware_system_designer_tracer/trace_format.h`; the Python reader mirrors it field for field.

## Environment

| Variable             | Role                                                                              |
| -------------------- | --------------------------------------------------------------------------------- |
| `ASD_TRACE_DIR`      | arms tracing; the directory the files go to                                       |
| `ASD_TRACE_CAPACITY` | records per process file (default 4194304); claims past it are counted as dropped |

The runtime sets both on the processes it spawns (`--measure`) and locates the library through the ament index or `ASD_TRACER_LIB`.

## Manual use

```bash
ASD_TRACE_DIR=/tmp/trace LD_PRELOAD=$(ros2 pkg prefix autoware_system_designer_tracer)/lib/libautoware_system_designer_tracer.so \
    ros2 run demo_nodes_cpp talker
```
