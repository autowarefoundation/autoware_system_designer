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

from __future__ import annotations

import os
from typing import Any, Dict, Iterator, List, Tuple

from ..file_io.system_structure_json import extract_system_structure_data, load_system_structure


def _iter_mode_payload_and_data(
    mode_keys: List[str],
    system_structure_dir: str,
) -> Iterator[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Yield (mode_key, payload, extracted_data) for each mode."""

    for mode_key in mode_keys:
        structure_path = os.path.join(system_structure_dir, f"{mode_key}.json")
        payload = load_system_structure(structure_path)
        data, _ = extract_system_structure_data(payload)
        yield mode_key, payload, data
