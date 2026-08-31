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

"""Canonical path namespace shared by manifest readers, file maps, and duplicate ranking."""

import os
from functools import lru_cache
from typing import Any, Optional

# Manifest recording the workspace package map and the anchor for relative entries.
PACKAGE_MAP_FILENAME = "_package_map.yaml"
WORKSPACE_ROOT_KEY = "workspace_root"

# Placeholder for the workspace root in exported files; expanded on load.
WORKSPACE_ROOT_TOKEN = "${workspace_root}"
WORKSPACE_ROOT_ENV = "AUTOWARE_SYSTEM_DESIGNER_WORKSPACE_ROOT"


@lru_cache(maxsize=None)
def canonical_path(path: str) -> str:
    """Absolute path with symlinks resolved; the single key namespace for file lookups and ranking."""
    return os.path.realpath(path)


def resolve_manifest_path(path: str, anchor: str) -> str:
    """Canonical absolute path of a manifest entry; relative entries are joined to the anchor."""
    return canonical_path(path if os.path.isabs(path) else os.path.join(anchor, path))


def _map_strings(value: Any, convert) -> Any:
    """Copy of a JSON payload with *convert* applied to every string, dict keys included."""
    if isinstance(value, str):
        return convert(value)
    if isinstance(value, dict):
        return {_map_strings(k, convert): _map_strings(v, convert) for k, v in value.items()}
    if isinstance(value, list):
        return [_map_strings(item, convert) for item in value]
    return value


def _root_variants(workspace_root: str) -> list:
    """Prefix spellings of the root; the symlink-resolved form covers canonical paths."""
    root = workspace_root.rstrip(os.sep)
    variants = [root, canonical_path(root)]
    return list(dict.fromkeys(v for v in variants if v and v != os.sep))


def contract_workspace_paths(value: Any, workspace_root: Optional[str]) -> Any:
    """Payload with every workspace-root prefix replaced by the export token."""
    if not workspace_root:
        return value
    roots = _root_variants(workspace_root)

    def convert(text: str) -> str:
        for root in roots:
            text = text.replace(root + os.sep, WORKSPACE_ROOT_TOKEN + os.sep)
            if text == root:
                text = WORKSPACE_ROOT_TOKEN
        return text

    return _map_strings(value, convert)


def expand_workspace_paths(value: Any, workspace_root: Optional[str]) -> Any:
    """Payload with every export token replaced by the workspace root."""
    if not workspace_root:
        return value
    root = workspace_root.rstrip(os.sep)

    def convert(text: str) -> str:
        text = text.replace(WORKSPACE_ROOT_TOKEN + os.sep, root + os.sep)
        return root if text == WORKSPACE_ROOT_TOKEN else text

    return _map_strings(value, convert)


def has_workspace_token(value: Any) -> bool:
    """True when any string in the payload still carries the export token."""
    if isinstance(value, str):
        return WORKSPACE_ROOT_TOKEN in value
    if isinstance(value, dict):
        return any(has_workspace_token(k) or has_workspace_token(v) for k, v in value.items())
    if isinstance(value, list):
        return any(has_workspace_token(item) for item in value)
    return False


def derive_workspace_root(actual_dir: str, tokenized_dir: str) -> Optional[str]:
    """Workspace root implied by where a tokenized directory actually resides.

    The token-relative tail of ``tokenized_dir`` is matched against the end of
    ``actual_dir``; the environment override wins. None when the directory was
    exported without a token or the tail does not match.
    """
    env_root = os.environ.get(WORKSPACE_ROOT_ENV)
    if env_root:
        return canonical_path(env_root)
    if not tokenized_dir.startswith(WORKSPACE_ROOT_TOKEN):
        return None
    tail = [p for p in tokenized_dir[len(WORKSPACE_ROOT_TOKEN) :].split(os.sep) if p]
    actual = canonical_path(actual_dir).rstrip(os.sep).split(os.sep)
    if not tail:
        return os.sep.join(actual) or os.sep
    if actual[-len(tail) :] != tail:
        return None
    return os.sep.join(actual[: -len(tail)]) or os.sep
