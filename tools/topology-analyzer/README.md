# topology-analyzer

Two-script tool for capturing and comparing ROS 2 system graphs.
Useful for verifying that a refactored launch configuration matches a reference system, or for auditing what changed between two software versions.

## Scripts

| Script                    | Purpose                                                                            |
| ------------------------- | ---------------------------------------------------------------------------------- |
| `ros2_graph_snapshot.py`  | Connects to a live ROS 2 system and snapshots its node graph to JSON               |
| `ros2_topology_report.py` | Generates a human-readable report from one snapshot, or a structured diff from two |

Both scripts are standalone Python files with no build step required.

---

## Workflow

### 1. Capture snapshots

Run this while each system is live. Each call produces a `graph.json`.

```bash
# While system A is running:
python3 ros2_graph_snapshot.py --out /tmp/snap_a/graph.json --spin-seconds 3.0

# While system B is running:
python3 ros2_graph_snapshot.py --out /tmp/snap_b/graph.json --spin-seconds 3.0
```

The script connects to the ROS 2 graph, waits `--spin-seconds` for node discovery to settle, then records every node's publishers, subscribers, services, clients, and (optionally) parameter names and values.

### 2. Single-system report

```bash
python3 ros2_topology_report.py /tmp/snap_a/graph.json --out /tmp/topology_a.md
```

Groups nodes by their pub/sub/service/client signature and lists all topics with types.
Common infrastructure topics (`/rosout`, `/clock`, `/parameter_events`) are hidden by default to reduce noise.
If the snapshot has `component_info` data (taken with an updated snapshot script), a **Composable Node Containers** section lists every `ComponentManager` container and its composable nodes.

### 3. Diff two systems

```bash
python3 ros2_topology_report.py /tmp/snap_a/graph.json /tmp/snap_b/graph.json --out /tmp/diff.md
```

Matches nodes between the two snapshots — name-agnostic, so nodes that were renamed or moved to a different namespace are still paired by topology similarity — then reports:

- **Namespace Summary** — per-namespace counts of added/removed/changed nodes
- **Container Changes** — (when component data present) containers added/removed, and which composable nodes joined or left each container
- **Matching summary** — all matched node pairs with similarity scores
- **Added nodes** — nodes in the new system with no match in the old
- **Removed nodes** — nodes in the old system with no match in the new
- **Changed nodes** — matched nodes whose endpoints or parameters differ, tagged by change type
- **Edge-level changes** — added/removed/renamed pub→sub connections on topics

---

## Snapshot options (`ros2_graph_snapshot.py`)

| Flag                           | Default                                         | Description                                                                                                                                                   |
| ------------------------------ | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `--out PATH`                   | `./ros2_graph_snapshots/<timestamp>/graph.json` | Output path                                                                                                                                                   |
| `--spin-seconds N`             | `1.0`                                           | Seconds to wait for node discovery before snapshotting. Use 3–5 s for large or slow-starting systems                                                          |
| `--params {none,names,values}` | `values`                                        | Parameter collection mode. `values` fetches current values (slowest, most informative). `names` lists names only. `none` disables parameter queries (fastest) |
| `--filter REGEX`               | —                                               | Include only nodes whose fully-qualified name matches this regex, e.g. `'^/planning/'`                                                                        |
| `--max-nodes N`                | `0` (unlimited)                                 | Cap the number of nodes processed                                                                                                                             |
| `--sleep-per-node N`           | `0.0`                                           | Optional sleep between nodes to reduce CPU/network spikes on large graphs                                                                                     |
| `--include-hidden`             | off                                             | Include hidden node names (those with `/_` in the path)                                                                                                       |

### Snapshot JSON format

```json
{
  "timestamp": "2026-04-16T01:35:20Z",
  "node_count": 208,
  "duplicates": [],
  "errors": {},
  "param_names":  { "/some/node": ["param_a", "param_b"] },
  "param_values": { "/some/node": { "param_a": "1.0" } },
  "nodes": [
    {
      "fq_name": "/sensing/lidar/top/ring_outlier_filter",
      "publishers":  { "/sensing/lidar/top/pointcloud":                          ["sensor_msgs/msg/PointCloud2"] },
      "subscribers": { "/sensing/lidar/top/distortion_corrector_node/pointcloud": ["sensor_msgs/msg/PointCloud2"] },
      "services": { "...": ["..."] },
      "clients": {},
      "component_info": { "container": "/pointcloud_container", "component_id": 3 }
    },
    {
      "fq_name": "/localization/pose_estimator/ndt_scan_matcher",
      "publishers":  { "...": ["..."] },
      "subscribers": { "...": ["..."] },
      "services": {},
      "clients": {},
      "component_info": null
    }
  ]
}
```

`component_info` is `null` for standalone nodes (launched as separate processes).
It is populated for composable nodes loaded into a `ComponentManager` container.
If `composition_interfaces` is not available on the system, all entries will be `null`.

---

## Report options (`ros2_topology_report.py`)

### Common flags

| Flag                           | Default                                               | Description                                                                                                  |
| ------------------------------ | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `--out PATH`                   | alongside input as `topology.md` / `topology_diff.md` | Output path                                                                                                  |
| `--include-transform-listener` | off                                                   | Include `transform_listener*` nodes (normally very numerous and not meaningful for topology comparison)      |
| `--include-tool-nodes`         | off                                                   | Include `/graph_snapshot` and `/launch_ros_*` nodes (the snapshot tool itself and transient launch managers) |
| `--include-parameter-events`   | off                                                   | Include `/parameter_events` topic in matching and diffs                                                      |

### Single-report flags

| Flag                      | Default | Description                                                           |
| ------------------------- | ------- | --------------------------------------------------------------------- |
| `--max-groups N`          | `50`    | Max signature groups to print in detail (`0` = no limit)              |
| `--max-nodes-per-group N` | `10`    | Max node names shown per group                                        |
| `--include-common-topics` | off     | Show `/rosout`, `/clock`, `/parameter_events` in group topic listings |
| `--topic-focus REGEX`     | —       | Filter topics shown in the topic index section                        |

### Diff flags

| Flag                    | Default | Description                                                               |
| ----------------------- | ------- | ------------------------------------------------------------------------- |
| `--min-similarity N`    | `0.70`  | Minimum Jaccard similarity for fuzzy node matching                        |
| `--min-margin N`        | `0.10`  | Minimum gap to the second-best candidate required to accept a fuzzy match |
| `--max-match-summary N` | `100`   | Max matched pairs shown in the matching summary (`0` = no limit)          |
| `--max-changed-nodes N` | `200`   | Max changed node entries shown (`0` = no limit)                           |

---

## Diff output reference

### Container Changes

Only present when at least one snapshot contains `component_info` data (i.e., the snapshot was taken with a build that has `composition_interfaces` available).

```text
## Container Changes

- Standalone nodes: 12 -> 10
- Containers: 4 -> 5

### Added containers
- /sensing/lidar/new_container (3 nodes): /sensing/lidar/top/node_a, /sensing/lidar/top/node_b, ...

### Removed containers
- /old/lidar_container (2 nodes): /sensing/lidar/left/old_node_x, /sensing/lidar/left/old_node_y

### Changed containers (membership differs)
#### /pointcloud_container (+2 joined, -1 left)

- joined: /sensing/lidar/new_node  [new node]
- joined: /sensing/lidar/moved_node  [from /old/lidar_container]
- left:   /sensing/lidar/split_node  [now in /sensing/lidar/new_container]
```

### Namespace Summary

Quick overview of which namespaces have activity:

```text
- /perception/object_recognition:      +3 added, ~10 changed
- /perception/traffic_light_recognition: -22 removed
- /sensing/lidar:                      +6 added, -16 removed, ~9 changed
```

### Changed node tags

Each changed node entry is tagged with the kinds of differences found:

| Tag             | Meaning                                                                                     |
| --------------- | ------------------------------------------------------------------------------------------- |
| `[structural]`  | Publishers, subscribers, service servers, or clients were added or removed                  |
| `[remapped]`    | The node or its topics were renamed/moved to a different namespace; message types unchanged |
| `[param-name]`  | Parameter names were added or removed                                                       |
| `[param-value]` | Parameter values changed                                                                    |
| `[container]`   | The composable container the node runs in changed, or the node moved between standalone/composable |

Example:

```text
### /sensing/lidar/top/distortion_corrector_node -> /sensing/lidar/top/distortion_corrector_node [structural, remapped, param-name, param-value]
- subscribers:
  - removed: /sensing/lidar/top/mirror_cropped/pointcloud_ex
  - added:   /sensing/lidar/top/crop_box_filter_wheels/output
- parameter values:
  - changed: output_frame :: velodyne_top -> base_link

### /some/node -> /some/node [container]
- component:
  - container: /old_container -> /new_container
```

For composable-to-standalone transitions:

```text
### /some/node -> /some/node [container]
- component:
  - standalone -> composable in /pointcloud_container (id=3)
```

### Edge-level changes

Edges are pub→sub connections through a topic. Three categories:

- **Renamed edges** (`~`) — same publisher and subscriber nodes, only the topic name changed (a topic rename, not a topology change)
- **Added edges** (`+`) — new connections in the new system
- **Removed edges** (`-`) — connections present in the old system but not the new

```text
~ /perception/obstacle_segmentation/common_ground_filter
    -> /perception/occupancy_grid_map/occupancy_grid_map_node
    : /perception/obstacle_segmentation/single_frame/pointcloud
   -> /perception/obstacle_segmentation/pointcloud

+ /sensing/lidar/left/lidar -> /sensing/lidar/left/crop_box_filter_self : /sensing/lidar/left/lidar/velodyne_points
- /sensing/lidar/left/velodyne_ros_wrapper_node -> /sensing/lidar/left/crop_box_filter_self : /sensing/lidar/left/pointcloud_raw_ex
```

---

## Limitations

**Plugin class is not available at runtime.**
The C++ plugin class name (e.g., `autoware::euclidean_cluster::VoxelGridBasedEuclideanClusterNode`) is a launch-time property that the ROS 2 graph API does not expose.
The snapshot records which container a composable node runs in (via `composition_interfaces/srv/ListNodes`), but not the plugin class that was loaded.
If a node's plugin is swapped for one with an identical pub/sub interface, the diff will show no change.
To detect plugin-level changes, cross-reference the snapshot with the launch files or `.node.yaml` design files.

---

## Node matching algorithm (diff mode)

Nodes are matched across the two snapshots in four passes, in priority order:

1. **Same fully-qualified name** — exact name match; fast path
2. **Exact signature** — same pub/sub/service/client topic set and types (catches simple node renames)
3. **Normalized signature** — same basename+type set, ignoring namespace prefix (catches namespace moves)
4. **Fuzzy similarity** — weighted blend: message-type Jaccard (50%), endpoint Jaccard (30%), name similarity (15%), parameter name overlap (5%); requires mutual best match and a minimum margin over the second-best candidate

Nodes matched in pass 1 are not re-examined. Topology changes in shared nodes are always reported.

Standard ROS 2 parameter management services (`describe_parameters`, `get_parameters`, etc.) are suppressed from the services diff when the node itself was renamed, since those renames are purely derivative.

---

## Typical use cases

**Verify a refactor did not change topology:**

```bash
python3 ros2_graph_snapshot.py --out /tmp/before/graph.json
# ... apply refactor and restart ...
python3 ros2_graph_snapshot.py --out /tmp/after/graph.json
python3 ros2_topology_report.py /tmp/before/graph.json /tmp/after/graph.json
# Expect: 0 structural changes, only [remapped] changes if any topics were renamed
```

**Snapshot a single subsystem:**

```bash
python3 ros2_graph_snapshot.py \
    --filter '^/perception/' \
    --params none \
    --spin-seconds 2.0 \
    --out /tmp/perception.json
python3 ros2_topology_report.py /tmp/perception.json
```

**Diff without parameter noise (faster snapshot, less output):**

```bash
python3 ros2_graph_snapshot.py --params none --out /tmp/snap_a.json
python3 ros2_graph_snapshot.py --params none --out /tmp/snap_b.json
python3 ros2_topology_report.py /tmp/snap_a.json /tmp/snap_b.json
```
