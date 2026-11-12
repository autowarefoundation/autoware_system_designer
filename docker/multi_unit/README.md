# Multi-unit environment on Docker

One studio daemon per compute unit, each in its own container, standing in for a multi-ECU bench.
The containers build the system designer packages of this checkout and run the example system
`autoware_system_design_examples:MultiUnitDemo`: `demo_nodes_cpp` talkers and listeners on three
onboard units, every unit subscribing a topic published on another one.

| Unit        | Nodes                                   | Daemon port |
| ----------- | --------------------------------------- | ----------- |
| `main_ecu`  | `/command/talker`, `/report/listener`   | 8781        |
| `sub_ecu_1` | `/command/listener_1`                   | 8782        |
| `sub_ecu_2` | `/command/listener_2`, `/report/talker` | 8783        |

## Topologies

| Topology | Compose file          | Stands for            | Network                                          | Daemon reached by                | Peers                     |
| -------- | --------------------- | --------------------- | ------------------------------------------------ | -------------------------------- | ------------------------- |
| `host`   | `compose.host.yaml`   | containers of one ECU | host network and IPC, DDS on loopback            | the host's loopback              | `main_ecu` serves all     |
| `bridge` | `compose.bridge.yaml` | separate ECUs         | bridge network `172.30.87.0/24`, fixed addresses | SSH forward to the unit's `sshd` | none, one client per unit |

In the `host` topology the daemon of `main_ecu` has the other two as
[peers](../../autoware_system_designer_studio/README.md#peers): its workbench and API on port 8781
launch, observe and shut down every unit.

Every daemon binds the loopback of its own network namespace. In the `bridge` topology each
container runs an `sshd` on port 2222 as the unprivileged user `asd`; `run.sh` makes a client key
per `up`, and forwards each daemon port to the same port on the host, since a daemon answers only
requests whose `Host` names its port. DDS crosses the bridge network without shared memory.

## Use

```bash
./run.sh up host            # or bridge: build, start, wait until every daemon is healthy
./run.sh urls               # http://127.0.0.1:8781/ ... one workbench per unit
./run.sh compose host logs -f main_ecu
./run.sh ssh sub_ecu_1      # bridge only: shell on the unit
./run.sh down host          # --volumes also drops the built workspace
```

`up` runs the `build` service first: `colcon build` of the checkout (mounted read-only) into the
volume `asd-multi-unit-ws-<distro>`, which both topologies share, so a later `up` rebuilds only
what changed. Launch a unit from the Launch activity of its workbench, or by request:

```bash
curl -s http://127.0.0.1:8781/api/rpc -H 'Content-Type: application/json' -H 'X-System-Designer-Client: shell' \
    -d '{"id": 1, "method": "launch.start", "params": {"system": "autoware_system_design_examples:MultiUnitDemo", "compute_unit": "main_ecu", "mode": "default"}}'
```

| Variable             | Default                           | Role                                                |
| -------------------- | --------------------------------- | --------------------------------------------------- |
| `ASD_ROS_DISTRO`     | `humble`                          | ROS distribution of the image                       |
| `ASD_ROS_DOMAIN_ID`  | `87`                              | DDS domain of the units                             |
| `ASD_PORT_<UNIT>`    | `8781` to `8783`                  | daemon port of `MAIN_ECU`, `SUB_ECU_1`, `SUB_ECU_2` |
| `ASD_SUBNET_PREFIX`  | `172.30.87`                       | first three octets of the bridge network            |
| `ASD_UID`, `ASD_GID` | the calling user's                | ids of the container user                           |
| `ASD_STATE_DIR`      | `$XDG_RUNTIME_DIR/asd-multi-unit` | client key, known hosts, SSH control sockets        |
| `ASD_PACKAGES`       | studio, examples                  | packages the `build` service builds up to           |

The variables are the environment's own: `ROS_DISTRO` and `ROS_DOMAIN_ID` of the calling shell are
not read.

## Faults

In the `bridge` topology a unit can be cut off the way an ECU fails:

```bash
./run.sh compose bridge pause sub_ecu_2                                   # frozen ECU
./run.sh compose bridge unpause sub_ecu_2
./run.sh compose bridge kill sub_ecu_2                                    # power loss
docker network disconnect asd-multi-unit-bridge_vehicle asd-multi-unit-bridge-sub_ecu_2-1
./run.sh compose bridge exec -u root sub_ecu_2 tc qdisc add dev eth0 root netem delay 50ms loss 5%
```

## Tests

```bash
python3 -m pytest docker/multi_unit/test            # --keep leaves the last topology up
```

The tests carry the marker `docker` and are skipped without a Docker daemon. They bring up each
topology in turn and check, through the daemons' API alone: each daemon serves its unit, every
unit launches, each listener receives the topic of another unit, member output is readable, a
paused unit silences its topics and resumes (`bridge`), a shutdown ends one unit's processes and
leaves the others running, and each daemon lists the past run of its own unit. In the `host`
topology every unit is driven through the daemon of `main_ecu`, which also lists the sessions,
jobs and past runs of all three. They are not part of `colcon test`.

## Limits

Docker does not reproduce NIC and switch timing, time synchronisation, GPU or Jetson targets, or
the start order of system services.
