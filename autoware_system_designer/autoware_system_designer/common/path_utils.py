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

# Manifest recording the workspace package map and the anchor for relative entries.
PACKAGE_MAP_FILENAME = "_package_map.yaml"
WORKSPACE_ROOT_KEY = "workspace_root"


@lru_cache(maxsize=None)
def canonical_path(path: str) -> str:
    """Absolute path with symlinks resolved; the single key namespace for file lookups and ranking."""
    return os.path.realpath(path)


def resolve_manifest_path(path: str, anchor: str) -> str:
    """Canonical absolute path of a manifest entry; relative entries are joined to the anchor."""
    return canonical_path(path if os.path.isabs(path) else os.path.join(anchor, path))
