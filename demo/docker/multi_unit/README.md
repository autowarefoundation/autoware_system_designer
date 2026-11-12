# Multi-unit environment on Docker

One studio daemon per compute unit, each in its own container, standing in for a multi-ECU bench.
The containers build the system designer packages of this checkout and run the example system
`autoware_system_design_examples:TypeAlpha`: the nodes of
[`autoware_system_dummy_modules`](../../autoware_system_dummy_modules/README.md) on three onboard
units, exchanging `std_msgs` messages, every unit subscribing topics published on the other two.

| Unit        | Components                                 | Daemon port |
| ----------- | ------------------------------------------ | ----------- |
| `main_ecu`  | sensing, perception, robot state publisher | 8781        |
| `sub_ecu_1` | map, localization, vehicle                 | 8782        |
| `sub_ecu_2` | planning, control                          | 8783        |

This directory holds what every environment shares: the image, its entry point, the runner and
the tests. Which units exist, on which ports and addresses, and which daemon serves the others is
the content of one compose file per topology, kept with the system in
[`autoware_system_design_examples/ee_architecture`](../../autoware_system_design_examples/ee_architecture/README.md).
`run.sh` reads the units from that file; `ASD_SYSTEM` and `ASD_ARCHITECTURE_DIR` select the files
of another system.

## Topologies

| Topology | Compose file                    | Stands for            | Network                                          | `main_ecu` reaches its peers by      |
| -------- | ------------------------------- | --------------------- | ------------------------------------------------ | ------------------------------------ |
| `host`   | `TypeAlpha.host.compose.yaml`   | containers of one ECU | host network and IPC, DDS on loopback            | the shared loopback                  |
| `bridge` | `TypeAlpha.bridge.compose.yaml` | separate ECUs         | bridge network `172.30.87.0/24`, fixed addresses | SSH forwards it keeps as tunnel jobs |

In both topologies the daemon of `main_ecu` has the other two as
[peers](../../../autoware_system_designer_studio/README.md#peers): its workbench and API on port 8781
launch, observe and shut down every unit.

Every daemon binds the loopback of its own network namespace. In the `bridge` topology each
container runs an `sshd` on port 2222 as the unprivileged user `asd`. `run.sh` makes two keys per
`up`: the host's, with which it forwards each daemon port to the same port on the host (a daemon
answers only requests whose `Host` names its port), and the one the daemon of `main_ecu` opens its
forwards to the other units with. DDS crosses the bridge network without shared memory.

## Use

```bash
./run.sh up host            # or bridge: build, start, wait until every daemon is healthy
./run.sh urls               # http://127.0.0.1:8781/ ... one workbench per unit
./run.sh units bridge       # unit, daemon port and address
./run.sh compose host logs -f main_ecu
./run.sh ssh sub_ecu_1      # bridge only: shell on the unit
./run.sh down host          # --volumes also drops the built workspace
```

`up` runs the `build` service first: `colcon build` of the checkout (mounted read-only) into the
volume `asd-multi-unit-ws-<distro>`, which both topologies share, so a later `up` rebuilds only
what changed. Launch a unit from the Launch activity of its workbench, or by request:

```bash
curl -s http://127.0.0.1:8781/api/rpc -H 'Content-Type: application/json' -H 'X-System-Designer-Client: shell' \
    -d '{"id": 1, "method": "launch.start", "params": {"system": "autoware_system_design_examples:TypeAlpha", "compute_unit": "main_ecu", "mode": "default", "deploy": "vehicle_x"}}'
```

| Variable               | Default                           | Role                                                |
| ---------------------- | --------------------------------- | --------------------------------------------------- |
| `ASD_ROS_DISTRO`       | `humble`                          | ROS distribution of the image                       |
| `ASD_ROS_DOMAIN_ID`    | `87`                              | DDS domain of the units                             |
| `ASD_PORT_<UNIT>`      | `8781` to `8783`                  | daemon port of `MAIN_ECU`, `SUB_ECU_1`, `SUB_ECU_2` |
| `ASD_SUBNET_PREFIX`    | `172.30.87`                       | first three octets of the bridge network            |
| `ASD_UID`, `ASD_GID`   | the calling user's                | ids of the container user                           |
| `ASD_STATE_DIR`        | `$XDG_RUNTIME_DIR/asd-multi-unit` | client key, known hosts, SSH control sockets        |
| `ASD_PACKAGES`         | studio, examples                  | packages the `build` service builds up to           |
| `ASD_SYSTEM`           | `TypeAlpha`                       | system whose topology files are run                 |
| `ASD_ARCHITECTURE_DIR` | the examples' `ee_architecture`   | directory of `<system>.<topology>.compose.yaml`     |

The variables are the environment's own: `ROS_DISTRO` and `ROS_DOMAIN_ID` of the calling shell are
not read.

## Faults

In the `bridge` topology a unit can be cut off the way an ECU fails. The daemon of `main_ecu`
reports it `unreachable` within about 15 s and reads it anew once it answers again:

```bash
./run.sh compose bridge pause sub_ecu_2                                   # frozen ECU
./run.sh compose bridge unpause sub_ecu_2
./run.sh compose bridge kill sub_ecu_2                                    # power loss
./run.sh compose bridge start sub_ecu_2                                   # power back, launch gone
docker network disconnect asd-multi-unit-bridge_vehicle asd-multi-unit-bridge-sub_ecu_2-1
docker network connect --ip 172.30.87.13 asd-multi-unit-bridge_vehicle asd-multi-unit-bridge-sub_ecu_2-1
./run.sh compose bridge exec -u root sub_ecu_2 tc qdisc add dev eth0 root netem delay 50ms loss 5%
```

## Tests

```bash
python3 -m pytest demo/docker/multi_unit/test            # --keep leaves the last topology up
```

The tests carry the marker `docker` and are skipped without a Docker daemon. They bring up each
topology in turn and drive every unit through the daemon of `main_ecu` alone: the peers connect
without mismatch, every unit launches, each unit receives a topic of another unit, member
output is readable, a shutdown ends one unit's processes and leaves the others running, and the
past runs of all three are listed and read. In the `bridge` topology a unit also stays reachable
over a slow, lossy link, and is reported `unreachable` and then read anew when it is paused, taken
off the network, or killed and started again. They are not part of `colcon test`.

## Limits

Docker does not reproduce NIC and switch timing, time synchronisation, GPU or Jetson targets, or
the start order of system services. `docker network disconnect` removes the container's interface,
where a pulled cable leaves it in place: the daemons find each other again, the DDS participants of
a running launch do not, and the unit needs a new launch.
