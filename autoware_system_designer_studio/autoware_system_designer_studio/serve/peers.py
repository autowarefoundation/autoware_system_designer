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

"""Peer daemons: the daemons serving the compute units this daemon does not, reached over their API.

A peer is bound per compute unit (``<unit>=http://<host>:<port>``, from ``--peer`` or the site file);
units naming one URL share one link. A link is ``connecting`` until its first attempt ends,
``connected`` while the peer's event stream is open, and ``unreachable`` otherwise; it reconnects
with backoff. On every connect it reads the peer's health, systems, sessions and jobs and lists
under ``mismatch`` where the peer differs from this daemon (version, method set, served units,
exported systems); a mismatch is reported, never enforced.

While connected the link relays the peer's ``launch``, ``member``, ``member.log``,
``member.activity`` and ``monitor`` events of its units, and the ``job`` and ``job.log`` events of
their launch jobs, to this daemon's event stream. Job and run ids of a peer are published as
``<unit>/<id>``, which is also how requests name them.

Requests naming a peer's unit are forwarded to it. Log and rate follows made through this daemon
are released on the peer once this daemon's last event stream closes; rate follows are made again
after a reconnect.

A peer on another device is reached through an SSH forward: a binding with an SSH destination
(``--peer-ssh <unit>=<destination>`` or ``ssh`` in the site file) makes the daemon keep
``ssh -N -L 127.0.0.1:<port>:127.0.0.1:<port> <destination>`` running as a job of kind ``tunnel``,
started again with backoff whenever it ends. The forward keeps the peer's port, which the peer
requires in ``Host``; everything else about the connection (user, port, keys, host keys) comes from
the SSH configuration of the daemon's user.

Site file (YAML)::

    peers:
      sub_ecu_1: http://127.0.0.1:8782
      sub_ecu_2: {url: "http://127.0.0.1:8783", ssh: sub-ecu-2}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional
from urllib.parse import urlsplit

from .. import exports
from . import api, jobs, launch
from .events import TOPIC_HELLO, EventHub

logger = logging.getLogger(__name__)

SITE_FILE_ENV = "AUTOWARE_SYSTEM_DESIGNER_SITE_FILE"
SITE_PEERS_KEY = "peers"

PEER_CONNECTING = "connecting"
PEER_CONNECTED = "connected"
PEER_UNREACHABLE = "unreachable"

TOPIC_DAEMON = "daemon"
TOPIC_SYSTEMS_CHANGED = "systems.changed"
# Peer topics relayed as they are for the units of the link.
UNIT_TOPICS = (launch.TOPIC_MEMBER, launch.TOPIC_MEMBER_LOG, launch.TOPIC_MEMBER_ACTIVITY, launch.TOPIC_MONITOR)

CLIENT_HEADER = "X-System-Designer-Client"
CLIENT_NAME = "peer"
ID_SEPARATOR = "/"

RECONNECT_MIN_S = 0.5
RECONNECT_MAX_S = 10.0
CONNECT_TIMEOUT_S = 5.0
# A forwarded request outlasts the peer's own wait for its runtime.
FORWARD_TIMEOUT_S = launch.REQUEST_TIMEOUT_S + 5.0
# Requests whose answers are merged into one of this daemon's; a slow peer is left out.
AGGREGATE_TIMEOUT_S = 5.0
# A stream silent for this many heartbeat intervals of its peer is taken as lost.
STREAM_IDLE_HEARTBEATS = 3.0
STREAM_IDLE_MIN_S = 5.0
DEFAULT_HEARTBEAT_S = 15.0

TUNNEL_JOB_KIND = "tunnel"
SSH = "ssh"
# Applied over the user's SSH configuration: no prompt, and a forward that fails or goes silent ends the job.
SSH_OPTIONS = ("BatchMode=yes", "ExitOnForwardFailure=yes", "ServerAliveInterval=5", "ServerAliveCountMax=3")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost")
TUNNEL_RESTART_MIN_S = 1.0
TUNNEL_RESTART_MAX_S = 15.0
# A tunnel that lasted this long restarts without the backoff it had built up.
TUNNEL_STABLE_S = 30.0
MAX_BODY_BYTES = 64 << 20

Describe = Callable[[], Awaitable[dict[str, Any]]]


class PeerConfigError(Exception):
    pass


class PeerUnreachable(Exception):
    """The peer gave no usable answer; ``refused`` when no connection could be made at all."""

    def __init__(self, message: str, *, refused: bool = False) -> None:
        super().__init__(message)
        self.refused = refused


@dataclass(frozen=True)
class PeerSpec:
    """A unit's peer address, and the SSH destination it is forwarded from when it is on another device."""

    unit: str
    url: str
    ssh: Optional[str] = None


def normalize_url(text: str) -> str:
    """``http://<host>:<port>`` of a peer address; anything else is refused."""
    try:
        split = urlsplit(text.strip())
        port = split.port
    except ValueError as exc:
        raise PeerConfigError(f"invalid peer URL {text!r}: {exc}") from exc
    if split.scheme != "http" or not split.hostname or port is None:
        raise PeerConfigError(f"peer URL {text!r} must be http://<host>:<port>")
    if split.path not in ("", "/") or split.query or split.fragment or split.username:
        raise PeerConfigError(f"peer URL {text!r} must name only a host and a port")
    host = f"[{split.hostname}]" if ":" in split.hostname else split.hostname
    return f"http://{host}:{port}"


def parse_peer(text: str) -> PeerSpec:
    """A ``<unit>=<url>`` binding."""
    unit, sep, url = text.partition("=")
    if not sep or not unit.strip():
        raise PeerConfigError(f"invalid peer {text!r} (expected <unit>=http://<host>:<port>)")
    return PeerSpec(unit.strip(), normalize_url(url))


def parse_destination(text: str) -> str:
    """An SSH destination as ``ssh`` takes it: a configured host name or ``[user@]host``."""
    destination = text.strip()
    if not destination or destination.startswith("-") or any(ch.isspace() for ch in destination):
        raise PeerConfigError(f"invalid SSH destination {text!r}")
    return destination


def parse_peer_ssh(text: str) -> tuple[str, str]:
    """A ``<unit>=<destination>`` SSH binding."""
    unit, sep, destination = text.partition("=")
    if not sep or not unit.strip():
        raise PeerConfigError(f"invalid peer SSH binding {text!r} (expected <unit>=<destination>)")
    return unit.strip(), parse_destination(destination)


def with_ssh(specs: Iterable[PeerSpec], bindings: Iterable[tuple[str, str]]) -> list[PeerSpec]:
    """``specs`` with the SSH destination of each bound unit replaced."""
    by_unit = {spec.unit: spec for spec in specs}
    for unit, destination in bindings:
        if unit not in by_unit:
            raise PeerConfigError(f"SSH destination for compute unit {unit!r}, which has no peer")
        by_unit[unit] = replace(by_unit[unit], ssh=destination)
    return list(by_unit.values())


def validate_specs(specs: Iterable[PeerSpec]) -> None:
    """A forwarded peer answers on this device's loopback, and one address has one way to it."""
    ssh_by_url: dict[str, Optional[str]] = {}
    for spec in specs:
        if spec.ssh is not None and urlsplit(spec.url).hostname not in LOOPBACK_HOSTS:
            raise PeerConfigError(f"peer of {spec.unit!r} is forwarded over SSH, so its URL must be on 127.0.0.1")
        if ssh_by_url.setdefault(spec.url, spec.ssh) != spec.ssh:
            raise PeerConfigError(f"peers at {spec.url} name different SSH destinations")


def site_file_path(explicit: Optional[Path]) -> Optional[Path]:
    """The site file to read: the explicit one, else the one the environment names."""
    if explicit is not None:
        return explicit
    named = os.environ.get(SITE_FILE_ENV)
    return Path(named).expanduser() if named else None


def load_site_file(path: Path) -> list[PeerSpec]:
    """Peer bindings of a site file."""
    import yaml

    try:
        content = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PeerConfigError(f"cannot read site file {path}: {exc}") from exc
    if content is None:
        return []
    if not isinstance(content, dict):
        raise PeerConfigError(f"site file {path} must be a mapping")
    peers = content.get(SITE_PEERS_KEY) or {}
    if not isinstance(peers, dict):
        raise PeerConfigError(f"site file {path}: {SITE_PEERS_KEY} must map compute units to peers")
    specs = []
    for unit, entry in peers.items():
        url = entry.get("url") if isinstance(entry, dict) else entry
        ssh = entry.get("ssh") if isinstance(entry, dict) else None
        if not isinstance(unit, str) or not isinstance(url, str):
            raise PeerConfigError(f"site file {path}: peer {unit!r} needs a URL")
        if ssh is not None and not isinstance(ssh, str):
            raise PeerConfigError(f"site file {path}: ssh of peer {unit!r} must be a destination")
        try:
            specs.append(PeerSpec(unit, normalize_url(url), None if ssh is None else parse_destination(ssh)))
        except PeerConfigError as exc:
            raise PeerConfigError(f"site file {path}: {exc}") from exc
    return specs


def merge_specs(*sources: Iterable[PeerSpec]) -> list[PeerSpec]:
    """One binding per unit; a later source replaces an earlier one's."""
    merged: dict[str, PeerSpec] = {}
    for source in sources:
        for spec in source:
            merged[spec.unit] = spec
    return list(merged.values())


def split_id(value: Any) -> Optional[tuple[str, str]]:
    """(unit, peer id) of a prefixed job or run id; None for an id without a prefix."""
    if not isinstance(value, str) or ID_SEPARATOR not in value:
        return None
    unit, _, rest = value.partition(ID_SEPARATOR)
    return unit, rest


async def read_event(reader: asyncio.StreamReader, idle_s: float) -> Optional[tuple[str, dict[str, Any]]]:
    """The next (topic, data) of an event stream, None at its end; a stream silent for ``idle_s`` is lost."""
    loop = asyncio.get_running_loop()
    silent = PeerUnreachable(f"event stream silent for {idle_s:g} s")
    topic = ""
    data: list[str] = []
    while True:
        # The read is awaited bare, so a cancellation always reaches it; the timer ends a silent one.
        timer = loop.call_later(idle_s, reader.set_exception, silent)
        try:
            line = await reader.readline()
        except (OSError, ValueError, asyncio.LimitOverrunError) as exc:
            raise PeerUnreachable(f"event stream broke: {exc}") from exc
        finally:
            timer.cancel()
        if not line:
            return None
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if text:
            name, _, value = text.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                topic = value
            elif name == "data":
                data.append(value)
            continue
        if topic and data:
            try:
                payload = json.loads("\n".join(data))
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                return topic, payload
        topic, data = "", []


class PeerClient:
    """Requests and the event stream of one peer daemon, one connection per exchange."""

    def __init__(self, url: str) -> None:
        split = urlsplit(url)
        self.url = url
        self._host = split.hostname or ""
        self._port = split.port or 0
        self._netloc = split.netloc

    async def _open(self, head: str, body: bytes = b"") -> tuple[int, asyncio.StreamReader, asyncio.StreamWriter]:
        """Sends one request; answers the status with the stream positioned after the response head."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port, limit=launch.MAX_LINE), CONNECT_TIMEOUT_S
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise PeerUnreachable(f"no connection to {self.url}: {exc or type(exc).__name__}", refused=True) from exc
        try:
            writer.write(f"{head}\r\nHost: {self._netloc}\r\nConnection: close\r\n\r\n".encode("latin-1") + body)
            await writer.drain()
            response = await reader.readuntil(b"\r\n\r\n")
            status = int(response.split(b" ", 2)[1])
        except (OSError, ValueError, IndexError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            writer.close()
            raise PeerUnreachable(f"no HTTP answer from {self.url}: {exc or type(exc).__name__}") from exc
        except BaseException:
            writer.close()
            raise
        return status, reader, writer

    async def rpc(self, method: str, params: Optional[dict[str, Any]] = None, *, timeout: float) -> Any:
        """Result of a method of the peer; its error answer is raised as the ``ApiError`` it describes."""
        try:
            reply = await asyncio.wait_for(self._rpc(method, params or {}), timeout)
        except asyncio.TimeoutError as exc:
            raise PeerUnreachable(f"{self.url} did not answer {method} within {timeout:g} s") from exc
        if not isinstance(reply, dict):
            raise PeerUnreachable(f"malformed answer to {method} from {self.url}")
        if reply.get("ok"):
            return reply.get("result")
        error = reply.get("error") if isinstance(reply.get("error"), dict) else {}
        raise api.ApiError(
            str(error.get("code") or api.INTERNAL), str(error.get("message") or "peer error"), error.get("data")
        )

    async def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        body = json.dumps({"id": 1, "method": method, "params": params}, separators=(",", ":")).encode()
        head = (
            f"POST /api/rpc HTTP/1.1\r\nContent-Type: application/json\r\n"
            f"{CLIENT_HEADER}: {CLIENT_NAME}\r\nContent-Length: {len(body)}"
        )
        _, reader, writer = await self._open(head, body)
        try:
            payload = await reader.read(MAX_BODY_BYTES)
            while not reader.at_eof() and len(payload) < MAX_BODY_BYTES:
                payload += await reader.read(MAX_BODY_BYTES - len(payload))
            return json.loads(payload)
        except (OSError, ValueError) as exc:
            raise PeerUnreachable(f"unreadable answer to {method} from {self.url}: {exc}") from exc
        finally:
            writer.close()

    async def open_events(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            status, reader, writer = await asyncio.wait_for(
                self._open(f"GET /api/events HTTP/1.1\r\n{CLIENT_HEADER}: {CLIENT_NAME}"), CONNECT_TIMEOUT_S * 2
            )
        except asyncio.TimeoutError as exc:
            raise PeerUnreachable(f"{self.url} did not open an event stream") from exc
        if status != 200:
            writer.close()
            raise PeerUnreachable(f"{self.url} refused an event stream (HTTP {status})")
        return reader, writer


class PeerTunnel:
    """The SSH forward a peer's address answers on, kept running as a job; ``job`` is the current one."""

    def __init__(self, url: str, destination: str, runner: jobs.JobRunner) -> None:
        self.destination = destination
        self.job: Optional[jobs.Job] = None
        self._port = urlsplit(url).port
        self._runner = runner

    def argv(self) -> list[str]:
        forward = f"127.0.0.1:{self._port}:127.0.0.1:{self._port}"
        options = [arg for option in SSH_OPTIONS for arg in ("-o", option)]
        return [shutil.which(SSH) or SSH, "-N", *options, "-L", forward, "--", self.destination]

    async def run(self) -> None:
        delay = TUNNEL_RESTART_MIN_S
        while True:
            started = time.monotonic()
            spec = jobs.JobSpec(
                key=f"{TUNNEL_JOB_KIND}:{self._port}",
                kind=TUNNEL_JOB_KIND,
                title=f"tunnel {self.destination}:{self._port}",
                argv=self.argv(),
                cwd=Path("/"),
                params={"destination": self.destination, "port": self._port},
            )
            try:
                job = await self._runner.start(spec)
            except api.ApiError as exc:
                logger.warning("tunnel to %s not started: %s", self.destination, exc.message)
            else:
                previous, self.job = self.job, job
                if previous is not None:
                    self._runner.forget(previous.id)
                await job.done.wait()
                if time.monotonic() - started >= TUNNEL_STABLE_S:
                    delay = TUNNEL_RESTART_MIN_S
            await asyncio.sleep(delay)
            delay = min(delay * 2, TUNNEL_RESTART_MAX_S)


def _session_key(view: Mapping[str, Any]) -> tuple[Any, Any, Any]:
    return view.get("compute_unit"), view.get("system"), view.get("mode")


class PeerLink:
    """The connection to one peer daemon and what this daemon knows of its units.

    ``sessions`` holds the peer's session views (without members) as last reported, kept while the
    peer is unreachable.
    """

    def __init__(
        self,
        url: str,
        units: Iterable[str],
        hub: EventHub,
        describe: Describe,
        tunnel: Optional[PeerTunnel] = None,
    ) -> None:
        self.url = url
        self.units = sorted(set(units))
        self.tunnel = tunnel
        self.client = PeerClient(url)
        self.state = PEER_CONNECTING
        self.error: Optional[str] = None
        self.version: Optional[str] = None
        self.mismatch: list[str] = []
        self.sessions: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
        self._hub = hub
        self._describe = describe
        self._health: dict[str, Any] = {}
        self._systems: dict[str, dict[str, Any]] = {}
        # Peer job id -> unit of the launch jobs relayed.
        self._job_units: dict[str, str] = {}
        self._follows: set[str] = set()
        self._monitors: dict[str, set[str]] = {}
        self._stream: Optional[asyncio.StreamWriter] = None
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    def view(self) -> dict[str, Any]:
        return {
            "units": list(self.units),
            "url": self.url,
            "state": self.state,
            "error": self.error,
            "version": self.version,
            "mismatch": list(self.mismatch),
            "ssh": None if self.tunnel is None else self.tunnel.destination,
            "tunnel": None if self.tunnel is None or self.tunnel.job is None else self.tunnel.job.id,
        }

    # -- connection --------------------------------------------------------

    async def run(self) -> None:
        delay = RECONNECT_MIN_S
        while not self._closed:
            try:
                await self._relay()
            except PeerUnreachable as exc:
                reason = str(exc)
            except api.ApiError as exc:
                reason = f"{exc.code}: {exc.message}"
            except Exception as exc:  # noqa: BLE001 - a malformed peer answer must not end the link
                logger.exception("peer %s: unexpected error", self.url)
                reason = f"{type(exc).__name__}: {exc}"
            if self._closed:
                return
            delay = RECONNECT_MIN_S if self.state == PEER_CONNECTED else min(delay * 2, RECONNECT_MAX_S)
            self._set_state(PEER_UNREACHABLE, reason)
            await asyncio.sleep(delay)

    async def _relay(self) -> None:
        """One connection: reads the peer's state, then relays its events until the stream ends."""
        health = await self.client.rpc("health", timeout=AGGREGATE_TIMEOUT_S)
        health = health if isinstance(health, dict) else {}
        heartbeat = health.get("heartbeat_s")
        heartbeat = heartbeat if isinstance(heartbeat, (int, float)) and heartbeat > 0 else DEFAULT_HEARTBEAT_S
        idle_s = max(STREAM_IDLE_HEARTBEATS * heartbeat, STREAM_IDLE_MIN_S)
        reader, writer = await self.client.open_events()
        self._stream = writer
        try:
            first = await read_event(reader, idle_s)
            if first is None or first[0] != TOPIC_HELLO:
                raise PeerUnreachable(f"{self.url} opened no event stream")
            await self._sync(health)
            self._set_state(PEER_CONNECTED, None)
            while True:
                event = await read_event(reader, idle_s)
                if event is None:
                    raise PeerUnreachable(f"{self.url} closed the event stream")
                self._on_event(*event)
        finally:
            self._stream = None
            writer.close()

    async def _sync(self, health: dict[str, Any]) -> None:
        self._health = health
        version = health.get("version")
        self.version = version if isinstance(version, str) else None
        await self._sync_systems()
        status = await self.client.rpc("launch.status", timeout=AGGREGATE_TIMEOUT_S)
        self.sessions = {}
        for session in _dict_items(status, "sessions"):
            if session.get("compute_unit") in self.units:
                view = self._session_view({k: v for k, v in session.items() if k != "members"})
                self.sessions[_session_key(view)] = view
        listed = await self.client.rpc("jobs.list", timeout=AGGREGATE_TIMEOUT_S)
        self._job_units = {}
        for job in _dict_items(listed, "jobs"):
            self._job_view(job)
        # The peer dropped every follow with the stream it lost.
        self._follows.clear()
        for unit, clients in self._monitors.items():
            for client in clients:
                self._request("monitor.follow", {"compute_unit": unit, "client": client})

    async def _sync_systems(self) -> None:
        listed = await self.client.rpc("systems.list", timeout=AGGREGATE_TIMEOUT_S)
        self._systems = {s["id"]: s for s in _dict_items(listed, "systems") if isinstance(s.get("id"), str)}
        self.mismatch = self._compare(await self._describe())

    def _compare(self, local: Mapping[str, Any]) -> list[str]:
        """Where the peer differs from this daemon, as readable lines."""
        found = []
        if self.version != local.get("version"):
            found.append(f"version {self.version}, this daemon {local.get('version')}")
        methods = self._health.get("methods")
        peer_methods = set(methods) if isinstance(methods, list) else set()
        missing = sorted(set(local.get("methods") or ()) - peer_methods)
        extra = sorted(peer_methods - set(local.get("methods") or ()))
        if missing:
            found.append(f"methods missing: {', '.join(missing)}")
        if extra:
            found.append(f"methods unknown here: {', '.join(extra)}")
        served = self._health.get("served_units")
        if isinstance(served, list):
            for unit in self.units:
                if unit not in served:
                    found.append(f"does not serve compute unit {unit}")
        for system in local.get("systems") or ():
            if not any(unit["name"] in self.units for unit in system["compute_units"]):
                continue
            theirs = self._systems.get(system["id"])
            if theirs is None:
                found.append(f"{system['id']} is not exported")
                continue
            for field, label in _COMPARED_FIELDS:
                if _comparable(system, field) != _comparable(theirs, field):
                    found.append(f"{system['id']}: {label} differ")
        return found

    def _set_state(self, state: str, error: Optional[str]) -> None:
        if state == self.state and error == self.error:
            return
        if state != self.state:
            logger.info("peer %s (%s): %s%s", self.url, ", ".join(self.units), state, f": {error}" if error else "")
        self.state = state
        self.error = error
        self._publish_change()

    def _publish_change(self) -> None:
        self._hub.publish(TOPIC_DAEMON, {"peer": self.view()})
        self._hub.publish(TOPIC_SYSTEMS_CHANGED, {"peer": self.url})

    async def close(self) -> None:
        self._closed = True
        if self._stream is not None:
            self._stream.close()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=1.0)

    # -- events ------------------------------------------------------------

    def _on_event(self, topic: str, data: dict[str, Any]) -> None:
        if topic == launch.TOPIC_LAUNCH:
            if data.get("compute_unit") in self.units:
                view = self._session_view(data)
                self.sessions[_session_key(view)] = view
                self._hub.publish(topic, view)
        elif topic in UNIT_TOPICS:
            if data.get("compute_unit") in self.units:
                self._hub.publish(topic, data)
        elif topic == jobs.TOPIC_JOB:
            view = self._job_view(data)
            if view is not None:
                self._hub.publish(topic, view)
        elif topic == jobs.TOPIC_JOB_LOG:
            unit = self._job_units.get(data.get("job"))
            if unit is not None:
                self._hub.publish(topic, {**data, "job": self._prefixed(unit, data["job"])})
        elif topic == TOPIC_SYSTEMS_CHANGED:
            self._spawn(self._systems_changed())

    async def _systems_changed(self) -> None:
        await self._sync_systems()
        self._publish_change()

    # -- views -------------------------------------------------------------

    def _prefix(self, unit: Any) -> str:
        return unit if unit in self.units else self.units[0]

    def _prefixed(self, unit: Any, peer_id: str) -> str:
        return f"{self._prefix(unit)}{ID_SEPARATOR}{peer_id}"

    def _session_view(self, session: Mapping[str, Any]) -> dict[str, Any]:
        """A peer's session with its job named as this daemon names it."""
        view = dict(session)
        if isinstance(view.get("job"), str):
            view["job"] = self._prefixed(view.get("compute_unit"), view["job"])
        return view

    def _job_view(self, job: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        """A peer's launch job of one of the link's units as this daemon names it; None for any other job."""
        params = job.get("params")
        unit = params.get("compute_unit") if isinstance(params, dict) else None
        if job.get("kind") != launch.LAUNCH_JOB_KIND or unit not in self.units or not isinstance(job.get("id"), str):
            return None
        self._job_units[job["id"]] = unit
        return {**job, "id": self._prefixed(unit, job["id"])}

    def _run_view(self, run: Mapping[str, Any]) -> dict[str, Any]:
        view = dict(run)
        if isinstance(view.get("run"), str):
            view["run"] = self._prefixed(view.get("compute_unit"), view["run"])
        return view

    def unit_status(self, system_id: str, unit: str) -> str:
        """Status of one of the link's units as this daemon reports it."""
        if self.state != PEER_CONNECTED:
            return exports.UNIT_UNREACHABLE
        if any(self.session_active(s) and s.get("system") == system_id for s in self.sessions_of(unit)):
            return exports.UNIT_RUNNING
        units = self._systems.get(system_id, {}).get("compute_units")
        status = next((u.get("status") for u in units or () if isinstance(u, dict) and u.get("name") == unit), None)
        if status in (exports.UNIT_AVAILABLE, exports.UNIT_RUNNING):
            return exports.UNIT_AVAILABLE
        return exports.UNIT_REMOTE_UNSUPPORTED

    def sessions_of(self, unit: str) -> list[dict[str, Any]]:
        return [session for session in self.sessions.values() if session.get("compute_unit") == unit]

    @staticmethod
    def session_active(session: Mapping[str, Any]) -> bool:
        return session.get("state") != launch.SESSION_EXITED

    # -- requests ----------------------------------------------------------

    async def call(self, method: str, params: dict[str, Any], *, timeout: float = FORWARD_TIMEOUT_S) -> Any:
        """Result of a peer method; ``unavailable`` while the peer does not answer."""
        if self.state != PEER_CONNECTED:
            raise api.ApiError(
                api.UNAVAILABLE, f"the daemon at {self.url} is {self.state}: {self.error or 'no answer'}"
            )
        try:
            return await self.client.rpc(method, params, timeout=timeout)
        except PeerUnreachable as exc:
            if exc.refused and self._stream is not None:
                # No listener is left behind the open stream; ending it starts the reconnects.
                self._stream.close()
            raise api.ApiError(api.UNAVAILABLE, f"the daemon at {self.url} gave no answer: {exc}") from exc

    async def forward(self, method: str, params: dict[str, Any]) -> Any:
        """A unit-scoped method on the peer, its result named as this daemon names it."""
        result = await self.call(method, params)
        unit = params.get("compute_unit")
        if method == "launch.start" and isinstance(result, dict):
            if isinstance(result.get("job"), str):
                self._job_units[result["job"]] = self._prefix(unit)
            view = self._session_view(result)
            self.sessions[_session_key(view)] = {k: v for k, v in view.items() if k != "members"}
            return view
        if method == "launch.status":
            return {"sessions": self._status_views(result)}
        if method == "launch.follow":
            self._follows.add(unit)
        elif method == "launch.unfollow" and params.get("path") is None:
            self._follows.discard(unit)
        elif method == "monitor.follow":
            self._monitors.setdefault(unit, set()).add(params.get("client") or "")
        elif method == "monitor.unfollow":
            self._monitors.get(unit, set()).discard(params.get("client") or "")
        return result

    def _status_views(self, status: Any) -> list[dict[str, Any]]:
        sessions = [s for s in _dict_items(status, "sessions") if s.get("compute_unit") in self.units]
        return [self._session_view(session) for session in sessions]

    async def status_views(self) -> list[dict[str, Any]]:
        """Sessions of the link's units with their members; none while the peer does not answer."""
        return self._status_views(await self._aggregate("launch.status", {}))

    async def job_views(self) -> list[dict[str, Any]]:
        """Launch jobs of the link's units; none while the peer does not answer."""
        listed = await self._aggregate("jobs.list", {})
        return [view for view in map(self._job_view, _dict_items(listed, "jobs")) if view is not None]

    async def run_views(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Past runs of the link's units; none while the peer does not answer."""
        runs = _dict_items(await self._aggregate("launch.runs", params), "runs")
        return [self._run_view(run) for run in runs if run.get("compute_unit") in self.units]

    async def _aggregate(self, method: str, params: dict[str, Any]) -> Any:
        if self.state != PEER_CONNECTED:
            return None
        try:
            return await self.call(method, params, timeout=AGGREGATE_TIMEOUT_S)
        except api.ApiError as exc:
            logger.info("peer %s left out of %s: %s", self.url, method, exc.message)
            return None

    async def job_call(self, method: str, peer_id: str, params: dict[str, Any]) -> Any:
        """``jobs.get`` or ``jobs.cancel`` of a launch job of the link's units."""
        if self.state == PEER_CONNECTED and peer_id not in self._job_units:
            raise api.ApiError(api.NOT_FOUND, f"no job {params.get('job')!r}")
        result = await self.call(method, {**params, "job": peer_id})
        job = result.get("job") if method == "jobs.get" and isinstance(result, dict) else result
        view = self._job_view(job) if isinstance(job, dict) else None
        if view is None:
            raise api.ApiError(api.NOT_FOUND, f"no job {params.get('job')!r}")
        return {**result, "job": view} if method == "jobs.get" else view

    async def run_call(self, method: str, peer_run: str, params: dict[str, Any]) -> Any:
        """A method on one past run of the peer."""
        result = await self.call(method, {**params, "run": peer_run})
        return {**result, "run": params.get("run")} if isinstance(result, dict) else result

    def release_follows(self) -> None:
        """Drops every follow made through this daemon on the peer."""
        follows, self._follows = self._follows, set()
        monitors, self._monitors = self._monitors, {}
        if self.state != PEER_CONNECTED:
            return
        for unit in follows:
            self._request("launch.unfollow", {"compute_unit": unit})
        for unit, clients in monitors.items():
            for client in clients:
                self._request("monitor.unfollow", {"compute_unit": unit, "client": client})

    def _request(self, method: str, params: dict[str, Any]) -> None:
        """Sends a request whose answer nobody waits for; a failure is logged."""
        self._spawn(self.client.rpc(method, params, timeout=FORWARD_TIMEOUT_S))

    def _spawn(self, coroutine: Awaitable[Any]) -> None:
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(_log_failure)


_COMPARED_FIELDS = (
    ("default_mode", "default modes"),
    ("modes", "modes"),
    ("compute_units", "compute units"),
    ("deploy_variants", "deploy variants"),
)


def _comparable(system: Mapping[str, Any], field: str) -> Any:
    """The part of a system view that is the same on every daemon serving the same export."""
    value = system.get(field)
    if field == "modes":
        return sorted(m.get("name") for m in value or () if isinstance(m, dict))
    if field == "compute_units":
        return sorted((u.get("name"), u.get("kind")) for u in value or () if isinstance(u, dict))
    return value


def _dict_items(result: Any, key: str) -> list[dict[str, Any]]:
    items = result.get(key) if isinstance(result, dict) else None
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _log_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("peer request failed: %s", exc)


class PeerRegistry:
    """The peer links of this daemon, by compute unit."""

    def __init__(self, specs: Iterable[PeerSpec], hub: EventHub, describe: Describe, runner: jobs.JobRunner) -> None:
        specs = merge_specs(specs)
        validate_specs(specs)
        units_by_url: dict[str, list[str]] = {}
        for spec in specs:
            units_by_url.setdefault(spec.url, []).append(spec.unit)
        ssh_by_url = {spec.url: spec.ssh for spec in specs}
        self.links = [
            PeerLink(url, units, hub, describe, PeerTunnel(url, ssh_by_url[url], runner) if ssh_by_url[url] else None)
            for url, units in units_by_url.items()
        ]
        self._by_unit = {unit: link for link in self.links for unit in link.units}
        self._tasks: list[asyncio.Task] = []

    @property
    def units(self) -> list[str]:
        return sorted(self._by_unit)

    def start(self) -> None:
        self._tasks = [asyncio.ensure_future(link.run()) for link in self.links]
        self._tasks += [asyncio.ensure_future(link.tunnel.run()) for link in self.links if link.tunnel is not None]

    async def stop(self) -> None:
        for link in self.links:
            await link.close()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=1.0)

    def link_of(self, unit: Any) -> Optional[PeerLink]:
        return self._by_unit.get(unit) if isinstance(unit, str) else None

    def link_of_id(self, value: Any) -> Optional[tuple[PeerLink, str]]:
        """(link, peer id) of a prefixed job or run id; None for an id of this daemon."""
        split = split_id(value)
        link = None if split is None else self._by_unit.get(split[0])
        return None if link is None else (link, split[1])

    def views(self) -> list[dict[str, Any]]:
        return [link.view() for link in self.links]

    def sessions(self) -> list[dict[str, Any]]:
        """Every peer session as last reported."""
        return [session for link in self.links for session in link.sessions.values()]

    async def gather(self, call: Callable[[PeerLink], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
        """The lists every link answers ``call`` with, joined."""
        answers = await asyncio.gather(*(call(link) for link in self.links))
        return [item for answer in answers for item in answer]

    def release_follows(self) -> None:
        for link in self.links:
            link.release_follows()
