# Execution environments of the example system

Where the compute units of `TypeAlpha` run. The system design names only the units; each file here
binds them to an execution environment, as a Docker Compose project standing in for the hardware.

| File                            | Units run as          | Network                                          |
| ------------------------------- | --------------------- | ------------------------------------------------ |
| `TypeAlpha.host.compose.yaml`   | containers of one ECU | host network and IPC, DDS on loopback            |
| `TypeAlpha.bridge.compose.yaml` | separate ECUs         | bridge network `172.30.87.0/24`, fixed addresses |

A file holds what differs between environments: the service of each unit, its daemon port, its
address, and the peers the daemon of `main_ecu` serves. The image, its entry point and the runner
are the same for every environment and live in [`docker/multi_unit`](../../docker/multi_unit/README.md),
which reads the units, ports and addresses from these files.

The files are named `<System>.<topology>.compose.yaml`. An E/E architecture description of the
system takes over this content: devices, their networks and addresses, the execution environments
on each device, and the binding of every compute unit to one of them.
