#!/usr/bin/env python3
# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Multi-unit environment on Docker: one studio daemon per execution environment of a system's compute units.

A topology is the compose file <ASD_SYSTEM>.<TOPOLOGY>.compose.yaml in ASD_ARCHITECTURE_DIR, e.g.
host, bridge, local_pair of TypeAlpha. It states where each unit runs; the peers of the main daemon
follow from that and reach it as a site file.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

HERE = Path(__file__).resolve().parent
STATE_DIR = Path(
    os.environ.get("ASD_STATE_DIR") or Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "asd-multi-unit"
)
SYSTEM = os.environ.get("ASD_SYSTEM") or "TypeAlpha"
ARCHITECTURE_DIR = Path(
    os.environ.get("ASD_ARCHITECTURE_DIR") or HERE.parent / "autoware_system_designer_demo_systems" / "ee_architecture"
)
SUFFIX = ".compose.yaml"

# Mounted read-only into every unit: authorized_keys, the main daemon's peer key and its site file.
SITE_DIR = STATE_DIR / "site"
SITE_FILE = SITE_DIR / "peers.site.yaml"
CLIENT_KEY = STATE_DIR / "id_ed25519"
PEER_KEY = SITE_DIR / "peer_ed25519"
KNOWN_HOSTS = STATE_DIR / "known_hosts"
CONTROL_SOCKET = STATE_DIR / "main.sock"
USER = "asd"

COMPOSE_ENV = {
    "ASD_UID": os.environ.get("ASD_UID") or str(os.getuid()),
    "ASD_GID": os.environ.get("ASD_GID") or str(os.getgid()),
    "ASD_DOCKER_DIR": str(HERE),
    "ASD_SOURCE_DIR": os.environ.get("ASD_SOURCE_DIR") or str(HERE.parent.parent),
    "ASD_SITE_DIR": str(SITE_DIR),
}


class TopologyError(Exception):
    pass


@dataclass(frozen=True)
class Unit:
    """Binding of one compute unit, as the topology's compose file states it.

    ``netns`` is the service holding the network namespace of the unit's service; ``address`` and
    ``sshd`` are that namespace's, None without. ``device`` is its hostname, "host" on the host
    network. ``main`` marks the units of the daemon that has the others as peers.
    """

    name: str
    service: str
    port: int
    address: Optional[str]
    sshd: Optional[int]
    device: str
    netns: str
    main: bool

    @property
    def destination(self) -> str:
        """SSH destination of the unit's network namespace."""
        return f"ssh://{USER}@{self.address}:{self.sshd}"


def topologies() -> List[str]:
    """Topologies of the system, one per compose file beside it."""
    prefix = f"{SYSTEM}."
    return sorted(path.name[len(prefix) : -len(SUFFIX)] for path in ARCHITECTURE_DIR.glob(f"{prefix}*{SUFFIX}"))


def compose(topology: str, *args: str, **kwargs) -> subprocess.CompletedProcess:
    """``docker compose`` on a topology's file."""
    file = ARCHITECTURE_DIR / f"{SYSTEM}.{topology}{SUFFIX}"
    if not file.is_file():
        raise TopologyError(f"no topology '{topology}' of {SYSTEM}: {file}")
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.chmod(0o700)
    command = ["docker", "compose", "-f", str(file), *args]
    return subprocess.run(command, env={**os.environ, **COMPOSE_ENV}, check=False, **kwargs)


class Topology:
    """The bindings of a topology: the one place its compose file is read."""

    def __init__(self, name: str) -> None:
        self.name = name
        listed = compose(name, "config", "--format", "json", capture_output=True, text=True)
        if listed.returncode != 0:
            raise TopologyError(listed.stderr.strip())
        config = json.loads(listed.stdout)
        self.project: str = config["name"]
        services: Dict[str, dict] = config["services"]

        def netns_of(service: str) -> str:
            mode = services[service].get("network_mode") or ""
            return netns_of(mode[len("service:") :]) if mode.startswith("service:") else service

        self.units: List[Unit] = []
        for service, spec in services.items():
            environment = spec.get("environment") or {}
            netns = netns_of(service)
            owner = services[netns]
            networks = (owner.get("networks") or {}).values()
            sshd = (owner.get("environment") or {}).get("ASD_SSHD_PORT")
            for unit in (environment.get("ASD_COMPUTE_UNITS") or "").split():
                self.units.append(
                    Unit(
                        name=unit,
                        service=service,
                        port=int(environment["ASD_PORT"]),
                        address=next((n["ipv4_address"] for n in networks if n and n.get("ipv4_address")), None),
                        sshd=int(sshd) if sshd else None,
                        device=owner.get("hostname") or ("host" if owner.get("network_mode") == "host" else netns),
                        netns=netns,
                        main=environment.get("ASD_MAIN") == "true",
                    )
                )
        if not self.units:
            raise TopologyError(f"no unit in topology '{name}' of {SYSTEM}")
        main_services = sorted({unit.service for unit in self.units if unit.main})
        if len(main_services) != 1:
            raise TopologyError(f"one service of the topology has ASD_MAIN, not {main_services or 'none'}")
        self.main: Unit = next(unit for unit in self.units if unit.main)

    def unit(self, name: str) -> Unit:
        return next(unit for unit in self.units if unit.name == name)

    def remote(self, unit: Unit) -> bool:
        """Whether ``unit`` is on another device than the main daemon, behind an SSH forward."""
        return unit.device != self.main.device

    def peers(self) -> Dict[str, dict]:
        """Peer bindings of the main daemon, as its site file holds them.

        Every unit of another daemon is at that daemon's port on the loopback, forwarded from its
        network namespace when its device is another one.
        """
        bindings = {}
        for unit in self.units:
            if unit.service == self.main.service:
                continue
            bindings[unit.name] = {"url": f"http://127.0.0.1:{unit.port}"}
            if self.remote(unit):
                bindings[unit.name]["ssh"] = unit.destination
        return bindings

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.main.port}/"


def _ssh(unit: Unit, *args: str) -> List[str]:
    return [
        "ssh",
        *("-i", str(CLIENT_KEY)),
        *("-o", "BatchMode=yes"),
        *("-o", "IdentitiesOnly=yes"),
        *("-o", "StrictHostKeyChecking=accept-new"),
        *("-o", f"UserKnownHostsFile={KNOWN_HOSTS}"),
        *("-o", "ConnectTimeout=5"),
        *("-o", "ServerAliveInterval=2"),
        *("-o", "ServerAliveCountMax=3"),
        *args,
        unit.destination,
    ]


def _make_site(topology: Topology) -> None:
    """Keys of this run and the main daemon's site file.

    The host's client key and the key the main daemon reaches its peers with; the containers read
    the public halves as authorized_keys.
    """
    for key in (CLIENT_KEY, PEER_KEY):
        for path in (key, key.with_name(f"{key.name}.pub")):
            path.unlink(missing_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", key.name, "-f", str(key)], check=True)
    KNOWN_HOSTS.unlink(missing_ok=True)
    public = [key.with_name(f"{key.name}.pub").read_text() for key in (CLIENT_KEY, PEER_KEY)]
    (SITE_DIR / "authorized_keys").write_text("".join(public))
    SITE_FILE.write_text(json.dumps({"peers": topology.peers()}, indent=2) + "\n")


def _close_tunnel() -> None:
    if CONTROL_SOCKET.exists():
        closed = subprocess.run(["ssh", "-S", str(CONTROL_SOCKET), "-O", "exit", "main"], capture_output=True)
        if closed.returncode != 0:
            CONTROL_SOCKET.unlink(missing_ok=True)


def _open_tunnel(topology: Topology) -> None:
    """Forwards the main daemon's port from its container's loopback to the host's, on the same port."""
    main = topology.main
    if main.sshd is None:
        return
    forward = f"127.0.0.1:{main.port}:127.0.0.1:{main.port}"
    options = ("-S", str(CONTROL_SOCKET), "-f", "-N", "-M", "-o", "ExitOnForwardFailure=yes", "-L", forward)
    subprocess.run(_ssh(main, *options), check=True)


def up(topology: Topology) -> int:
    _close_tunnel()
    _make_site(topology)
    started = compose(topology.name, "up", "--build", "--wait")
    if started.returncode != 0:
        return started.returncode
    _open_tunnel(topology)
    print(topology.url)
    return 0


def down(topology: str, *args: str) -> int:
    _close_tunnel()
    return compose(topology, "down", *args).returncode


def show_units(topology: Topology) -> None:
    rows = [("UNIT", "SERVICE", "PORT", "ADDRESS", "DEVICE", "NETNS", "ROLE")]
    for unit in topology.units:
        role = "main" if unit.main else "peer"
        rows.append((unit.name, unit.service, str(unit.port), unit.address or "-", unit.device, unit.netns, role))
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    for row in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


def shell(topology: Topology, service: str, *command: str) -> int:
    unit = next((unit for unit in topology.units if unit.service == service and unit.sshd is not None), None)
    if unit is None:
        raise TopologyError(f"no service '{service}' with an sshd in topology '{topology.name}'")
    os.execvp("ssh", _ssh(unit, *command))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    commands.add_parser("topologies", help="topologies of the system")
    for name, summary in (
        ("up", "build, start and wait until every daemon is healthy"),
        ("down", "stop and remove the containers (--volumes drops the built workspace)"),
        ("compose", "docker compose on the topology (ps, logs, pause, exec, ...)"),
        ("units", "service, daemon port, address, device, network namespace and role of each unit"),
        ("url", "URL of the daemon that serves every unit"),
        ("ssh", "shell in a service with an sshd: ssh TOPOLOGY SERVICE [CMD...]"),
    ):
        command = commands.add_parser(name, help=summary)
        command.add_argument("topology")
        if name in ("down", "compose", "ssh"):
            command.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.command == "topologies":
            print("\n".join(topologies()))
        elif args.command == "up":
            return up(Topology(args.topology))
        elif args.command == "down":
            return down(args.topology, *args.args)
        elif args.command == "compose":
            return compose(args.topology, *args.args).returncode
        elif args.command == "units":
            show_units(Topology(args.topology))
        elif args.command == "url":
            print(Topology(args.topology).url)
        elif args.command == "ssh":
            if not args.args:
                parser.error("ssh needs a service")
            return shell(Topology(args.topology), *args.args)
    except TopologyError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
