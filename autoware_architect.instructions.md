# Rules of the Autoware Architect
This is rule description of the AutoWare Architect (AWArch).


## Role of the Autoware Architect
The Autoware Architect builds a system by given tree structure of element configurations. By describing software architecture elements, Autoware systems could be defined and built in modular manner. User can get system products such as launchers, diagrams, and analysis results.


## Configuration entities
### Node
'Node' is actual software element. leaf of the system structure.
```yaml
name: DetectorA.node

launch:
  package: autoware_perception_dummy_nodes
  plugin: autoware::perception_dummy_nodes::DetectorA
  node_output: screen
  use_container: true
  container_name: pointcloud_container

# interfaces
inputs:
  - name: pointcloud
    message_type: sensor_msgs/msg/PointCloud2

outputs:
  - name: objects
    message_type: autoware_perception_msgs/msg/DetectedObjects
    qos:
      reliability: reliable
      durability: transient_local

# parameters
parameter_files:
  - name: model_param_path
    default: path/to/model_param.yaml
    schema: path/to/model_param.schema.json
    allow_substs: true
  - name: ml_package_param_path
    default: path/to/ml_package_param.yaml
    schema: path/to/ml_package_param.schema.json
    allow_substs: true
  - name: class_remapper_param_path
    default: path/to/class_remapper_param.yaml

parameters:
  - name: build_only
    type: bool
    default: false
    description: If true, the node will only build the model and exit.

# processes
processes:
  - name: detector
    trigger_conditions:
      - or:
          - on_input: pointcloud
    outcomes:
      - to_output: objects

```
### Module
'module' is set of nodes and (sub-)modules. branch of the system structure. leafs and sub branches are called 'instance'
```yaml
name: DetectorA.module

instances:
  - instance: node_detector
    entity: DetectorA.node
  - instance: node_filter
    entity: FilterA.node

external_interfaces:
  input:
    - name: pointcloud
    - name: vector_map
  output:
    - name: objects
  parameter:
    - name: detector
    - name: filter

connections:
  - from: input.pointcloud
    to: node_detector.input.pointcloud
  - from: node_detector.output.objects
    to: node_filter.input.objects
  - from: input.vector_map
    to: node_filter.input.vector_map
  - from: node_filter.output.*
    to: output.*

```

### System
'System' is set of nodes and modules. root of the system structure. the branches are called 'component'.
```yaml
name: TypeAlpha.system

modes:
  - name: default
    description: Default mode
  - name: simulation
    description: Simulation mode

components:
  - component: sensor_module_a
    entity: LidarDummy.module
    namespace: sensing
    compute_unit: perception_ecu_1
  - component: map_loader
    entity: MapDummy.node
    namespace: map
    compute_unit: dummy_ecu_1
  - component: localizer
    entity: LocalizationDummy.node
    namespace: localization
    compute_unit: dummy_ecu_1
  - component: object_recognition
    entity: PerceptionA.module
    namespace: perception
    compute_unit: main_ecu
    parameter_set: [
      PerceptionModuleA.parameter_set
    ]
    mode: [default]
  - component: object_recognition
    entity: PerceptionB.module
    namespace: perception
    compute_unit: main_ecu
    parameter_set: [
      PerceptionModuleA.parameter_set,  
      PerceptionModuleABuild.parameter_set,
    ]
    mode: simulation
  - component: planner
    entity: PlanningDummy.node
    namespace: planning
    compute_unit: dummy_ecu_2

connections:
  - from: sensor_module_a.output.concatenated/pointcloud
    to: object_recognition.input.pointcloud
  - from: sensor_module_a.output.concatenated/pointcloud
    to: localizer.input.pointcloud
  - from: map_loader.output.vector_map
    to: localizer.input.lanelet_map
  - from: map_loader.output.vector_map
    to: object_recognition.input.vector_map
  - from: map_loader.output.vector_map
    to: planner.input.lanelet_map
  - from: object_recognition.output.objects
    to: planner.input.predicted_objects

```

### Parameter set
'parameter set' is set of parameters per nodes. the parameter set is configured in the system's components.
```yaml
name: PerceptionModuleA.parameter_set
parameters:
  - node: /perception/object_recognition/detector_a1/node_detector
    parameter_files:
      - model_param_path: perception/object_recognition/detector_a1/node_detector/model_param_path.param.yaml
      - ml_package_param_path: perception/object_recognition/detector_a1/node_detector/ml_package_param_path.param.yaml
      - class_remapper_param_path: perception/object_recognition/detector_a1/node_detector/class_remapper_param_path.param.yaml
    parameters:
      - name: build_only
        type: bool
        value: false
  - node: /perception/object_recognition/detector_a1/node_filter
    parameter_files:
      - filtering_range_param: perception/object_recognition/detector_a1/node_filter/filtering_range_param.param.yaml
    parameters: []
  - node: /perception/object_recognition/detector_a2/node_detector
    parameter_files:
      - model_param_path: perception/object_recognition/detector_a2/node_detector/model_param_path.param.yaml
      - ml_package_param_path: perception/object_recognition/detector_a2/node_detector/ml_package_param_path.param.yaml
      - class_remapper_param_path: perception/object_recognition/detector_a2/node_detector/class_remapper_param_path.param.yaml
    parameters:
      - name: build_only
        type: bool
        value: false
  - node: /perception/object_recognition/detector_a2/node_filter
    parameter_files:
      - filtering_range_param: perception/object_recognition/detector_a2/node_filter/filtering_range_param.param.yaml
    parameters: []
  - node: /perception/object_recognition/node_tracker
    parameter_files:
      - tracker_setting_path: perception/object_recognition/node_tracker/tracker_setting_path.param.yaml
      - data_association_matrix_path: perception/object_recognition/node_tracker/data_association_matrix_path.param.yaml
      - input_channels_path: perception/object_recognition/node_tracker/input_channels_path.param.yaml
    parameters: []
```

## Parameters
### Parameter
variable for node.

### Parameter_file
set of variables for node.


## Structure of entities
Entities are defined by following types.
* system contains modules and nodes
* module contains modules and nodes
* node is independent

### Namespace
Namespace is address of nodes and topics. It is defined by its hierarchy.


## Instance
### deployment
instance of system. it contains deploying vehicle parameters such as vehicle id to specify calibration parameter, environment such as map.

### instance
instance is elements consisting 'module. 

### component
component is elements consisting 'system'. special version of 'instance'

### connection
connection connect ports for messaging system

### topic
address of message. resolved and defined by the system build process

### process
callback in a node. indicating chain of events.


## Abstractions
### Port
gate of interfaces. node and module can have. 

### Link
connection between ports. configured by 'connection'

### Event
instance of process.


## Constrains
### Topic type
When out-port and in-port are connected, both should have same message type.

### Connection
External port is interface of module only accessible from outside.

### Configuration name
Configuration name and its file name should have same name.
The type of configuration is indicated at the end of the name, `'name'.'type'`

