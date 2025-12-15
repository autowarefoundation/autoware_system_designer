# Autoware Architect Instruction Manifest

## 1. Role & Objective
You are an AI coding assistant tasked with creating and managing Autoware Architect (AWArch) system configurations. Your goal is to generate valid, modular, and consistent YAML configuration files that define the software architecture of an Autoware system.

## 2. File System Organization
Follow this directory structure for consistency (not mandatory).
- **Root**: `src/<package_name>/architecture/`
- **Nodes**: `src/<package_name>/architecture/node/` (suffix: `.node.yaml`)
- **Modules**: `src/<package_name>/architecture/module/` (suffix: `.module.yaml`)
- **Systems**: `src/<package_name>/architecture/system/` (suffix: `.system.yaml`)
- **Parameter Sets**: `src/<package_name>/architecture/parameter_set/` (suffix: `.parameter_set.yaml`)

## 3. Configuration Entities & Schemas

### 3.1. Node Configuration (`.node.yaml`)
Represents a single ROS 2 node.
**Required Fields:**
- `name`: Must match filename (e.g., `MyNode.node`).
- `launch`: Dictionary defining execution details.
  - `package`: ROS 2 package name.
  - `plugin`: C++ class name (component) or script entry point.
  - `executable`: Name of the executable.
  - `ros2_launch_file`: (Required if `executable` is not set) Alternative setting used for normal ros2 launcher wrapper.
  - `node_output`: (Optional) `screen`, `log`, etc.
  - `use_container`: (Optional) `true`/`false`.
  - `container_name`: (Required if `use_container: true`) Name of the component container.
- `inputs`: List of input ports (subscribers).
  - `name`: Port name.
  - `message_type`: Full ROS message type (e.g., `sensor_msgs/msg/PointCloud2`).
- `outputs`: List of output ports (publishers).
  - `name`: Port name.
  - `message_type`: Full ROS message type.
  - `qos`: (Optional) QoS settings (`reliability`, `durability`, etc.).
- `parameters`: Individual default parameters.
  - `name`: Parameter name.
  - `type`: `bool`, `int`, `double`, `string`.
  - `default`: Default value.
- `parameter_files`: Reference to parameter files.
  - `name`: Identifier for the file reference.
  - `default`: Path to file (use `$(find-pkg-share pkg)/path` or relative path).
  - `schema`: (Optional) Path to JSON schema.
- `processes`: Execution logic / Event chains.
  - `name`: Name of the process/callback.
  - `trigger_conditions`: Logic to start process. Can be nested with `or`/`and`.
    - `on_input`: Triggered by input port (`on_input: port_name`).
    - `on_trigger`: Triggered by another process (`on_trigger: process_name`).
    - `periodic`: Triggered periodically (`periodic: 10.0` [Hz]).
    - `once`: Triggered once (`once: null`).
    - **Monitoring**: Optional fields `warn_rate`, `error_rate`, `timeout` can be added to trigger definitions.
  - `outcomes`: Result of process.
    - `to_output`: Sends result to output port (`to_output: port_name`).
    - `to_trigger`: Triggers another process (`to_trigger: process_name`).
    - `terminal`: Ends the chain (`terminal: null`).

### 3.2. Module Configuration (`.module.yaml`)
Represents a composite component containing nodes or other modules.
**Required Fields:**
- `name`: Must match filename (e.g., `MyModule.module`).
- `instances`: List of internal entities.
  - `instance`: Local name for the instance (e.g., `lidar_driver`).
  - `entity`: Reference to the entity definition (e.g., `LidarDriver.node`).
- `external_interfaces`: Defines the module's boundary.
  - `input`: List of externally accessible input ports.
  - `output`: List of externally accessible output ports.
  - `parameter`: List of exposed parameter namespaces.
- `connections`: Internal wiring.
  - `from`: Source port path.
  - `to`: Destination port path.

**Connection Syntax:**
- **External Input to Internal Input**: `from: input.<ext_port>` -> `to: <instance>.input.<int_port>`
- **Internal Output to Internal Input**: `from: <instance_a>.output.<port>` -> `to: <instance_b>.input.<port>`
- **Internal Output to External Output**: `from: <instance>.output.<int_port>` -> `to: output.<ext_port>`

### 3.3. System Configuration (`.system.yaml`)
Top-level entry point defining the complete system.
**Required Fields:**
- `name`: Must match filename (e.g., `MyCar.system`).
- `modes`: List of operation modes (e.g., `default`, `simulation`).
- `components`: Top-level instances.
  - `component`: Name of the component instance.
  - `entity`: Reference to module/node (e.g., `SensingModule.module`).
  - `namespace`: ROS namespace prefix.
  - `compute_unit`: Hardware resource identifier (e.g., `ecu_1`).
  - `parameter_set`: List of parameter set files to apply.
  - `mode`: (Optional) List of modes where this component is active. When it is empty, applied for all existing modes.
- `connections`: Top-level wiring between components.

### 3.4. Parameter Set Configuration (`.parameter_set.yaml`)
Overrides parameters for specific nodes within the system hierarchy.
**Fields:**
- `parameters`: List of overrides.
  - `node`: Full hierarchical path to the node instance (e.g., `/sensing/lidar/driver`).
  - `parameter_files`: Dict mapping file keys to new paths.
  - `parameters`: List of individual value overrides.

## 4. Constraints & Validation Rules
1.  **Type Safety**: Connected ports MUST have identical `message_type`.
2.  **Single Publisher**: An `input` port can have multiple sources, but an `output` port (publisher) generally drives the topic. In AWArch, one topic is published by one node/port.
3.  **Naming Convention**:
    -   Files: `PascalCase.type.yaml` (e.g., `LidarDriver.node.yaml`).
    -   Instance/Port Names: `snake_case` (e.g., `pointcloud_input`).
4.  **Path Resolution**:
    -   Use `$(find-pkg-share <package_name>)` for absolute ROS paths.
    -   Relative paths are resolved relative to the package defining them.

## 5. Examples

### Node Example
```yaml
name: Detector.node
launch:
  package: my_perception
  plugin: my_perception::Detector
  executable: detector_node
  node_output: screen
inputs:
  - name: image
    message_type: sensor_msgs/msg/Image
outputs:
  - name: objects
    message_type: autoware_perception_msgs/msg/DetectedObjects
processes:
  - name: detect
    trigger_conditions:
      - on_input: image
    outcomes:
      - to_output: objects
```

### Module Example
```yaml
name: Perception.module
instances:
  - instance: detector
    entity: Detector.node
external_interfaces:
  input:
    - name: image
  output:
    - name: objects
connections:
  - from: input.image
    to: detector.input.image
  - from: detector.output.objects
    to: output.objects
```

## 6. Build System Functions
The `autoware_architect` package provides CMake macros to automate the build and deployment process.

### `autoware_architect_build_deploy`
Builds the entire system deployment.
```cmake
autoware_architect_build_deploy(
  <project_name>
  <deployment_file>
)
```

### `autoware_architect_generate_launcher`
Generates individual node launchers from node configurations.
```cmake
autoware_architect_generate_launcher()
```

### `autoware_architect_parameter`
Generates parameter files from JSON schemas.
```cmake
autoware_architect_parameter()
```