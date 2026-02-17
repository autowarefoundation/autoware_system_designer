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

from typing import Any, Dict, Iterable, List, Optional


def get_endpoint_entity_name(endpoint: Any) -> Optional[str]:
    """Extract the entity name from an endpoint like "entity.port"."""
    if not isinstance(endpoint, str):
        return None
    if not endpoint:
        return None
    return endpoint.split(".", 1)[0]


def filter_connections_by_removed_entities(
    connections: List[Dict[str, Any]] | None,
    removed_entities: Iterable[str],
) -> List[Dict[str, Any]]:
    """Remove connections whose 'from'/'to' endpoints reference removed entities."""
    removed_set = {name for name in removed_entities if isinstance(name, str) and name}
    if not connections:
        return []
    if not removed_set:
        return connections

    return [
        conn
        for conn in connections
        if get_endpoint_entity_name(conn.get("from")) not in removed_set
        and get_endpoint_entity_name(conn.get("to")) not in removed_set
    ]
