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

import difflib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.source_location import format_source, source_from_config
from autoware_system_designer.model.export_schema import LauncherPortData
from autoware_system_designer.model.links import Connection, ConnectionType, Link
from autoware_system_designer.model.ports import InPort, OutPort, Port

if TYPE_CHECKING:
    from autoware_system_designer.builder.instances.instances import Instance


@dataclass
class _PortInfo:
    """Runtime metadata for a port during connection resolution."""

    port_name: str
    instance: Optional["Instance"]
    port: Optional[InPort | OutPort]
    role: Optional[str] = None


logger = logging.getLogger(__name__)


WILDCARDS = Connection.WILDCARDS


def has_wildcard(text: str) -> bool:
    return any(c in text for c in WILDCARDS)


@lru_cache(maxsize=None)
def _compile_pattern(pattern: str) -> Tuple[re.Pattern, Tuple[str, ...]]:
    """Regex of a pattern and the wildcard character of each capture group, in order."""
    parts: List[str] = []
    kinds: List[str] = []
    literal_start = 0
    for idx, char in enumerate(pattern):
        if char in WILDCARDS:
            parts.append(re.escape(pattern[literal_start:idx]))
            parts.append("(.*?)")
            kinds.append(char)
            literal_start = idx + 1
    parts.append(re.escape(pattern[literal_start:]))
    return re.compile("".join(parts)), tuple(kinds)


def match_and_pair_wildcard_ports(
    source_pattern: str,
    target_pattern: str,
    source_ports: Dict[str, "_PortInfo"],
    target_ports: Dict[str, "_PortInfo"],
) -> List[tuple[str, str]]:
    """Return (source_key, target_key) pairs honoring wildcard usage.

    Rules:
    - One side has a wildcard => every match of that side pairs with the other side's port,
      except a match naming the same instance port as the other side.
    - Both sides have wildcards => source captures are substituted into the target pattern.

    The wildcards *, ^ and + all match any character sequence; distinct characters
    let a target pattern place each capture of the source pattern.
    """
    source_regex, _ = _compile_pattern(source_pattern)
    target_regex, _ = _compile_pattern(target_pattern)
    src_matches = [key for key in source_ports if source_regex.fullmatch(key)]
    tgt_matches = [key for key in target_ports if target_regex.fullmatch(key)]
    if not src_matches or not tgt_matches:
        return []

    src_wc = has_wildcard(source_pattern)
    tgt_wc = has_wildcard(target_pattern)

    if src_wc and not tgt_wc:
        return [(s, t) for s in sorted(src_matches) for t in tgt_matches if s != t]
    if tgt_wc and not src_wc:
        return [(s, t) for s in src_matches for t in sorted(tgt_matches) if s != t]

    targets = set(tgt_matches)
    pairs: List[tuple[str, str]] = []
    for s in src_matches:
        t = _apply_wildcard_substitution(source_pattern, target_pattern, s)
        if t in targets:
            pairs.append((s, t))
    return pairs


def _apply_wildcard_substitution(source_pattern: str, target_pattern: str, matched_name: str) -> str:
    """Substitute wildcard captures from a matched source key into target pattern.

    Each capture of the source pattern fills the next wildcard of the same character
    in the target pattern; a target wildcard without a capture stays as written.

    Example:
      source: "input.*_^" matched against "input.pcl_upper_right"
      target: "^.input.*"
      result: "upper_right.input.pcl"
    """
    source_regex, kinds = _compile_pattern(source_pattern)
    match = source_regex.fullmatch(matched_name)
    if not match:
        return matched_name

    captures: Dict[str, List[str]] = {char: [] for char in WILDCARDS}
    for kind, value in zip(kinds, match.groups()):
        captures[kind].append(value)

    used = {char: 0 for char in WILDCARDS}
    result: List[str] = []
    for char in target_pattern:
        if char in WILDCARDS and used[char] < len(captures[char]):
            result.append(captures[char][used[char]])
            used[char] += 1
        else:
            result.append(char)
    return "".join(result)


def _topic_segments(topic: str) -> Optional[List[str]]:
    """Segments of an absolute ROS topic; None when the topic is relative or has an empty segment."""
    if not isinstance(topic, str) or not topic.startswith("/"):
        return None
    segments = topic[1:].split("/")
    return segments if all(segments) else None


class LinkManager:
    """Manages port, connection, and link operations for Instance objects."""

    def __init__(self, instance: "Instance"):
        self.instance = instance
        self.in_ports: Dict[str, InPort] = {}
        self.out_ports: Dict[str, OutPort] = {}
        self.links: List[Link] = []
        # Boundary output each publishing node port is exported through, by node port identity.
        self._exports: Dict[int, OutPort] = {}

    def get_in_port(self, name: str) -> InPort:
        if name in self.in_ports:
            return self.in_ports[name]
        raise ValidationError(self._format_missing_port_error(name, direction="input"))

    def get_out_port(self, name: str) -> OutPort:
        if name in self.out_ports:
            return self.out_ports[name]
        raise ValidationError(self._format_missing_port_error(name, direction="output"))

    # ------------------------------------------------------------------
    # Error formatting helpers
    # ------------------------------------------------------------------
    def _format_missing_port_error(self, port_name: str, direction: str) -> str:
        """Short missing port error with suggestions."""
        available = sorted(self.in_ports.keys() if direction == "input" else self.out_ports.keys())
        suggestions = self._suggest(port_name, available)
        return (
            f"[E_PORT_NOT_FOUND] {direction} '{port_name}' not found in '{self.instance.name}'. "
            f"Available: {available if available else '(none)'}; Suggest: {suggestions}."
        )

    @staticmethod
    def _suggest(target: str, pool: List[str], n: int = 5) -> str:
        """Return comma list of close matches or a clear placeholder if none."""
        m = difflib.get_close_matches(target, pool, n=n, cutoff=0.6)
        return ", ".join(m) if m else "(no close matches)"

    @staticmethod
    def _describe(connection: Connection) -> str:
        def end(instance: str, kind: str, name: str) -> str:
            return f"{instance}.{kind}.{name}" if instance else f"{kind}.{name}"

        return (
            f"{end(connection.from_instance, connection.from_port_type, connection.from_port_name)} -> "
            f"{end(connection.to_instance, connection.to_port_type, connection.to_port_name)}"
        )

    def _connection_error(self, connection: Connection, message: str) -> ValidationError:
        return ValidationError(
            f"{message}; Connection: {self._describe(connection)}{format_source(connection.source)}",
            source=connection.source,
        )

    def _err_type_mismatch(self, port: InPort | OutPort, attempted_type: str):
        return f"[E_TYPE_MISMATCH] External port '{port.port_path}' type clash: existing={port.msg_type}, new={attempted_type}"

    def _err_missing_port(self, connection: Connection, is_source: bool, candidates: Dict[str, _PortInfo]) -> str:
        """Missing endpoint of an explicit connection, with the ports the endpoint could name."""
        kind = "output" if is_source else "input"
        instance_name = connection.from_instance if is_source else connection.to_instance
        port_name = connection.from_port_name if is_source else connection.to_port_name
        available = sorted(key.split(".", 1)[1] for key in candidates if key.split(".", 1)[0] == instance_name)
        listed = available if available else "(none)"
        if not instance_name:
            return (
                f"[E_EXT_{kind.upper()}_MISSING] External {kind} '{port_name}' not found. Available: {listed}; "
                f"Suggest: {self._suggest(port_name, available)}"
            )
        child = self.instance.children.get(instance_name)
        if child is None:
            children = sorted(self.instance.children)
            return (
                f"[E_INSTANCE_MISSING] Instance '{instance_name}' not found. Available: {children}; "
                f"Suggest: {self._suggest(instance_name, children)}"
            )
        declared = getattr(child.configuration, "inputs" if kind == "input" else "outputs", None) or []
        if child.entity_type == "module" and any(definition.name == port_name for definition in declared):
            return (
                f"[E_PORT_UNCONNECTED] {kind} port '{port_name}' is declared by '{instance_name}' "
                f"but no connection inside the module reaches it"
                f"{format_source(source_from_config(child.configuration, '/connections'))}"
            )
        return (
            f"[E_PORT_MISSING] {kind} port '{port_name}' not in '{instance_name}'. Available: {listed}; "
            f"Suggest: {self._suggest(port_name, available)}"
        )

    # ------------------------------------------------------------------
    # Connection resolution
    # ------------------------------------------------------------------
    def _resolve_ports_for_connection(
        self,
        connection: Connection,
        from_info: _PortInfo,
        to_info: _PortInfo,
    ) -> tuple[OutPort | InPort, OutPort | InPort]:
        """Concrete port objects of a matched connection; a boundary port is created on first use.

        A boundary port is one object shared by every connection that names it.
        """
        from_port = from_info.port
        to_port = to_info.port

        if connection.type == ConnectionType.EXTERNAL_TO_INTERNAL:
            if from_port is None:
                from_port = InPort(
                    from_info.port_name, to_port.msg_type, self.instance.resolved_path, role=connection.from_port_type
                )
            elif from_port.msg_type != to_port.msg_type:
                raise self._connection_error(connection, self._err_type_mismatch(from_port, to_port.msg_type))
        elif connection.type == ConnectionType.INTERNAL_TO_EXTERNAL:
            if to_port is None:
                to_port = OutPort(
                    to_info.port_name, from_port.msg_type, self.instance.resolved_path, role=connection.to_port_type
                )
            elif to_port.msg_type != from_port.msg_type:
                raise self._connection_error(connection, self._err_type_mismatch(to_port, from_port.msg_type))

        from_info.port = from_port
        to_info.port = to_port
        return from_port, to_port

    @staticmethod
    def _filter_candidates(ports: Dict[str, _PortInfo], declared_kind: str, external: bool) -> Dict[str, _PortInfo]:
        """Restrict match candidates to the connection's declared side: boundary vs child, and port kind."""
        filtered = {}
        for key, info in ports.items():
            if (info.instance is None) != external:
                continue
            if info.role is not None and info.role != declared_kind:
                continue
            filtered[key] = info
        return filtered

    def _check_port_kind(self, connection: Connection, info: _PortInfo, declared_kind: str, key: str):
        """Validate that a connection endpoint's declared kind matches the port's actual kind."""
        if info.role is not None and info.role != declared_kind:
            raise ValidationError(
                f"[E_PORT_KIND] Connection declares '{declared_kind}' but port '{key}' is a {info.role}"
                f"{format_source(connection.source)}",
                source=connection.source,
            )

    def _match_connection(
        self,
        connection: Connection,
        sources: Dict[str, _PortInfo],
        targets: Dict[str, _PortInfo],
    ) -> List[Tuple[_PortInfo, _PortInfo]]:
        """Port pairs a connection selects; an explicit endpoint or a wildcard matching nothing is an error."""
        from_key = f"{connection.from_instance}.{connection.from_port_name}"
        to_key = f"{connection.to_instance}.{connection.to_port_name}"

        if connection.has_wildcard:
            pairs = match_and_pair_wildcard_ports(
                from_key,
                to_key,
                self._filter_candidates(sources, connection.from_port_type, connection.from_is_external),
                self._filter_candidates(targets, connection.to_port_type, connection.to_is_external),
            )
            if not pairs:
                raise self._connection_error(connection, "[E_WILDCARD_EMPTY] No ports matched wildcard patterns")
            return [(sources[s], targets[t]) for s, t in pairs]

        from_info = sources.get(from_key)
        if from_info is None:
            raise self._connection_error(connection, self._err_missing_port(connection, True, sources))
        to_info = targets.get(to_key)
        if to_info is None:
            raise self._connection_error(connection, self._err_missing_port(connection, False, targets))
        self._check_port_kind(connection, from_info, connection.from_port_type, from_key)
        self._check_port_kind(connection, to_info, connection.to_port_type, to_key)
        return [(from_info, to_info)]

    def _link_connection(
        self,
        connection: Connection,
        sources: Dict[str, _PortInfo],
        targets: Dict[str, _PortInfo],
    ) -> None:
        """Create the links of one connection; a wildcard whose every match is a global subscriber is an error."""
        pairs = self._match_connection(connection, sources, targets)
        skipped = 0
        for from_info, to_info in pairs:
            from_port, to_port = self._resolve_ports_for_connection(connection, from_info, to_info)

            # A global subscriber keeps its topic; a connection cannot feed it.
            if isinstance(to_port, InPort) and to_port.is_global:
                message = (
                    f"Skipping connection targeting global input port '{to_port.port_path}'; "
                    f"Connection: {self._describe(connection)}{format_source(connection.source)}"
                )
                if connection.has_wildcard:
                    logger.debug(message)
                else:
                    logger.warning(message)
                skipped += 1
                continue

            if connection.type == ConnectionType.INTERNAL_TO_EXTERNAL:
                self._claim_export(connection, from_port, to_port)

            self.links.append(
                Link(from_port.msg_type, from_port, to_port, self.instance.resolved_path, connection.type)
            )
            if connection.type == ConnectionType.EXTERNAL_TO_INTERNAL:
                self.in_ports.setdefault(from_port.name, from_port)
            elif connection.type == ConnectionType.INTERNAL_TO_EXTERNAL:
                self.out_ports.setdefault(to_port.name, to_port)

        if connection.has_wildcard and skipped == len(pairs):
            raise self._connection_error(
                connection, "[E_WILDCARD_PRESET_INPUT] Every port the wildcard matched is a global input port"
            )

    def _claim_export(self, connection: Connection, from_port: OutPort, to_port: OutPort) -> None:
        """A boundary output names the topic of its node port, so a node port leaves through one."""
        node_port = from_port.get_reference_list()[0]
        claimed = self._exports.setdefault(id(node_port), to_port)
        if claimed is not to_port:
            raise self._connection_error(
                connection,
                f"[E_EXPORT_TWICE] '{node_port.port_path}' is already exported as '{claimed.name}'; "
                f"a publisher has one topic, so it leaves '{self.instance.name}' through one output",
            )

    def _check_duplicate_connections(self, connection_list: List[Connection]) -> None:
        """Each pair of endpoints is connected once."""
        seen = set()
        for conn in connection_list:
            signature = self._describe(conn)
            if signature in seen:
                file_path = getattr(self.instance.configuration, "file_path", "unknown")
                cfg_src = source_from_config(self.instance.configuration, "/connections")
                raise ValidationError(
                    f"[E_DUPLICATE_CONNECTION] Duplicate connection found: {signature} (type={conn.type.name}). "
                    f"At {file_path}{format_source(cfg_src)}"
                )
            seen.add(signature)

    def _collect_candidates(self) -> Tuple[Dict[str, _PortInfo], Dict[str, _PortInfo]]:
        """Ports a connection can name as source and as target, keyed '<instance>.<port>'.

        A boundary port has an empty instance name and no port object until a connection uses it.
        """
        sources: Dict[str, _PortInfo] = {}
        targets: Dict[str, _PortInfo] = {}
        for child_key, child in self.instance.children.items():
            for name, port in child.link_manager.out_ports.items():
                sources[f"{child_key}.{name}"] = _PortInfo(name, child, port, port.role)
            for name, port in child.link_manager.in_ports.items():
                targets[f"{child_key}.{name}"] = _PortInfo(name, child, port, port.role)

        configuration = self.instance.configuration
        for definition in getattr(configuration, "inputs", None) or []:
            sources[f".{definition.name}"] = _PortInfo(definition.name, None, None, definition.port_role)
        for definition in getattr(configuration, "outputs", None) or []:
            targets[f".{definition.name}"] = _PortInfo(definition.name, None, None, definition.port_role)
        return sources, targets

    def set_links(self):
        """Link the children along the entity's connections and create the boundary ports they use."""
        configuration = self.instance.configuration
        connection_list = [
            Connection(cfg, source=source_from_config(configuration, f"/connections/{idx}"))
            for idx, cfg in enumerate(configuration.connections or [])
        ]
        if not connection_list and not getattr(configuration, "remaps", None):
            cfg_src = source_from_config(configuration, "/connections")
            logger.warning(
                f"{self.instance.entity_type.capitalize()} '{self.instance.name}' has no connections configured"
                f"{format_source(cfg_src)}"
            )
        self._check_duplicate_connections(connection_list)

        sources, targets = self._collect_candidates()

        # A remapped port keeps its topic when a link exports it.
        self.apply_remaps()

        for connection in connection_list:
            self._link_connection(connection, sources, targets)

        self._bind_interface_topics()

    def apply_remaps(self):
        """Set the topics of child out-ports from the system's remap entries.

        Only out-port types (publisher, server) may be remapped. A remap overrides a
        node-level global topic and reaches the publishing node port through the
        reference chain, so the launch remap of the node carries the topic.
        """
        remaps = getattr(self.instance.configuration, "remaps", None)
        if not remaps:
            return
        for entry in remaps:
            parts = entry.source.split(".")
            if len(parts) != 3:
                raise ValidationError(
                    f"[E_REMAP_SOURCE] Invalid remap source '{entry.source}': "
                    "expected format '{instance}.{port_type}.{port_name}'"
                )
            instance_name, port_type, port_name = parts

            child_instance = self.instance.children.get(instance_name)
            if child_instance is None:
                raise ValidationError(
                    f"[E_REMAP_INSTANCE] Remap source instance '{instance_name}' not found. "
                    f"Available: {list(self.instance.children.keys())}"
                )

            if port_type not in ("publisher", "server"):
                raise ValidationError(
                    f"[E_REMAP_PORT_TYPE] Invalid port type '{port_type}' in remap source "
                    f"'{entry.source}'. Only out-port types are allowed: publisher, server"
                )
            port = child_instance.link_manager.out_ports.get(port_name)

            if port is None:
                available = list(child_instance.link_manager.out_ports.keys())
                raise ValidationError(
                    f"[E_REMAP_PORT] Port '{port_name}' not found in {port_type} ports of "
                    f"'{instance_name}'. Available: {available}"
                )

            if port.role is not None and port.role != port_type:
                raise ValidationError(
                    f"[E_REMAP_PORT_TYPE] Remap source '{entry.source}' declares {port_type} "
                    f"but port '{port_name}' is a {port.role}"
                )

            topic_parts = _topic_segments(entry.topic)
            if topic_parts is None:
                raise ValidationError(
                    f"[E_REMAP_TOPIC] Remap topic '{entry.topic}' must be an absolute ROS topic "
                    "without empty segments (e.g. '/foo/bar')"
                )
            self._force_remap_port(port, topic_parts)

    def _bind_interface_topics(self):
        """Bind the boundary ports that declare a topic; such a port is used by a connection.

        A bound output (publisher, server) sets the topic of the node port it exports, except a
        global one, which keeps its topic. A bound input (subscriber, client) sets the topic of
        every node port it stands for. A topic is bound by one boundary port unless it is global.
        """
        bound: Dict[str, List[Tuple[str, bool]]] = {}
        configuration = self.instance.configuration
        declared = (
            (getattr(configuration, "inputs", None) or [], self.in_ports),
            (getattr(configuration, "outputs", None) or [], self.out_ports),
        )
        for definitions, ports in declared:
            for definition in definitions:
                if definition.topic is None:
                    continue
                port = ports.get(definition.name)
                if port is None:
                    source = source_from_config(configuration, f"/{definition.port_role}s")
                    raise ValidationError(
                        f"[E_INTERFACE_UNCONNECTED] {definition.port_role} interface port '{definition.name}' "
                        f"has no connection{format_source(source)}",
                        source=source,
                    )
                topic_parts = _topic_segments(definition.topic)
                label = f"{definition.port_role} '{definition.name}'"
                is_global = False
                if isinstance(port, OutPort):
                    node_port = port.get_reference_list()[0]
                    is_global = node_port.is_global
                    if not is_global:
                        self._force_remap_port(port, topic_parts)
                    elif node_port.own_topic != topic_parts:
                        raise ValidationError(
                            f"[E_INTERFACE_GLOBAL] {label} exports the global '{node_port.port_path}' "
                            f"on '{node_port.get_topic()}'; a global port keeps its topic, so the interface "
                            f"names '{node_port.get_topic()}', not '{definition.topic}'"
                        )
                else:
                    self._bind_input_topic(port, topic_parts)
                bound.setdefault(definition.topic, []).append((label, is_global))

        for topic, ports in bound.items():
            if len(ports) > 1 and not all(is_global for _, is_global in ports):
                raise ValidationError(
                    f"[E_INTERFACE_TOPIC_SHARED] topic '{topic}' is bound by {', '.join(l for l, _ in ports)}; "
                    "a topic is bound by one interface port unless every port on it is global"
                )

    @staticmethod
    def _bind_input_topic(port: InPort, topic_parts: List[str]) -> None:
        for chained in port.get_chain_list():
            if chained.is_remapped and chained.own_topic and chained.own_topic != topic_parts:
                raise ValidationError(
                    f"[E_INTERFACE_TOPIC] '{chained.port_path}' is bound to '{chained.get_topic()}' and to "
                    f"'/{'/'.join(topic_parts)}' (interface port '{port.name}'); a port takes one topic"
                )
            chained.is_remapped = True
            chained.set_topic(topic_parts[:-1], topic_parts[-1])

    @staticmethod
    def _force_remap_port(port: OutPort, topic_parts: list[str]):
        """Force-set a remapped topic on an OutPort and mark its whole reference chain remapped.

        set_topic() sets the topic on the publishing node port; subscribers resolve it from there.
        """
        port.is_remapped = True
        for ref_port in port.reference:
            LinkManager._force_remap_port(ref_port, topic_parts)
        port.set_topic(topic_parts[:-1], topic_parts[-1])

    def initialize_node_ports(self):
        """Create the ports a node design declares; a global port holds its topic from the start."""
        configuration = self.instance.configuration
        for port_cls, definitions, ports in (
            (InPort, configuration.inputs, self.in_ports),
            (OutPort, configuration.outputs, self.out_ports),
        ):
            for definition in definitions:
                port = port_cls(
                    definition.name,
                    definition.message_type,
                    self.instance.resolved_path,
                    remap_target=definition.remap_target,
                    role=definition.port_role,
                )
                if definition.global_topic is not None:
                    port.is_global = True
                    topic = definition.global_topic.lstrip("/").split("/")
                    port.set_topic(topic[:-1], topic[-1])
                ports[definition.name] = port

    def get_input_events(self):
        """Get input events from all input ports."""
        return [in_port.event for in_port in self.in_ports.values()]

    def get_output_events(self):
        """Get output events from all output ports."""
        return [out_port.event for out_port in self.out_ports.values()]

    def check_ports(self):
        """A node subscriber or client takes one topic; every port connected to it resolves to that topic."""
        if self.instance.entity_type != "node":
            return

        for in_port in self.in_ports.values():
            sources: Dict[str, Port] = {}
            for server_port in in_port.servers:
                sources.setdefault(server_port.get_topic(), server_port)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"In port '{in_port.port_path}' topic '{in_port.get_topic()}' sources {sorted(sources)}")
            kind = in_port.role or "input"
            if in_port.own_topic:
                # The port holds a topic of its own: a global one, or one bound by a subsystem interface.
                fixed = "/" + "/".join(in_port.own_topic)
                others = {topic: port for topic, port in sources.items() if topic != fixed}
                if others:
                    raise ValidationError(
                        f"[E_TOPIC_CONFLICT] {kind} '{in_port.port_path}' is bound to topic '{fixed}' "
                        f"and connected to:{self._list_sources(others)}"
                    )
                continue
            if len(sources) > 1:
                raise ValidationError(
                    f"[E_TOPIC_CONFLICT] {kind} '{in_port.port_path}' is connected on {len(sources)} topics, "
                    f"and a port takes one topic:{self._list_sources(sources)}\n"
                    "Connect one source, or give the sources one topic with 'remaps'."
                )

    @staticmethod
    def _list_sources(sources: Dict[str, Port]) -> str:
        return "".join(f"\n  {port.port_path} on '{topic}'" for topic, port in sorted(sources.items()))

    def log_module_configuration(self):
        """Log module configuration details."""
        logger.debug(f"Instance '{self.instance.name}' module configuration: {len(self.links)} links established")
        for link in self.links:
            logger.debug(f"  Link: {link.from_port.port_path} -> {link.to_port.port_path}")
        # new ports
        for in_port in self.in_ports.values():
            logger.debug(f"  New in port: {in_port.port_path}")
        for out_port in self.out_ports.values():
            logger.debug(f"  New out port: {out_port.port_path}")

    def get_all_in_ports(self):
        """Get all input ports."""
        return list(self.in_ports.values())

    def get_all_out_ports(self):
        """Get all output ports."""
        return list(self.out_ports.values())

    def get_all_remap_ports(self) -> List[LauncherPortData]:
        """Get all ports for topic remapping."""
        ports: List[LauncherPortData] = []

        for port in self.in_ports.values():
            if port.get_topic() == "":
                continue
            ports.append(
                {
                    "name": port.name,
                    "topic": port.get_topic(),
                    "remap_target": port.remap_target,
                }
            )

        for port in self.out_ports.values():
            ports.append(
                {
                    "name": port.name,
                    "topic": port.get_topic(),
                    "remap_target": port.remap_target,
                }
            )
        return ports

    def get_all_links(self):
        """Get all links."""
        return self.links
