# Multi-unit environment on Docker

One studio daemon per compute unit, each in its own container, standing in for a multi-ECU bench.
The containers build the system designer packages of this checkout and run the example system
`autoware_system_designer_demo_systems:TypeAlpha`: the nodes of
[`autoware_system_designer_demo_nodes`](../autoware_system_designer_demo_nodes/README.md) on three onboard
units, exchanging `std_msgs` messages, every unit subscribing topics published on the other two.

| Unit                 | Components                                 | Daemon port |
| -------------------- | ------------------------------------------ | ----------- |
| `sensing_perception` | sensing, perception, robot state publisher | 8781        |
| `map_localization`   | map, localization, vehicle                 | 8782        |
| `planning_control`   | planning, control                          | 8783        |

This directory holds what every topology shares: the image, its entry point, the compose services
(`compose.base.yaml`), the runner and the tests. Which units run where, on which ports and
addresses, and which daemon serves the others is the content of one compose file per topology, kept
with the system in
[`autoware_system_designer_demo_systems/ee_architecture`](../autoware_system_designer_demo_systems/ee_architecture/README.md).
A file added there is a new topology; `ASD_SYSTEM` and `ASD_ARCHITECTURE_DIR` select the files of
another system.

## Topologies

| Topology   | Stands for                                 | Network                                              | `sensing_perception` reaches its peers by |
| ---------- | ------------------------------------------ | ---------------------------------------------------- | ----------------------------------------- |
| `host`     | containers of one ECU                      | host network and IPC, DDS on loopback                | the shared loopback                       |
| `bridge`   | separate ECUs                              | bridge network `172.30.87.0/24`, fixed addresses     | SSH forwards it keeps as tunnel jobs      |
| `two_ecus` | two containers of one ECU and a second ECU | the same network, two units in one network namespace | the shared loopback, an SSH forward       |

In every topology the daemon of `sensing_perception` has the other two as
[peers](../../autoware_system_designer_studio/README.md#peers): its workbench and API on port 8781
launch, observe and shut down every unit.

Every daemon binds the loopback of its network namespace. A container standing for a device runs
an `sshd` on port 2222 as the unprivileged user `asd`. `run.sh` makes two keys per `up`: the
host's, with which it forwards the main daemon's port to the same port on the host (a daemon
answers only requests whose `Host` names its port), and the one the main daemon opens its forwards
to the other devices with. DDS crosses the bridge network without shared memory.

## Use

```bash
./run.sh topologies              # host, bridge, two_ecus
./run.sh up bridge               # build, start, wait until every daemon is healthy
./run.sh url bridge              # http://127.0.0.1:8781/ the main daemon's workbench
./run.sh units bridge            # unit, service, daemon port, address, role
./run.sh compose bridge logs -f sensing_perception
./run.sh ssh bridge map_localization    # shell in a service with an sshd
./run.sh down bridge             # --volumes also drops the built workspace
```

`up` runs the `build` service first: `colcon build` of the checkout (mounted read-only) into the
volume `asd-multi-unit-ws-<distro>`, which every topology shares, so a later `up` rebuilds only
what changed. Launch a unit from the Launch activity of the workbench, or by request:

```bash
curl -s http://127.0.0.1:8781/api/rpc -H 'Content-Type: application/json' -H 'X-System-Designer-Client: shell' \
    -d '{"id": 1, "method": "launch.start", "params": {"system": "autoware_system_designer_demo_systems:TypeAlpha", "compute_unit": "map_localization", "mode": "default", "deploy": "vehicle_x"}}'
```

| Variable                 | Default                           | Role                                            |
| ------------------------ | --------------------------------- | ----------------------------------------------- |
| `ASD_ROS_DISTRO`         | `humble`                          | ROS distribution of the image                   |
| `ASD_ROS_DOMAIN_ID`      | `87`                              | DDS domain of the units                         |
| `ASD_RMW_IMPLEMENTATION` | the distribution's                | RMW implementation, e.g. `rmw_cyclonedds_cpp`   |
| `ASD_UID`, `ASD_GID`     | the calling user's                | ids of the container user                       |
| `ASD_STATE_DIR`          | `$XDG_RUNTIME_DIR/asd-multi-unit` | client key, known hosts, SSH control socket     |
| `ASD_PACKAGES`           | studio, examples                  | packages the `build` service builds up to       |
| `ASD_SYSTEM`             | `TypeAlpha`                       | system whose topology files are run             |
| `ASD_ARCHITECTURE_DIR`   | the examples' `ee_architecture`   | directory of `<system>.<topology>.compose.yaml` |

The variables are the environment's own: `ROS_DISTRO` and `ROS_DOMAIN_ID` of the calling shell are
not read. Daemon ports and addresses are the topology file's.

## Faults

A unit on a device of its own can be cut off the way an ECU fails. The main daemon reports it
`unreachable` within about 15 s and reads it anew once it answers again:

```bash
./run.sh compose bridge pause planning_control                           # frozen ECU
./run.sh compose bridge unpause planning_control
./run.sh compose bridge kill planning_control                            # power loss
./run.sh compose bridge start planning_control                           # power back, launch gone
docker network disconnect asd-multi-unit-bridge_vehicle asd-multi-unit-bridge-planning_control-1
docker network connect --ip 172.30.87.13 asd-multi-unit-bridge_vehicle asd-multi-unit-bridge-planning_control-1
./run.sh compose bridge exec -u root planning_control tc qdisc add dev eth0 root netem delay 50ms loss 5%
```

## Tests

```bash
python3 -m pytest demo/docker_multi_unit/test            # --keep leaves the last topology up
```

The tests carry the marker `docker` and are skipped without a Docker daemon. They bring up each
topology in turn and drive every unit through its main daemon alone: the peers connect without
mismatch, a peer on another device through an SSH forward, every unit launches, each
unit receives a topic of another unit, member output is readable, a shutdown ends one unit's
processes and leaves the others running, and the past runs of all three are listed and read. A unit
on a device of its own also stays reachable over a slow, lossy link, and is reported `unreachable`
and then read anew when it is paused, taken off the network, or killed and started again. They are
not part of `colcon test`.

## Limits

Docker does not reproduce NIC and switch timing, time synchronisation, GPU or Jetson targets, or
the start order of system services. `docker network disconnect` removes the container's interface,
where a pulled cable leaves it in place: the daemons find each other again, the DDS participants of
a running launch do not, and the unit needs a new launch.
