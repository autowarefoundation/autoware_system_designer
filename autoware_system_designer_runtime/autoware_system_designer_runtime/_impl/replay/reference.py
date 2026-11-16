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

"""Reference outputs of a replay: where they are kept and what names them.

A reference belongs to one combination of export, deploy arguments, scope, bag and
parameters; the key is a hash over all of them, so a change in any starts a new
reference::

    <record root>/<package>/<system>/replay/<Mode>/<key>/{reference.json, outputs/}

Plain Python with no ROS import.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ...records import export_identity, system_record_dir

REPLAY_KIND = "replay"
OUTPUTS_DIR = "outputs"
REFERENCE_FILE = "reference.json"
KEY_LENGTH = 16


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def _parameter_files(entity: Mapping[str, Any]) -> Iterable[str]:
    for item in entity.get("parameter_files_all", []) or []:
        if isinstance(item, Mapping) and isinstance(item.get("path"), str):
            yield item["path"]
    for child in entity.get("children", []) or []:
        yield from _parameter_files(child)


def key_inputs(
    structure: Mapping[str, Any],
    *,
    scope: Iterable[str],
    bag_fingerprint: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """What a reference depends on. *structure* is the scope's structure with its arguments bound,
    so it carries the export, the deploy arguments and every parameter value."""
    files = sorted(set(_parameter_files(structure)))
    return {
        "structure": hashlib.sha256(json.dumps(structure, sort_keys=True, default=str).encode()).hexdigest(),
        "parameter_files": {path: _file_hash(Path(path)) for path in files},
        "scope": sorted(scope),
        "bag": hashlib.sha256(bag_fingerprint.encode()).hexdigest(),
        "options": dict(extra or {}),
    }


def reference_key(inputs: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()[:KEY_LENGTH]


def reference_dir(json_path: Path, mode: str, key: str, root: Optional[Path] = None) -> Optional[Path]:
    """The keyed reference directory of an export's structure file; None outside an export tree."""
    identity = export_identity(Path(json_path))
    if identity is None:
        return None
    package, system = identity
    return system_record_dir(package, system, root) / REPLAY_KIND / mode / key
