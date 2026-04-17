# auto-system-config-generator

Parses a flattened ROS 2 launch XML (output of [launch_unifier](https://github.com/xmfcx/launch_unifier_ws)) and generates skeleton **Autoware System Designer** YAML configs:

- `<SystemName>.system.yaml` — top-level system with components, node_groups, and connections
- `<EntityName>.module.yaml` — one per top-level namespace group

## Prerequisites

```bash
pip install lxml pyyaml
```

## Workflow

### Step 1 — Flatten the launch tree with launch_unifier

```bash
cd launch_unifier_ws
source install/setup.bash
source ~/workspace/awf/autoware/install/setup.bash

ros2 run launch_unifier launch_unifier --ros-args \
  -p launch_command:="ros2 launch autoware_launch logging_simulator.launch.xml \
    vehicle_model:=\$VEHICLE_MODEL \
    sensor_model:=\$SENSOR_MODEL \
    vehicle_id:=\$VEHICLE_ID \
    map_path:=\$MAP_PATH \
    pointcloud_map_file:=\$MAP_PCD_FILE \
    map:=true vehicle:=true system:=true rviz:=false \
    planning:=true control:=true localization:=true \
    sensing:=true perception:=true \
    perception_mode:=\$PERCEPTION_MODE"
# Produces: output/generated.launch.xml
```

### Step 2 — Generate system designer YAMLs

```bash
cd src/core/autoware_system_designer/tools/auto-system-config-generator

python3 generate_system_config.py \
  --launch-xml /path/to/output/generated.launch.xml \
  --system-name MySystem \
  --output-dir generated/ \
  --verbose
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--launch-xml` | (required) | Path to `generated.launch.xml` from launch_unifier |
| `--system-name` | `GeneratedSystem` | System name (file prefix and YAML `name` field) |
| `--output-dir` | `generated/` | Output directory |
| `--compute-unit` | `main_ecu` | Compute unit label assigned to all components |
| `--group-depth` | `1` | Namespace depth for component grouping (1 = top-level) |
| `--component-map` | `config/component_map.yaml` | Namespace → name/entity override YAML |
| `--no-modules` | off | Skip per-component module YAML files |
| `--verbose` | off | Print grouping and connection statistics |

## How it works

1. **Parse** — `launch_parser.py` reads `generated.launch.xml` using `lxml` in recovery mode (handles XML with embedded JSON strings). Extracts `<node>`, `<composable_node>`, and `<node_container>` elements along with their `<remap>` and `<param>` children.

2. **Group** — `grouper.py` assigns each node to a `ComponentGroup` by the first N segments of its ROS namespace (default N=1). Names and entity labels come from `config/component_map.yaml` when available.

3. **Resolve connections** — `connection_resolver.py` examines every `<remap>` entry:
   - `~/input/xxx` → subscriber port named `xxx`
   - `~/output/xxx` → publisher port named `xxx`
   - Bare `input` / `output` → port name derived from the resolved topic
   - Infrastructure topics (`/rosout`, `/tf`, `/diagnostics*`, etc.) are filtered out.
   - A connection entry is generated for every topic where the publisher and subscriber belong to different groups.

4. **Emit** — `emitter.py` serializes groups and connections into `autoware_system_design_format: 0.3.1` YAML.

## Customizing component names

Edit `config/component_map.yaml` to override the auto-generated name/entity for any namespace:

```yaml
/sensing:
  name: sensing
  entity_name: SampleSensorKit

/perception:
  name: perception
  entity_name: Perception
```

## Limitations

- **Connections only cover `~/input/` and `~/output/` remaps.** Nodes using bare topic names without these remaps will not contribute to the connection list.
- **Module connections are skeletal.** Internal node-to-node wiring within a module may be incomplete where remaps are absent.
- **Entity names are inferred** from the C++ plugin class or executable name; they may not match existing entity definitions in the design library.
- **xacro-embedded XML** in param values may be silently skipped by lxml recovery; this does not affect topology extraction.
