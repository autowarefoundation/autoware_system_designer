# Execution environments of the example system

Where the compute units of `TypeAlpha` run. The system design names only the units; each file here
binds them to an execution environment, as a Docker Compose project standing in for the hardware.

| File                              | Units run as                               | Network                                              |
| --------------------------------- | ------------------------------------------ | ---------------------------------------------------- |
| `TypeAlpha.host.compose.yaml`     | containers of one ECU                      | host network and IPC, DDS on loopback                |
| `TypeAlpha.bridge.compose.yaml`   | separate ECUs                              | bridge network `172.30.87.0/24`, fixed addresses     |
| `TypeAlpha.two_ecus.compose.yaml` | two containers of one ECU and a second ECU | the same network, two units in one network namespace |

The files are named `<System>.<topology>.compose.yaml`. A file holds the binding of each unit and
nothing else; the services it extends, their image and the runner are the same for every topology
and live in [`docker/multi_unit`](../../docker/multi_unit/README.md).

| Binding of a unit                   | In the file                             |
| ----------------------------------- | --------------------------------------- |
| The environment it runs in          | its service, with `ASD_COMPUTE_UNIT`    |
| Network namespace and shared memory | `network_mode`, `ipc`                   |
| Address                             | `ipv4_address` on the network `vehicle` |
| Daemon port                         | `ASD_PORT`                              |
| The daemon serving the other units  | the service with `ASD_PEERS`            |
| A peer on another device            | its address under `ASD_PEER_SSH`        |

An E/E architecture description of the system takes over this content: devices, their networks and
addresses, the execution environments on each device, and the binding of every compute unit to one
of them. The runner and the tests read the bindings in one place, `run.sh units <topology>`, so
they stay as they are when the description replaces these values.
