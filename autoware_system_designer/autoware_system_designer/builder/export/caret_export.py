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


"""CARET architecture export: reserved seam, not implemented.

CARET is the measurement path of the sequence diagram's latency view. The
design data already holds what a CARET architecture description needs:

* ``processes[]`` of a node → callbacks of that node
* ``in_ports`` / ``out_ports`` and the links between them → publish/subscribe
  communications
* each clock root's chain to the terminals it reaches → CARET target paths

Measured results come back as ``visualizer.latency_source`` files keyed by
node path and process or topic. Neither direction is wired into the export
pipeline yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from autoware_system_designer.builder.instances import Instance


def export_caret_architecture(instance: "Instance") -> Dict[str, Any]:
    """Build the CARET architecture description of one deployed system.

    Args:
        instance: Root of the deployed instance tree.

    Raises:
        NotImplementedError: always; the mapping is documented above and awaits an implementation.
    """
    raise NotImplementedError("CARET architecture export is not implemented")
