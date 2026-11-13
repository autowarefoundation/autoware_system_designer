# Execution environments of the demo system

Where the compute units of `TypeAlpha` run. The system design names only the units; each file here
binds them to an execution environment, as a Docker Compose project standing in for the hardware.

| File                                        | Units run as                                    | Network                                              |
| ------------------------------------------- | ----------------------------------------------- | ---------------------------------------------------- |
| `TypeAlpha.host.compose.yaml`               | containers of one ECU                           | host network and IPC, DDS on loopback                |
| `TypeAlpha.bridge.compose.yaml`             | separate ECUs                                   | bridge network `172.30.87.0/24`, fixed addresses     |
| `TypeAlpha.local_pair.compose.yaml`         | two containers of the main ECU and a second ECU | the same network, two units in one network namespace |
| `TypeAlpha.remote_pair.compose.yaml`        | the main ECU and two containers of a second ECU | the same network, two peers behind one address       |
| `TypeAlpha.shared_environment.compose.yaml` | the main ECU and one environment of two units   | the same network, one daemon serving two units       |

The files are named `<System>.<topology>.compose.yaml`. A file states where each unit runs; the
services it extends, their image and the runner are the same for every topology and live in
[`docker_multi_unit`](../../docker_multi_unit/README.md).

| Stated per execution environment    | In the file                                             |
| ----------------------------------- | ------------------------------------------------------- |
| The environment                     | a service                                               |
| The compute units it runs           | `ASD_COMPUTE_UNITS`, one daemon for all of them         |
| Daemon port                         | `ASD_PORT`, unique in the topology                      |
| Device                              | `hostname` of the service holding the network namespace |
| Network namespace and shared memory | `network_mode`, `ipc` (`service:<name>` joins a device) |
| Address                             | `ipv4_address` on the network `vehicle`                 |
| The daemon serving the other units  | `ASD_MAIN: "true"` on one service                       |

Nothing else is stated. The peers of the main daemon follow from these bindings: every unit of
another daemon is bound to that daemon's port on the loopback, and forwarded over SSH from its
address when its device is not the main daemon's. Two rules come with that: daemon ports are
unique across devices, because a forward keeps the port, and every network namespace of another
device has an `sshd`, because a forward ends on the loopback of the namespace it enters.

The remaining keys are Docker's part of standing in for the hardware: `ASD_SSHD_PORT` on a service
holding a network namespace, `ROS_LOCALHOST_ONLY` on the host network, `cap_add` for the fault
tests, and the start order.

An E/E architecture description of the system takes over this content: devices, their networks and
addresses, the execution environments on each device, the binding of every compute unit to one of
them, and the main environment. The runner and the tests read the bindings in one place,
`Topology` of `run.py`, so they stay as they are when the description replaces these values.
