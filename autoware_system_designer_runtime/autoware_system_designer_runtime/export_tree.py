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


"""Read side of an exported system: its manifest and the workspace paths its JSON carries tokenized.

An export lays out ``<output_root>/exports/<system>/{deployment.json, system_structure/<Mode>.json, ...}``.
Plain Python with no ROS import, shared by the launcher and the studio.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

# Workspace-path token written by the exporter; kept in sync with
# autoware_system_designer.common.path_utils (this module stays stdlib-only).
WORKSPACE_ROOT_TOKEN = "${workspace_root}"
WORKSPACE_ROOT_ENV = "AUTOWARE_SYSTEM_DESIGNER_WORKSPACE_ROOT"
MANIFEST_FILENAME = "deployment.json"


def manifest_path(json_path: Path) -> Path:
    """The export manifest sits at <output_root>/exports/<system>/deployment.json, beside system_structure."""
    return json_path.parent.parent / MANIFEST_FILENAME


def read_manifest(json_path: Path) -> Optional[dict[str, Any]]:
    """Manifest payload, or None when there is no readable manifest beside the export."""
    try:
        payload = json.loads(manifest_path(json_path).read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def derive_workspace_root(json_path: Path) -> Optional[str]:
    """Workspace root implied by where the export tree actually resides."""
    env_root = os.environ.get(WORKSPACE_ROOT_ENV)
    if env_root:
        return os.path.realpath(env_root)
    tokenized = (read_manifest(json_path) or {}).get("deployment_package_path", "")
    if not isinstance(tokenized, str):
        return None
    if not tokenized.startswith(WORKSPACE_ROOT_TOKEN):
        return None
    tail = [p for p in tokenized[len(WORKSPACE_ROOT_TOKEN) :].split(os.sep) if p]
    # system_structure dir sits at <output_root>/exports/<system>/system_structure.
    try:
        output_root = json_path.resolve().parents[3]
    except IndexError:
        return None
    actual = str(output_root).rstrip(os.sep).split(os.sep)
    if tail and actual[-len(tail) :] != tail:
        return None
    return os.sep.join(actual[: -len(tail)] if tail else actual) or os.sep


def expand_workspace_paths(value: Any, workspace_root: str) -> Any:
    root = workspace_root.rstrip(os.sep)
    if isinstance(value, str):
        value = value.replace(WORKSPACE_ROOT_TOKEN + os.sep, root + os.sep)
        return root if value == WORKSPACE_ROOT_TOKEN else value
    if isinstance(value, dict):
        return {
            expand_workspace_paths(k, workspace_root): expand_workspace_paths(v, workspace_root)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [expand_workspace_paths(item, workspace_root) for item in value]
    return value


def resolve_structure_paths(data: Any, json_path: str) -> Any:
    """Expand tokenized workspace paths in a loaded system_structure payload."""
    text_has_token = WORKSPACE_ROOT_TOKEN in json.dumps(data)
    if not text_has_token:
        return data
    root = derive_workspace_root(Path(json_path))
    if root is None:
        raise RuntimeError(
            f"Cannot locate the workspace root for tokenized paths in {json_path}; "
            f"set {WORKSPACE_ROOT_ENV} to the workspace root directory."
        )
    return expand_workspace_paths(data, root)
