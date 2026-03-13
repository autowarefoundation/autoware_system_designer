# Autoware System Designer Instruction Manifest

## 1. Role & Objective

This document describes how to create and manage Autoware System Designer configurations (written in autoware_system_design_format). The goal is to generate valid, modular, and consistent YAML configuration files that define the software architecture of an Autoware system.

## 2. File Format Version

All YAML files MUST start with the format version specification. The tool supports files whose _major_ version matches and whose _minor_ version is less-than-or-equal-to `DESIGN_FORMAT_VERSION`. All entity types (Nodes, Modules, Systems, Parameter Sets) must use a version up to `DESIGN_FORMAT_VERSION`.

**Note**: The supported format version is defined in `autoware_system_designer/__init__.py` as `DESIGN_FORMAT_VERSION`.

## 3. File System Organization

Follow this directory structure for consistency (not mandatory).

- **Root**: `src/<package_name>/design/`
- **Nodes**: `src/<package_name>/design/node/` (suffix: `.node.yaml`)
- **Modules**: `src/<package_name>/design/module/` (suffix: `.module.yaml`)
- **Systems**: `src/<package_name>/design/system/` (suffix: `.system.yaml`)
- **Parameter Sets**: `src/<package_name>/design/parameter_set/` (suffix: `.parameter_set.yaml`)

## 4. Configuration Entities & Schemas

### 4.1. Node Configuration (`.node.yaml`)

Represents a single ROS 2 node.
**Required Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename (e.g., `MyNode.node`).
- `package`: Dictionary defining the ROS 2 package information.
  - `name`: ROS 2 package name.
  - `provider`: Provider of the package (e.g., `dummy`, `tier4`, `autoware`).
- `launch`: Dictionary defining execution details.
  - `plugin`: C++ class name (component) or script entry point.
  - `executable`: (Optional) Name of the executable.
  - `ros2_launch_file`: (Required if `executable` and `plugin` are not set) Alternative setting used for normal ros2 launcher wrapper.
  - `node_output`: (Optional) `screen`, `log`, etc.
  - `use_container`: (Optional) `true`/`false`.
  - `container_target`: (Required if `use_container: true`) target component container.
- `subscribers`: List of input ports (subscribers).
  - `name`: Port name. Can include slashes (e.g., `perception/objects`).
  - `message_type`: Full ROS message type (e.g., `sensor_msgs/msg/PointCloud2`).
  - `remap_target`: (Optional) The internal ROS 2 topic name used by the node.
    - **Default**: If not provided, it defaults to `~/input/<name>`.
    - **Required when**: The node implementation uses a specific topic name that does not follow the `~/input/` convention (e.g., legacy code or global topics like `/tf`).
  - `global`: (Optional) If set, the input topic subscribes to a global topic name (e.g., `/tf`).
  - `qos`: (Optional) QoS settings (`reliability`, `durability`, etc.).
- `publishers`: List of output ports (publishers).
  - `name`: Port name. Can include slashes.
  - `message_type`: Full ROS message type.
  - `qos`: (Optional) QoS settings (`reliability`, `durability`, etc.).
  - `remap_target`: (Optional) The internal ROS 2 topic name used by the node.
    - **Default**: If not provided, it defaults to `~/output/<name>`.
    - **Required when**: The node implementation uses a specific topic name that does not follow the `~/output/` convention.
  - `global`: (Optional) If set, the output topic is published to a global topic name (e.g., `/tf`).
- `servers`: (Optional) List of service/action servers.
  - `name`: Server name.
  - `message_type`: Full ROS service or action type (e.g., `std_srvs/srv/SetBool` or `example_interfaces/action/Fibonacci`).
  - `global`: (Optional) If set, the server uses a global topic name.
  - `qos`: (Optional) QoS settings.
  - `remap_target`: (Optional) The internal ROS 2 service/action name used by the node.
- `clients`: (Optional) List of service/action clients.
  - `name`: Client name.
  - `message_type`: Full ROS service or action type (e.g., `std_srvs/srv/SetBool` or `example_interfaces/action/Fibonacci`).
  - `global`: (Optional) If set, the client uses a global topic name.
  - `qos`: (Optional) QoS settings.
  - `remap_target`: (Optional) The internal ROS 2 service/action name used by the node.
- `param_files`: List of parameter file references. Can be an empty list `[]`.
  - `name`: Identifier for the file reference.
  - `default`: Path to file (use `$(find-pkg-share pkg)/path` or relative path).
  - `schema`: (Optional) Path to JSON schema.
  - `allow_substs`: (Optional) `true`/`false` to allow substitution in parameter files.
- `param_values`: List of individual default parameters. Can be an empty list `[]`.
  - `name`: Parameter name.
  - `type`: Parameter type (`bool`, `int`, `double`, `string`, `array`, etc.).
  - `default`: Default value.
  - `description`: (Optional) Brief explanation of the parameter.
- `processes`: Execution logic / Event chains.
  - `name`: Name of the process/callback.
  - `trigger_conditions`: Logic to start process. Can be nested with `or`/`and`.
    - `on_input`: Triggered by input port (`on_input: port_name`).
    - `on_trigger`: Triggered by another process (`on_trigger: process_name`).
    - `periodic`: Triggered periodically (`periodic: 10.0` [Hz]).
    - `once`: Triggered once. Can be `once: null` or `once: <port_name>` to trigger once when a specific port receives data.
    - **Monitoring**: Optional fields `warn_rate`, `error_rate`, `timeout` can be added to trigger definitions.
  - `outcomes`: Result of process.
    - `to_output`: Sends result to output port (`to_output: port_name`).
    - `to_trigger`: Triggers another process (`to_trigger: process_name`).
    - `terminal`: Ends the chain (`terminal: null`).

### 4.2. Module Configuration (`.module.yaml`)

Represents a composite component containing nodes or other modules.
**Required Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename (e.g., `MyModule.module`).
- `instances`: List of internal entities.
  - `name`: Local name for the instance (e.g., `lidar_driver`).
  - `entity`: Reference to the entity definition (e.g., `LidarDriver.node`).
  - `launch`: (Optional) Override launch configurations for this instance.
- `subscribers`: List of externally accessible input ports.
  - `name`: Port name.
- `publishers`: List of externally accessible output ports.
  - `name`: Port name.
- `servers`: (Optional) List of externally accessible service/action servers.
  - `name`: Server name.
- `clients`: (Optional) List of externally accessible service/action clients.
  - `name`: Client name.
- `connections`: Internal wiring. List of connection pairs, where each connection is a list of two port paths. Supports wildcards (e.g., `subscriber.*` or `node.publisher.*`).

**Connection Syntax:**

For topic-based connections (subscribers/publishers):

- **External Subscriber to Internal Subscriber**:

  ```yaml
  - - subscriber.<external_subscriber_port>
    - <instance>.subscriber.<port>
  ```

- **Internal Publisher to Internal Subscriber**:

  ```yaml
  - - <instance_a>.publisher.<port>
    - <instance_b>.subscriber.<port>
  ```

- **Internal Publisher to External Publisher**:

  ```yaml
  - - <instance>.publisher.<port>
    - publisher.<external_publisher_port>
  ```

For service/action-based connections (servers/clients):

- **External Client to Internal Client**:

  ```yaml
  - - client.<external_client_port>
    - <instance>.client.<port>
  ```

- **Internal Server to Internal Client**:

  ```yaml
  - - <instance_a>.server.<port>
    - <instance_b>.client.<port>
  ```

- **Internal Server to External Server**:

  ```yaml
  - - <instance>.server.<port>
    - server.<external_server_port>
  ```

### 4.3. System Configuration (`.system.yaml`)

Top-level entry point defining the complete system.
**Required Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename (e.g., `MyCar.system`).
- `arguments`: (Optional) List of system arguments.
  - `name`: Argument name.
- `variables`: List of system variables.
  - `name`: Variable name.
  - `value`: Variable value (supports `$(find-pkg-share pkg)` and `$(env VAR)` substitutions).
- `variable_files`: (Optional) List of variable file references.
  - `name`: Variable file identifier.
  - `value`: Path to variable file.
- `modes`: List of operation modes.
  - `name`: Mode name (e.g., `Runtime`, `LoggingSimulation`).
  - `description`: (Optional) Description of the mode.
  - `default`: (Optional) `true`/`false` to mark as default mode.
- `parameter_sets`: List of parameter set files. Can be an empty list `[]`.
- `components`: Top-level instances.
  - `name`: Name of the component instance.
  - `entity`: Reference to module/node (e.g., `SensingModule.module`).
  - `namespace`: ROS namespace prefix.
  - `compute_unit`: Hardware resource identifier (e.g., `main_ecu`).
  - `parameter_set`: (Optional) Parameter set file name(s) to apply. Can be a string or an array of strings.
- `connections`: Top-level wiring between components. List of connection pairs, where each connection is a list of two port paths. Supports wildcards (e.g., `component.publisher.^` for wildcard).

**Mode-Specific Overrides:**
Each mode can define overrides using the mode name as a key:

- `override`: Dictionary containing mode-specific overrides. All system configuration fields can be overridden (e.g., `variables`, `variable_files`, `modes`, `parameter_sets`, `components`, `connections`). The variant resolver applies the appropriate merge strategy for each field type (key-based replacement for fields with identifiers, append for lists without keys, dictionary merge for dictionaries).
- `remove`: Dictionary specifying what to remove in this mode. All system configuration fields can be removed (e.g., `modes`, `parameter_sets`, `components`, `variables`, `connections`). The variant resolver applies the appropriate removal strategy (key-based removal for fields with identifiers, full match for lists without keys). When components are removed, connections involving them are automatically filtered out.

### 4.4. Parameter Set Configuration (`.parameter_set.yaml`)

Overrides parameters for specific nodes within the system hierarchy.
**Fields:**

- `autoware_system_design_format`: Must be a version up to the supported `DESIGN_FORMAT_VERSION`.
- `name`: Must match filename.
- `parameters`: List of overrides.
  - `node`: Full hierarchical path to the node instance (e.g., `/perception/object_recognition/detector_a1/node_detector`).
  - `param_files`: List of dictionaries mapping parameter file keys to new paths.
    - Format: `- <key>: <path>` (e.g., `- model_param_path: path/to/file.yaml`).
  - `param_values`: List of individual parameter value overrides.
    - `name`: Parameter name.
    - `type`: Parameter type (`bool`, `int`, `double`, `string`, etc.).
    - `value`: Override value (not `default`).

## 5. Base-Variant Pattern

The Autoware System Designer supports a base-variant pattern that allows you to define a base configuration and then create variants that inherit from it. This pattern is useful for creating reusable configurations, reducing duplication, and creating mode-specific configurations (e.g., Runtime vs. Simulation) or vehicle-specific variants.

### 5.1. Using the `base` Field

Instead of defining all fields directly, you can specify a `base` field that references another entity of the same type. When using `base`, the current configuration inherits all fields from the base entity. To modify the inherited configuration, you must use `override` and `remove` keys (see Sections 5.2 and 5.3 for details).

**Base Field Syntax:**

- `base`: Reference to another entity (e.g., `BaseNode.node`, `BaseModule.module`, `BaseSystem.system`).
- `override`: Dictionary containing fields to override or extend (see Section 5.2).
- `remove`: Dictionary containing fields to remove (see Section 5.3).

**When to Use Base:**

- When creating variants of an existing configuration with minor differences
- When you want to inherit the structure from a parent configuration
- When you want to reduce duplication across similar configurations

**Required Fields When Using Base:**
When using `base`, the required fields listed in Section 4.1 (Nodes), Section 4.2 (Modules), and Section 4.3 (Systems) are **not required** in the variant configuration (they are inherited from the base).

**Required Fields When NOT Using Base:**
When not using `base`, you must define all required fields directly as specified in Section 4.1 (Nodes), Section 4.2 (Modules), and Section 4.3 (Systems).

### 5.2. Override Mechanism

The `override` section merges items into the base configuration. Merge behavior depends on field type:

- **Key-based merging** (lists with identifiable keys like `name`): Items with matching keys replace existing items; new keys are appended. Examples: `variables`, `modes`, `components`, `instances`, `subscribers`, `publishers`, `servers`, `clients`, `param_values`, `processes`.
- **Append-only merging** (lists without keys): All override items are appended. Examples: `connections`, `variable_files`, `parameter_sets`.
- **Dictionary merging**: Fields are merged recursively (e.g., `launch` configuration in nodes).

### 5.3. Remove Mechanism

The `remove` section removes specific items from the base configuration:

- **Key-based removal**: Items are removed where `item[key_field]` matches `spec[key_field]`. For components/instances, connections involving removed entities are automatically filtered out.
- **Full match removal** (lists without keys): Items are removed if they match all properties in the spec (e.g., `connections` require both source and destination ports to match).

### 5.4. Order of Operations

Removals are applied **before** overrides to ensure removed items don't interfere with new additions.

## 6. Constraints & Validation Rules

1. **Type Safety**: Connected ports MUST have identical `message_type`.
2. **Single Publisher**: A subscriber port can have multiple sources, but a publisher port generally drives the topic. In the autoware system designer, one topic is published by one node/port.
3. **Naming Convention**:
   - Files: `PascalCase.type.yaml` (e.g., `LidarDriver.node.yaml`).
   - Instance/Port Names: `snake_case` (e.g., `pointcloud_input`).
4. **Path Resolution**:
   - Use `$(find-pkg-share <package_name>)` for absolute ROS paths.
   - Relative paths are resolved relative to the package defining them.
5. **Required Fields**: See Section 5.1 for details on required fields when using or not using `base`.

## 7. Examples

### Node Example (0.3.0)

```yaml
autoware_system_design_format: 0.3.0
name: Detector.node
package:
  name: my_perception
  provider: tier4
launch:
  plugin: my_perception::Detector
  executable: detector_node
  node_output: screen
subscribers:
  - name: image
    message_type: sensor_msgs/msg/Image
  - name: ros_transform
    message_type: tf2_msgs/msg/TFMessage
    global: /tf
publishers:
  - name: objects
    message_type: autoware_perception_msgs/msg/DetectedObjects
    qos:
      reliability: reliable
      durability: transient_local
param_files: []
param_values: []
processes:
  - name: detect
    trigger_conditions:
      - or:
          - on_input: image
          - once: image
    outcomes:
      - to_output: objects
```

### Module Example (0.3.0)

```yaml
autoware_system_design_format: 0.3.0
name: DetectorA.module
instances:
  - name: node_detector
    entity: DetectorA.node
  - name: node_filter
    entity: FilterA.node
subscribers:
  - name: pointcloud
  - name: vector_map
publishers:
  - name: objects
connections:
  - - subscriber.pointcloud
    - node_detector.subscriber.pointcloud
  - - node_detector.publisher.objects
    - node_filter.subscriber.objects
  - - subscriber.vector_map
    - node_filter.subscriber.vector_map
  - - node_filter.publisher.*
    - publisher.*
```

### System Example (0.3.0)

```yaml
autoware_system_design_format: 0.3.0
name: AutowareSample.system
variables:
  - name: config_path
    value: $(find-pkg-share autoware_sample_deployment)/config
  - name: vehicle_model
    value: sample_vehicle
variable_files:
  - name: vehicle_info
    value: $(find-pkg-share sample_vehicle_description)/config/vehicle_info.param.yaml
modes:
  - name: Runtime
    description: on-vehicle runtime mode
    default: true
  - name: LoggingSimulation
    description: Logged data replay simulation mode
parameter_sets: []
components:
  - name: sensing
    entity: SampleSensorKit.module
    namespace: sensing
    compute_unit: main_ecu
    parameter_set: sample_system_sensing.parameter_set
connections:
  - - localization.publisher.kinematic_state
    - sensing.subscriber.odometry
LoggingSimulation:
  override:
    components:
      - name: sensing
        entity: SampleSensorKit_sim.module
        namespace: sensing
        compute_unit: main_ecu
```

### Parameter Set Example (0.3.0)

```yaml
autoware_system_design_format: 0.3.0
name: PerceptionModuleA.parameter_set
parameters:
  - node: /perception/object_recognition/detector_a1/node_detector
    param_files:
      - model_param_path: perception/object_recognition/detector_a1/node_detector/model_param_path.param.yaml
      - ml_package_param_path: perception/object_recognition/detector_a1/node_detector/ml_package_param_path.param.yaml
    param_values:
      - name: build_only
        type: bool
        value: false
```

## 8. Build System Functions

The `autoware_system_designer` package provides CMake macros to automate the build and deployment process.

### `autoware_system_designer_build_deploy`

Builds the entire system deployment.

```cmake
autoware_system_designer_build_deploy(
  <project_name>
  <deployment_file>
)
```

### `autoware_system_designer_generate_launcher`

Generates individual node launchers from node configurations.

```cmake
autoware_system_designer_generate_launcher()
```

### `autoware_system_designer_parameter`

Generates parameter files from JSON schemas.

```cmake
autoware_system_designer_parameter()
```
