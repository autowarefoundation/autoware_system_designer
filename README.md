<picture>
  <source media="(prefers-color-scheme: dark)" srcset="autoware_system_designer_studio/autoware_system_designer_studio/web/img/awsd_icon_dark.svg">
  <img src="autoware_system_designer_studio/autoware_system_designer_studio/web/img/awsd_icon_light.svg" alt="AWSD" height="96">
</picture>

# Autoware System Designer

**Autoware System Designer** is a comprehensive toolset designed to define, build, and deploy Autoware systems in a modular and structured way. It allows developers to describe the software architecture using configuration files (YAML) and automatically generates system artifacts such as ROS 2 launch files and parameter templates, which the studio visualizes.

## Overview

Traditional ROS 2 launch files can become complex and hard to manage as a system grows. Autoware System Designer solves this by introducing a higher-level abstraction layer that separates **system design** from **deployment details**.

![System Architecture Example](autoware_system_designer/doc/images/node_diagram.png)

### Key Features

- **Modular Architecture**: Define systems using reusable **Nodes** and **Modules**.
- **Automatic Launch Generation**: Generate correct-by-construction ROS 2 XML launch files.
- **System Visualization**: The studio (`autoware_system_designer_studio`) draws diagrams from the exported artifacts, served live or packed for `file://`: the node graph, the logic diagram of trigger chains, and the sequence diagram, an event-chain latency view that reports the minimum, mean and maximum chain from the design's rates and a measurement file (see [doc/latency_view.md](autoware_system_designer/doc/latency_view.md)).
- **Parameter Management**: Manage parameters efficiently using **Parameter Sets** and schema validation.
- **Deployment Configuration**: Easily switch between different hardware configurations or simulation modes using **Deployments**.

## Core Concepts

Design files come in two groups. The **main formats** describe the architecture. The **sub formats** select what a build generates from it.

### Main Formats

The system is built upon four main entity types. Their files declare `autoware_system_design_format`, are validated against the schema of that version and are collected from every package into one design registry.

![Configuration Structure](autoware_system_designer/doc/images/configuration_structure.png)

#### 1. Node (`*.node.yaml`)

Represents a single ROS 2 node. It defines:

- **Package**: Package name and provider information.
- **Launch**: Node execution configuration (plugin, executable, container settings, etc.).
- **Inputs/Outputs**: Ports (topics) with message types, QoS settings, and remap targets.
- **Parameters**: Configuration variables (inline or via parameter files).
- **Processes**: Internal logic flow with trigger conditions and outcomes.

#### 2. Module (`*.module.yaml`)

A container for grouping nodes and other sub-modules. It defines:

- **Instances**: Nodes/modules contained within.
- **Connections**: Wiring between internal components.
- **External Interfaces**: Ports exposed to the outside world.

#### 3. System (`*.system.yaml`)

The top-level description of a complete autonomous driving system. It defines:

- **Variables**: System-wide configuration variables and variable files.
- **Components**: Top-level nodes/modules with compute unit assignment, namespaces, and parameter sets.
- **Node Groups**: Group nodes by listing their node paths (currently ROS 2 component containers are implemented).
- **Connections**: Data flow between components.
- **Modes**: Operation modes (e.g., Runtime, Simulation) with mode-specific overrides.

#### 4. Parameter Set (`*.parameter_set.yaml`)

A collection of parameter overrides that can be applied to specific nodes in a system, allowing for flexible configuration management without modifying the core node definitions.

A parameter file is read at build time when its path resolves to a file: system variables resolve, and `$(find-pkg-share <package>)` resolves for a package among the collected designs. A path that keeps a launch argument, including inside a package name such as `$(find-pkg-share $(var vehicle_model)_description)`, or names a package outside the collected designs, is loaded by the launcher at launch time.

### Sub Formats

A sub format file is a build input of `autoware_system_designer_build_deploy`, read only by the package that builds it. It is plain YAML checked by its parser, without a schema, and names main-format entities to build.

#### Deployment (`*.deployments.yaml`)

A deployments table instantiates one system for each listed environment or vehicle. It defines:

- **Base**: The system to deploy.
- **Deploy List**: The deployments, each with a name and the values of the system arguments.

```yaml
base: TypeAlpha.system

deploy_list:
  - name: vehicle_x
    arguments:
      - name: vehicle_id
        value: vehicle_x
```

#### Subsystem (`*.subsystem.yaml`)

A subsystem launches one instance (a module or a node) behind a fixed launch-file interface, so a hand-written entry launch file can be replaced by a generated one at the same path. It holds a part of a system, never a system: a whole system is built and launched through its deployments. It is not part of any system; a system instantiates the same module.

```yaml
name: perception # launch file stem; equals the file name stem

instance: # the module or node the subsystem launches
  name: perception
  entity: Perception.module
  path: /perception
  parameter_set: [sample_system_perception.parameter_set]

arguments: # launch arguments of the generated file
  - name: use_sim_time
    default: false
  - name: pointcloud_container_name
    default: pointcloud_container
variables: # build-time values, as in a system
  - name: sensor_model
    value: sample_sensor_kit

subscribers: # external interface: instance ports bound to topics
  - name: concatenated_pointcloud
    topic: /sensing/lidar/concatenated/pointcloud
  - name: vector_map
    topic: /map/vector_map
publishers:
  - name: object_recognition/objects
    topic: /perception/object_recognition/objects
servers: # services the instance offers, bound to service names
  - name: reset
    topic: /perception/reset
clients: # services the instance calls
  - name: get_route
    topic: /api/routing/get_route

system_launches: # launch files included ahead of the instance, e.g. the global parameter loader
  - package: autoware_global_parameter_loader
    file: launch/global_params.launch.py
    scoped: false # default true; false applies its parameters to the whole launch file
    arguments:
      - name: use_sim_time
        value: $(var use_sim_time)

node_groups: # as in a system; `external` loads into a container the caller starts
  - name: $(var pointcloud_container_name)
    type: ros2_component_container_mt
    external: true
    nodes: [/perception/obstacle_segmentation/*]
```

The subsystem builds as a system on one compute unit whose boundary ports are the interface ports. An interface port carries the name of the instance port it binds to its topic; instance ports left out keep the topic the design gives them.

The generated launch file follows the layout of a hand-written entry launch file:

1. arguments, with their defaults
2. external interfaces as arguments: `input/<subscriber or client>` and `output/<publisher or server>`, defaulting to their topic or service names
3. system launches
4. the nodes, in groups that push one namespace segment each; containers come first in their group

Node remaps reach interface topics through the interface arguments, so overriding an interface argument rewires every node on that topic. A topic is bound by one interface port, so each interface argument rewires its own topic. Interface ports exporting global publishers are the exception: they may share their global topic, which they keep, and they get no argument.

Node parameters resolved at build time are written to `exports/<name>/config/<node path>.param.yaml`, inside the export tree the designer owns and apart from the package's own `config/`, and loaded with `$(find-pkg-share <package>)`. Values carrying a substitution (`$(var ...)`) stay inline, and parameter files whose path is only known at launch time are loaded in place, keeping the launch order of default, override and mode parameters. Paths under a package directory are written as `$(find-pkg-share <package>)/...`, so the output does not depend on the workspace it was generated in.

The export manifest (`exports/<name>/deployment.json`) records `kind: subsystem` and the launch-file interface: the launch file, its arguments, the interface ports with their topics and the system launches. The studio lists a subsystem beside the systems of its package. Its node diagram draws the interface ports on the subsystem box, labelled with their topics, and the Interface view lists the launch command, the arguments, the interface and the instance. The studio builds the package of a subsystem and replays a scope of it from a rosbag: the launch arguments take their defaults, and the replay starts the containers of the `external` node groups. A subsystem is not launched from the studio.

A subsystem can also be generated outside a package build:

```bash
# Writes launch/sensing.launch.xml and exports/sensing/config/ into the package owning the subsystem file.
python3 <autoware_system_designer>/script/generate_subsystem.py <pkg>/design/sensing.subsystem.yaml \
  [--output-root <dir>] [--source-only]
```

Packages resolve against the workspace install when it exists, otherwise against the sources (`--source-only` forces this). Parameter files generated during a package build exist only in the install.

## Project Structure

To use Autoware System Designer in your ROS 2 package, organize your files as follows:

```text
src/<package_name>/
├── design/             # Main formats
│   ├── node/           # *.node.yaml
│   ├── module/         # *.module.yaml
│   ├── system/         # *.system.yaml
│   ├── parameter_set/  # *.parameter_set.yaml
│   └── subsystem/      # Sub format: <name>.subsystem.yaml (an instance behind a generated launch file)
├── deployment/         # Sub format: *.deployments.yaml (System instantiation)
├── schema/             # *.schema.json (Parameter schemas)
├── CMakeLists.txt
└── package.xml
```

## Build Integration

Autoware System Designer integrates directly into the CMake build process. Select the appropriate macros based on your package type.

In your `CMakeLists.txt`:

```cmake
find_package(autoware_system_designer REQUIRED)

# 1. Generate standalone node launchers (Optional)
# Use this if your package defines nodes and you want individual launch files.
autoware_system_designer_generate_launcher()

# 2. Generate parameter files from schemas (Optional)
# Use this if your package contains JSON schemas in the schema/ directory.
autoware_system_designer_parameter()

# 3. Build a full system deployment (Deployment Packages Only)
# Use this only in the package responsible for building the full system configuration.
autoware_system_designer_build_deploy(
  ${PROJECT_NAME}
  type_alpha.deployments
  PRINT_LEVEL=WARNING
  STRICT=AUTO  # AUTO|ON|OFF (AUTO follows AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT)
)

# 4. Build a subsystem (Optional)
# Generates share/<package>/launch/sensing.launch.xml from sensing.subsystem.yaml under design/.
# See "Subsystem" under "Sub Formats".
autoware_system_designer_build_deploy(
  ${PROJECT_NAME}
  sensing.subsystem
)
```

## Use as an independent workspace

```sh
# make workspace and git clone
cd ~/workspace
git clone git@github.com:autowarefoundation/autoware_system_designer.git autoware_system_designer/src
cd autoware_system_designer

# set dependency
source /opt/ros/$ROS_DISTRO/setup.bash
rosdep update
rosdep install -y --from-paths src --ignore-src --rosdistro $ROS_DISTRO

# build
colcon build

# open the studio (use your web browser)
source install/setup.bash
autoware-system-designer-studio serve          # then open http://127.0.0.1:8780/
# or write a static copy that opens from file://: pack creates the -o directory
# (any path) and its index.html is the entry page
autoware-system-designer-studio pack -o ~/studio_view
xdg-open ~/studio_view/index.html
```

## Glossary

### Entity Definitions (Main Formats)

- **System**: The complete software stack.
- **Module**: A reusable group of nodes/modules.
- **Node**: An atomic software unit (process).
- **Parameter Set**: Configuration overrides.
- **Component**: A top-level instance within a System.

### Connectivity

- **Port**: An interface for data exchange (Input/Output).
- **Connection**: A link between ports.
- **Topic**: The resolved ROS 2 topic name.

### Execution

- **Process**: A logical unit of execution within a node (e.g., a callback).
- **Event**: A runtime occurrence of a process.

### Instantiation (Sub Formats)

- **Deployment**: A specific instance of a System tailored for an environment/vehicle.
- **Subsystem**: One instance launched behind a generated entry launch file with fixed topics and launch arguments.
