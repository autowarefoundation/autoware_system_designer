# auto-system-config-generator

Unified pipeline that turns a ROS 2 launch file into skeleton **Autoware System Designer** YAML configs:

- `<SystemName>.system.yaml` — top-level system with components, node groups, and connections
- `<EntityName>.module.yaml` — one per namespace level (generated recursively)
- `<SystemName>_<component>.parameter_set.yaml` — optional, collected param files per component

## How it works

```
┌──────────────────────────────────────────────────────────┐
│  Phase 1 — Parse launcher                                │
│  launch_unifier flattens the launch tree into a single   │
│  generated.launch.xml with all nodes/remaps resolved.    │
├──────────────────────────────────────────────────────────┤
│  Phase 2 — Load runtime structure  (optional)            │
│  topology-analyzer captures a live ROS 2 graph snapshot  │
│  (graph.json) from the running system.                   │
├──────────────────────────────────────────────────────────┤
│  Phase 3 — Combine                                       │
│  Live pub/sub topics not visible as remaps in the XML    │
│  are merged into the node records from the snapshot.     │
├──────────────────────────────────────────────────────────┤
│  Phase 4 — Generate                                      │
│  Namespace tree → connection resolution → YAML emission. │
└──────────────────────────────────────────────────────────┘
```

### Sub-tools used

| Sub-tool | Role |
|----------|------|
| `launch_unifier/` (vendored) | Evaluates launch files and serialises the resolved entity tree to flat XML |
| `topology-analyzer/` (sibling tool) | Captures live node pub/sub/service topology from a running ROS 2 system |

---

## Prerequisites

```bash
pip install lxml pyyaml
# ROS 2 environment must be sourced for launch_unifier and --live-snapshot
source /opt/ros/$ROS_DISTRO/setup.bash
source ~/workspace/awf/autoware/install/setup.bash
```

---

## Usage

### Option A — Full pipeline (recommended)

Run launch_unifier and generate configs in one command:

```bash
cd src/core/autoware_system_designer/tools/auto-system-config-generator

python3 generate_system_config.py \
  --launch-package autoware_launch \
  --launch-file    autoware.launch.xml \
  --launch-args \
    vehicle_model:=$VEHICLE_MODEL \
    sensor_model:=$SENSOR_MODEL \
    map_path:=$MAP_PATH \
    map:=true vehicle:=true system:=true \
    planning:=true control:=true localization:=true \
    sensing:=true perception:=true \
    perception_mode:=$PERCEPTION_MODE \
    rviz:=false \
  --system-name    Autoware \
  --output-dir     generated/ \
  --verbose
```

To also enrich connections with live topic data, start the system separately and add `--live-snapshot`:

```bash
# Terminal 1: start the system
ros2 launch autoware_launch autoware.launch.xml vehicle_model:=... ...

# Terminal 2: run the generator with live snapshot
python3 generate_system_config.py \
  --launch-package autoware_launch \
  --launch-file    autoware.launch.xml \
  --launch-args    vehicle_model:=$VEHICLE_MODEL sensor_model:=$SENSOR_MODEL ... \
  --live-snapshot \
  --snapshot-spin-seconds 5.0 \
  --system-name    Autoware \
  --output-dir     generated/
```

### Option B — From a pre-generated launch XML

If you already have a `generated.launch.xml` from a previous run or from the standalone `run_launch_unifier.py`:

```bash
python3 generate_system_config.py \
  --launch-xml  output/generated.launch.xml \
  --graph-json  output/graph.json \
  --system-name Autoware \
  --output-dir  generated/
```

### Option C — Launch_unifier only

To produce only the flattened XML without generating configs:

```bash
python3 run_launch_unifier.py \
  --launch-package autoware_launch \
  --launch-file    autoware.launch.xml \
  vehicle_model:=$VEHICLE_MODEL \
  sensor_model:=$SENSOR_MODEL
# Output: ./output/generated.launch.xml  ./output/entity_tree.pu
```

---

## Options reference

### Launch source (one required)

| Flag | Description |
|------|-------------|
| `--launch-xml FILE` | Pre-generated `generated.launch.xml` — skips launch_unifier |
| `--launch-package PKG` | ROS 2 package owning the launch file (use with `--launch-file`) |
| `--launch-file FILE` | Launch file name inside the package share |
| `--launch-path PATH` | Absolute path to a launch file (alternative to `--launch-package`) |
| `--launch-args key:=val ...` | Space-separated launch arguments forwarded to the launch file |
| `--unifier-output-dir DIR` | Directory for launch_unifier output (default: `./output`) |
| `--launch-debug` | Enable launch debug logging |

### Runtime snapshot (optional)

| Flag | Default | Description |
|------|---------|-------------|
| `--graph-json FILE` | — | Path to a pre-captured graph snapshot JSON |
| `--live-snapshot` | off | Capture a live snapshot from the running ROS 2 system |
| `--snapshot-spin-seconds N` | `3.0` | Discovery wait time for `--live-snapshot` |
| `--snapshot-params` | `names` | Parameter depth for `--live-snapshot`: `none`, `names`, or `values` |

### Generation options

| Flag | Default | Description |
|------|---------|-------------|
| `--system-name NAME` | `GeneratedSystem` | System name (file prefix and YAML `name` field) |
| `--output-dir DIR` | `generated/` | Output directory |
| `--compute-unit NAME` | `main_ecu` | Compute unit label for all components |
| `--system-depth N` | `1` | Namespace depth for system.yaml components. Sub-modules below this depth are generated recursively. |
| `--parameter-sets` | off | Generate `parameter_set` YAML files per top-level component |
| `--component-map FILE` | `config/component_map.yaml` | Namespace → name/entity override YAML |
| `--no-modules` | off | Skip per-component module YAML files |
| `--node-configs` | off | Generate `*.node.yaml` for node entities not already defined |
| `--package-map FILE` | auto | `_package_map.yaml` for entity discovery (auto via ament_index) |
| `--group-depth N` | — | **(Legacy)** Fixed-depth flat grouping; disables recursive tree mode |
| `--verbose` | off | Print progress and statistics |

---

## Output layout

```
<output-dir>/
├── system/
│   └── <SystemName>.system.yaml
├── module/
│   ├── <top_ns>/<Entity>.module.yaml
│   └── <top_ns>/<sub_ns>/<Entity>.module.yaml   (recursive)
├── parameter_set/                               (only with --parameter-sets)
│   └── <SystemName>_<component>.parameter_set.yaml
├── node/                                        (only with --node-configs)
│   └── <ns>/<Entity>.yaml
└── graph.json                                   (only with --live-snapshot)
```

---

## Pipeline details

### Phase 1 — Parse launcher (`launch_unifier`)

`launch_unifier` evaluates the launch file tree by running it through the ROS 2 launch system with monkey-patches that capture resolved node attributes. It writes:

- `generated.launch.xml` — flat XML with `<node>`, `<composable_node>`, and `<node_container>` elements plus resolved `<remap>` and `<param>` children
- `entity_tree.pu` — PlantUML diagram of the entity hierarchy

### Phase 2 — Load runtime structure (`topology-analyzer`)

`topology-analyzer/ros2_graph_snapshot.py` queries a live ROS 2 system for each node's publishers, subscribers, services, and clients, plus optional parameter names/values. It writes `graph.json`.

This step fills in topics that are hard-coded inside nodes (not expressed as `<remap>` entries in the launch file), giving more complete connection data.

### Phase 3 — Combine

`graph_parser.merge_graph_topics()` iterates over every `NodeRecord` from the launch XML and looks up the matching node in the snapshot by fully-qualified name. Any pub/sub topic not already covered by an explicit remap is appended as a synthetic `~/input/<port>` or `~/output/<port>` entry.

### Phase 4 — Generate

1. **Build namespace tree** — `namespace_tree.py` places every node into a recursive `NamespaceNode` hierarchy that mirrors the ROS namespace structure.
2. **Resolve connections** — `connection_resolver.py` matches `~/output/<port>` remaps against `~/input/<port>` remaps across components at each tree level.
3. **Emit YAMLs** — `emitter.py` serialises each `NamespaceNode` into a `*.module.yaml` and the top-level view into `*.system.yaml`.

---

## Customising component names

Edit `config/component_map.yaml` to override the auto-generated name/entity for any namespace:

```yaml
/sensing:
  name: sensing
  entity_name: SampleSensorKit

/perception:
  name: perception
  entity_name: Perception
```

---

## Limitations

- **Connections only cover `~/input/` and `~/output/` remaps.** Nodes using bare topic names without these remaps will not appear in connection lists unless a live snapshot is provided.
- **Module connections are skeletal.** Internal node-to-node wiring within a module may be incomplete where remaps are absent.
- **Entity names are inferred** from the C++ plugin class or executable name; they may not match existing entity definitions in the design library.
- **`--live-snapshot` requires a running system.** The generator itself does not launch or manage the ROS 2 system lifecycle.
