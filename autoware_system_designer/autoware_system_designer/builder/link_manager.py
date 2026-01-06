# Copyright 2025 TIER IV, inc.
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

import logging
import fnmatch
import re
import difflib
from typing import Any, Dict, List, TYPE_CHECKING

from ..models.ports import InPort, OutPort
from ..models.links import Link, Connection, ConnectionType
from ..exceptions import ValidationError

if TYPE_CHECKING:
    from .instances import Instance

logger = logging.getLogger(__name__)


def match_and_pair_wildcard_ports(
    source_pattern: str,
    target_pattern: str,
    source_ports: Dict[str, Any],
    target_ports: Dict[str, Any],
) -> List[tuple[str, str]]:
    """Return (source_key, target_key) pairs honoring wildcard usage.

    Rules:
    - Both '*' => intersect identical keys.
    - One side has '*' => cartesian with other side's concrete match(es).
    - Both sides have '*' (not both '*') => substitute source captures into target pattern.
    """
    def _match(pattern: str, keys: List[str]) -> List[str]:
        """Match keys against a pattern that may contain wildcards (*, ^, +).
        
        This treats * ^ + as distinct wildcards, but functionally they all match
        sequences of characters. Using different wildcards allows for specific
        multi-capture matching in the substitution phase.
        """
        # Create regex where each wildcard type captures a group
        regex_pattern = re.escape(pattern)
        regex_pattern = regex_pattern.replace(r"\*", "(.*?)")
        regex_pattern = regex_pattern.replace(r"\^", "(.*?)") 
        regex_pattern = regex_pattern.replace(r"\+", "(.*?)")
        
        matches = []
        for key in keys:
            if re.match(f"^{regex_pattern}$", key):
                matches.append(key)
        return matches

    src_matches = _match(source_pattern, list(source_ports.keys()))
    tgt_matches = _match(target_pattern, list(target_ports.keys()))
    if not src_matches or not tgt_matches:
        return []

    # Check for wildcard presence
    src_wc = any(c in source_pattern for c in "*^+")
    tgt_wc = any(c in target_pattern for c in "*^+")
    
    # Simple case: both patterns are purely just one wildcard character (e.g. both "*")
    if source_pattern in ["*", "^", "+"] and target_pattern in ["*", "^", "+"]:
        common = sorted(set(src_matches) & set(tgt_matches))
        return [(k, k) for k in common]

    pairs: List[tuple[str, str]] = []

    if src_wc and not tgt_wc:  # replicate target across each source expansion
        for s in sorted(src_matches):
            for t in tgt_matches:
                if s != t:
                    pairs.append((s, t))
        return pairs
    if tgt_wc and not src_wc:  # replicate source across each target expansion
        for s in src_matches:
            for t in sorted(tgt_matches):
                if s != t:
                    pairs.append((s, t))
        return pairs

    # Both sides have wildcards (non-trivial); use substitution logic.
    for s in src_matches:
        t = _apply_wildcard_substitution(source_pattern, target_pattern, s)
        if t in tgt_matches:
            pairs.append((s, t))
    return pairs


def _apply_wildcard_substitution(source_pattern: str, target_pattern: str, matched_name: str) -> str:
    """Substitute wildcard captures from a matched source key into target pattern.
    
    Wildcards (*, ^, +) are treated as distinct placeholders. Captures from 
    wildcards in the source pattern are mapped to the corresponding wildcard 
    type in the target pattern.
    
    Example 1: 
      source: "input.*_^" matched against "input.foo_bar"
      target: "^.input.*"
      result: "bar.input.foo"

    Example 2:
      source: "input.*_^" matched against "input.pcl_upper_right"
      target: "^.input.*"
      result: "upper_right.input.pcl"
    """
    # 1. Extract captures from source pattern
    # Convert wildcards to named groups to track which type matched what
    # We use a simple sequential extraction since regex groups are ordered
    
    # Build source regex
    source_regex_parts = []
    wildcard_order = [] # stores type of wildcard encountered: '*', '^', or '+'
    
    last_idx = 0
    # Iterate through pattern to find wildcards
    # Using a simple parser loop because we need to preserve order and type
    i = 0
    while i < len(source_pattern):
        if source_pattern[i] in "*^+":
            # Add preceding literal text
            if i > last_idx:
                source_regex_parts.append(re.escape(source_pattern[last_idx:i]))
            # Add capture group
            source_regex_parts.append("(.*?)")
            wildcard_order.append(source_pattern[i])
            last_idx = i + 1
        i += 1
    if last_idx < len(source_pattern):
        source_regex_parts.append(re.escape(source_pattern[last_idx:]))
        
    source_regex = "^" + "".join(source_regex_parts) + "$"
    match = re.match(source_regex, matched_name)
    
    if not match:
        return matched_name
        
    captures = match.groups()
    if len(captures) != len(wildcard_order):
        return matched_name # Should match if regex worked

    # Map wildcard type to its captured value(s)
    # Since a type can appear multiple times, we use a list/iterator approach
    wildcard_captures = {
        '*': [],
        '^': [],
        '+': []
    }
    for wc_type, captured_val in zip(wildcard_order, captures):
        wildcard_captures[wc_type].append(captured_val)

    # 2. Substitute into target pattern
    result_parts = []
    last_idx = 0
    i = 0
    
    # We need to track index for each wildcard type in target to consume correct capture
    wc_indices = {'*': 0, '^': 0, '+': 0}
    
    while i < len(target_pattern):
        char = target_pattern[i]
        if char in "*^+":
            # Add preceding literal text
            if i > last_idx:
                result_parts.append(target_pattern[last_idx:i])
            
            # Find substitution value
            if wc_indices[char] < len(wildcard_captures[char]):
                result_parts.append(wildcard_captures[char][wc_indices[char]])
                wc_indices[char] += 1
            else:
                # If target has more wildcards of a type than source, leave it as is?
                # Or simplistic behavior: reuse last or empty? 
                # Standard behavior: leave the wildcard char if no capture available (unlikely if patterns align)
                result_parts.append(char)
            
            last_idx = i + 1
        i += 1
        
    if last_idx < len(target_pattern):
        result_parts.append(target_pattern[last_idx:])
        
    return "".join(result_parts)


class LinkManager:
    """Manages port, connection, and link operations for Instance objects."""

    def __init__(self, instance: 'Instance'):
        self.instance = instance
        self.in_ports: Dict[str, InPort] = {}
        self.out_ports: Dict[str, OutPort] = {}
        self.links: List[Link] = []

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

    # ------------------------------------------------------------------
    # Centralized error helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _suggest(target: str, pool: List[str], n: int = 5) -> str:
        """Return comma list of close matches or '-' if none."""
        m = difflib.get_close_matches(target, pool, n=n, cutoff=0.6)
        return ", ".join(m) if m else "-"

    def _err_external_decl(self, kind: str, name: str, declared: List[str]):
        return (
            f"[E_EXT_DECL] External {kind} '{name}' not declared. Declared: {declared if declared else '(none)'}; "
            f"Suggest: {self._suggest(name, declared)}"
        )

    def _err_type_mismatch(self, port: InPort | OutPort, attempted_type: str):
        return (
            f"[E_TYPE_MISMATCH] External port '{port.port_path}' type clash: existing={port.msg_type}, new={attempted_type}"
        )

    def _err_wildcard_no_matches(self, connection: Connection):
        return (
            "[E_WILDCARD_EMPTY] No ports matched wildcard patterns. "
            f"From='{connection.from_instance}.{connection.from_port_name}' To='{connection.to_instance}.{connection.to_port_name}'"
        )

    def _err_missing_external_io(self, kind: str, name: str, available: List[str]):
        return (
            f"[E_EXT_{kind.upper()}_MISSING] External {kind} '{name}' not found. Available: {available if available else '(none)'}; "
            f"Suggest: {self._suggest(name, available)}"
        )

    def _err_missing_internal(self, role: str, instance_name: str, port_name: str, available: List[str]):
        return (
            f"[E_PORT_MISSING] {role} port '{port_name}' not in '{instance_name}'. Available: {available if available else '(none)'}; "
            f"Suggest: {self._suggest(port_name, available)}"
        )

    def _register_external_port(self, port_dict: Dict[str, Any], port_obj: InPort | OutPort, kind: str):
        """Generic logic for adding/updating an external port.

        kind: 'input' or 'output'.
        """
        cfg_list = self.instance.configuration.external_interfaces.get(kind, [])
        declared_names = {item.get("name") for item in cfg_list}
        if port_obj.name not in declared_names:
            raise ValidationError(self._err_external_decl(kind, port_obj.name, sorted(declared_names)))

        existing = port_dict.get(port_obj.name)
        if existing:
            if existing.msg_type != port_obj.msg_type:
                raise ValidationError(self._err_type_mismatch(existing, port_obj.msg_type))
            existing.set_references(port_obj.reference)
        else:
            port_dict[port_obj.name] = port_obj

    def set_in_port(self, in_port: InPort):  # external interface only
        self._register_external_port(self.in_ports, in_port, "input")

    def set_out_port(self, out_port: OutPort):  # external interface only
        self._register_external_port(self.out_ports, out_port, "output")

    def _create_link_from_ports(self, from_port, to_port, connection_type: ConnectionType):
        """Create and append a link between two ports.
        
        Args:
            from_port: Source port (InPort or OutPort)
            to_port: Destination port (InPort or OutPort)
            connection_type: Type of connection
        """
        link = Link(from_port.msg_type, from_port, to_port, self.instance.namespace, connection_type)
        self.links.append(link)

    def _resolve_ports_for_connection(
        self,
        connection: Connection,
        from_info: Dict[str, Any] | None,
        to_info: Dict[str, Any] | None,
    ) -> tuple[OutPort | InPort, OutPort | InPort]:
        """Resolve concrete port objects for a connection, creating externals when needed."""

        def _existing_port(info: Dict[str, Any] | None, accessor) -> OutPort | InPort | None:
            if not info:
                return None
            port = info.get("port")
            if port is not None:
                return port
            instance = info.get("instance")
            if instance is None:
                return None
            return accessor(instance.link_manager, info["port_name"])

        from_port = _existing_port(from_info, lambda lm, name: lm.get_out_port(name))
        to_port = _existing_port(to_info, lambda lm, name: lm.get_in_port(name))

        if connection.type == ConnectionType.EXTERNAL_TO_INTERNAL:
            if to_port is None:
                raise ValidationError(
                    f"[E_CONN_TARGET_MISSING] EXTERNAL_TO_INTERNAL input.{connection.from_port_name} -> {connection.to_instance}.input.{connection.to_port_name}"
                )
            port_name = (from_info or {}).get("port_name", connection.from_port_name)
            from_port = InPort(port_name, to_port.msg_type, self.instance.namespace)
        elif connection.type == ConnectionType.INTERNAL_TO_EXTERNAL:
            if from_port is None:
                raise ValidationError(
                    f"[E_CONN_SOURCE_MISSING] INTERNAL_TO_EXTERNAL {connection.from_instance}.output.{connection.from_port_name} -> output.{connection.to_port_name}"
                )
            port_name = (to_info or {}).get("port_name", connection.to_port_name)
            to_port = OutPort(port_name, from_port.msg_type, self.instance.namespace)
            
        if from_info is not None:
            from_info["port"] = from_port
        if to_info is not None:
            to_info["port"] = to_port

        return from_port, to_port

    def _create_wildcard_links(
        self,
        connection: Connection,
        port_list_from: Dict[str, Dict[str, Any]],
        port_list_to: Dict[str, Dict[str, Any]],
    ):
        """Create links for wildcard connections.
        
        Args:
            connection: Connection configuration
            port_list_from: Mapping of source port keys to metadata dictionaries
            port_list_to: Mapping of target port keys to metadata dictionaries
        """
        # filter the port name lists only for target instance
        from_idx = f"{connection.from_instance}.{connection.from_port_name}"
        to_idx = f"{connection.to_instance}.{connection.to_port_name}"

        # Match and pair ports based on wildcard patterns
        port_pairs = match_and_pair_wildcard_ports(
            from_idx,
            to_idx,
            port_list_from,
            port_list_to,
        )
        
        # Validate matched ports
        if not port_pairs:
            logger.warning(self._err_wildcard_no_matches(connection))
            return

        # Create links for each matched pair
        for from_key, to_key in port_pairs:
            from_info = port_list_from.get(from_key)
            to_info = port_list_to.get(to_key)

            if from_info is None or to_info is None:
                raise ValidationError(f"[E_WILDCARD_META] Missing metadata for {from_key} -> {to_key} (internal bug)")

            from_port, to_port = self._resolve_ports_for_connection(connection, from_info, to_info)

            self._create_link_from_ports(from_port, to_port, connection.type)


    def _check_and_deduplicate_connections(self, connection_list: List[Connection]) -> List[Connection]:
        """Check for duplicate connections and deduplicate if identical, error if conflicting.
        
        Args:
            connection_list: List of Connection objects to check
            
        Returns:
            Deduplicated list of Connection objects
            
        Raises:
            ValidationError: If duplicate connections are found that differ in some way
        """
        def _format_connection_string(conn: Connection) -> str:
            """Format connection for display in error messages."""
            if conn.from_instance == "":
                from_str = f"input.{conn.from_port_name}"
            else:
                from_str = f"{conn.from_instance}.output.{conn.from_port_name}"
            
            if conn.to_instance == "":
                to_str = f"output.{conn.to_port_name}"
            else:
                to_str = f"{conn.to_instance}.input.{conn.to_port_name}"
            
            return f"{from_str} -> {to_str}"
        
        seen_connections: Dict[str, Connection] = {}  # key: connection signature, value: first occurrence
        duplicate_indices: List[int] = []  # indices of duplicates to remove
        
        for idx, conn in enumerate(connection_list):
            # Create a signature for the connection (endpoints only)
            conn_signature = f"{conn.from_instance}.{conn.from_port_name} -> {conn.to_instance}.{conn.to_port_name}"
            
            if conn_signature in seen_connections:
                conn_str = _format_connection_string(conn)
                file_path = getattr(self.instance.configuration, 'file_path', 'unknown')
                raise ValidationError(f"[E_DUPLICATE_CONNECTION] Duplicate connection found: {conn_str} (type={conn.type.name}). At {file_path}")
            else:
                seen_connections[conn_signature] = conn
        
        # Return deduplicated list (keep first occurrence, remove duplicates)
        return [conn for idx, conn in enumerate(connection_list) if idx not in duplicate_indices]

    def set_links(self):
        """Set up links based on entity connections."""
        connection_list: List[Connection] = [Connection(cfg) for cfg in self.instance.configuration.connections]
        if len(connection_list) == 0:
            logger.warning(f"Module '{self.instance.name}' has no connections configured, at {self.instance.configuration.file_path}")
            return
        
        # Check for and deduplicate duplicate connections
        connection_list = self._check_and_deduplicate_connections(connection_list)

        # dictionary of ports, having field of instance, port-name, port-type
        port_list_from: Dict[str, Dict[str, Any]] = {}
        port_list_to: Dict[str, Dict[str, Any]] = {}

        # ports from children instances
        for child_instance in self.instance.children.values():
            for port_name, port in child_instance.link_manager.in_ports.items():
                idx = f"{child_instance.name}.{port_name}"
                port_list_to[idx] = {"instance": child_instance, "port_name": port_name, "port": port}
            for port_name, port in child_instance.link_manager.out_ports.items():
                idx = f"{child_instance.name}.{port_name}"
                port_list_from[idx] = {"instance": child_instance, "port_name": port_name, "port": port}
        # ports from external interfaces
        external_interfaces = getattr(self.instance.configuration, "external_interfaces", None)
        if isinstance(external_interfaces, dict):
            for ext_input in external_interfaces.get("input", []):
                port_name = ext_input.get("name")
                idx = f".{port_name}"
                port_list_from[idx] = {"instance": None, "port_name": port_name, "port": None}
            for ext_output in external_interfaces.get("output", []):
                port_name = ext_output.get("name")
                idx = f".{port_name}"
                port_list_to[idx] = {"instance": None, "port_name": port_name, "port": None}

        # Establish links based on connection type
        for connection in connection_list:
            wildcard_fields = [
                connection.from_port_name or "",
                connection.to_port_name or "",
                connection.from_instance or "",
                connection.to_instance or "",
            ]
            has_wildcard = any(c in field for field in wildcard_fields for c in "*^+")
            if has_wildcard:
                self._create_wildcard_links(connection, port_list_from, port_list_to)
            else:
                # set from_port and to_port
                from_key = f"{connection.from_instance}.{connection.from_port_name}"
                to_key = f"{connection.to_instance}.{connection.to_port_name}"
                from_info = port_list_from.get(from_key)
                to_info = port_list_to.get(to_key)

                # intuitive error if missing
                if from_info is None:
                    # external input case uses empty instance name
                    if connection.type == ConnectionType.EXTERNAL_TO_INTERNAL:
                        missing_name = connection.from_port_name
                        available = sorted([
                            k.split(".")[1] for k in port_list_from.keys() if k.startswith(".")
                        ])
                        msg = self._err_missing_external_io("input", missing_name, available)
                    else:
                        # internal output missing
                        instance_name = connection.from_instance or "<root>"
                        available = sorted([
                            k.split(".")[1] for k in port_list_from.keys() if k.startswith(f"{instance_name}.")
                        ])
                        msg = self._err_missing_internal("output", instance_name, connection.from_port_name, available)

                    if self.instance.entity_type == "module":
                        raise ValidationError(msg)
                    logger.warning(msg)
                    continue

                if to_info is None:
                    if connection.type == ConnectionType.INTERNAL_TO_EXTERNAL:
                        missing_name = connection.to_port_name
                        available = sorted([
                            k.split(".")[1] for k in port_list_to.keys() if k.startswith(".")
                        ])
                        msg = self._err_missing_external_io("output", missing_name, available)
                    else:
                        instance_name = connection.to_instance or "<root>"
                        available = sorted([
                            k.split(".")[1] for k in port_list_to.keys() if k.startswith(f"{instance_name}.")
                        ])
                        msg = self._err_missing_internal("input", instance_name, connection.to_port_name, available)

                    if self.instance.entity_type == "module":
                        raise ValidationError(msg)
                    logger.warning(msg)
                    continue

                from_port, to_port = self._resolve_ports_for_connection(connection, from_info, to_info)
                self._create_link_from_ports(from_port, to_port, connection.type)

        # Create external ports after links are set
        self._create_external_ports()

    def _create_external_ports(self):
        """Create external ports based on link list."""
        for link in self.links:
            if link.from_port.namespace == self.instance.namespace:
                self.set_in_port(link.from_port)
            if link.to_port.namespace == self.instance.namespace:
                self.set_out_port(link.to_port)

    def initialize_node_ports(self):
        """Initialize ports for node entity during node configuration."""
        if self.instance.entity_type != "node":
            return
            
        # set in_ports
        for cfg_in_port in self.instance.configuration.inputs:
            in_port_name = cfg_in_port.get("name")
            in_port_msg_type = cfg_in_port.get("message_type")
            in_port_instance = InPort(in_port_name, in_port_msg_type, self.instance.namespace, remap_target=cfg_in_port.get("remap_target"))
            if "global" in cfg_in_port:
                in_port_instance.is_global = True
                topic = cfg_in_port.get("global")
                if topic[0] == "/":
                    topic = topic[1:]
                in_port_instance.topic = topic.split("/")
            self.in_ports[in_port_name] = in_port_instance

        # set out_ports
        for cfg_out_port in self.instance.configuration.outputs:
            out_port_name = cfg_out_port.get("name")
            out_port_msg_type = cfg_out_port.get("message_type")
            out_port_instance = OutPort(out_port_name, out_port_msg_type, self.instance.namespace, remap_target=cfg_out_port.get("remap_target"))
            if "global" in cfg_out_port:
                out_port_instance.is_global = True
                topic = cfg_out_port.get("global")
                if topic[0] == "/":
                    topic = topic[1:]
                out_port_instance.topic = topic.split("/")
            self.out_ports[out_port_name] = out_port_instance

    def get_input_events(self):
        """Get input events from all input ports."""
        return [in_port.event for in_port in self.in_ports.values()]

    def get_output_events(self):
        """Get output events from all output ports."""
        return [out_port.event for out_port in self.out_ports.values()]

    def check_ports(self):
        """Check and debug port configurations."""
        # check ports only for node. in case of module, the check is done
        if self.instance.entity_type != "node":
            return

        for out_port in self.out_ports.values():
            logger.debug(
                f"[PORT_USER_DEBUG] port path: '{out_port.port_path}' topic: '{out_port.get_topic()}' (users={len(out_port.users)})"
            )

        # check ports
        for in_port in self.in_ports.values():
            logger.debug(f"  In port: {in_port.port_path}")
            logger.debug(f"    Subscribing topic: {in_port.topic}")
            server_port_list = in_port.servers
            if server_port_list == []:
                logger.debug("    Server port not found")
                continue
            for server_port in server_port_list:
                logger.debug(f"    server: {server_port.port_path}, topic: {server_port.topic}")

        for out_port in self.out_ports.values():
            logger.debug(f"  Out port: {out_port.port_path}")
            user_port_list = out_port.users
            if user_port_list == []:
                logger.debug("    User port not found")
                continue
            for user_port in user_port_list:
                logger.debug(f"    user: {user_port.port_path}")

    def log_module_configuration(self):
        """Log module configuration details."""
        logger.debug(
            f"Instance '{self.instance.name}' module configuration: {len(self.links)} links established"
        )
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

    def get_all_links(self):
        """Get all links."""
        return self.links
