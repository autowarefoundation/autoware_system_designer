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

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Union


def _get_endpoint_entity_name(endpoint: Any) -> Optional[str]:
    """Extract the entity name from an endpoint like "entity.port"."""
    if not isinstance(endpoint, str):
        return None
    if not endpoint:
        return None
    return endpoint.split(".", 1)[0]


def _connection_to_list(conn: Union[List[Any], tuple, Dict[str, Any]]) -> Optional[List[Any]]:
    """Convert a connection (list of 2 or dict with 2 values) to a 2-element list."""
    if isinstance(conn, dict):
        values = list(conn.values())
        if len(values) != 2:
            return None
        return list(values)
    if isinstance(conn, (list, tuple)) and len(conn) == 2:
        return list(conn)
    return None


def filter_connections_by_removed_entities(
    connections: List[Union[List[Any], Dict[str, Any]]] | None,
    removed_entities: Iterable[str],
) -> List[List[Any]]:
    """Remove connections whose endpoints reference removed entities.

    Accepts connections as list-of-lists or list-of-dicts (any two keys).
    Returns a list of 2-element lists (key-value form is flattened to list).
    """
    removed_set = {name for name in removed_entities if isinstance(name, str) and name}
    if not connections:
        return []
    if not removed_set:
        return [_connection_to_list(c) for c in connections if _connection_to_list(c) is not None]

    result: List[List[Any]] = []
    for conn in connections:
        pair = _connection_to_list(conn)
        if pair is None:
            continue
        if (
            _get_endpoint_entity_name(pair[0]) not in removed_set
            and _get_endpoint_entity_name(pair[1]) not in removed_set
        ):
            result.append(pair)
    return result
