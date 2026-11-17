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

"""Directory listings for the studio's path selectors: one directory's entries, directories first, a
directory that holds a rosbag marked as one."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from . import api

# Entries a listing gives at most.
MAX_ENTRIES = 2000
# Files that make a directory a rosbag.
BAG_MARKERS = ("metadata.yaml",)
BAG_SUFFIXES = (".mcap", ".db3")


def _is_bag(directory: Path) -> bool:
    try:
        with os.scandir(directory) as entries:
            return any(e.is_file() and (e.name in BAG_MARKERS or e.name.endswith(BAG_SUFFIXES)) for e in entries)
    except OSError:
        return False


def _start(path: Optional[str]) -> Path:
    """The directory a listing shows: *path*, its nearest existing ancestor, else the home directory; a
    file's directory for a file."""
    if path is None or path == "":
        return Path.home()
    if not isinstance(path, str):
        raise api.ApiError(api.INVALID_PARAMS, "path must be a string")
    current = Path(path).expanduser()
    if not current.is_absolute():
        raise api.ApiError(api.INVALID_PARAMS, "path must be an absolute path")
    while not current.is_dir() and current != current.parent:
        current = current.parent
    return current


def list_directory(path: Optional[str], *, files: bool = False, hidden: bool = False) -> dict[str, Any]:
    """The entries of the directory at *path* (see ``_start``): subdirectories, with ``bag`` for one that
    holds a rosbag, and with *files* the files and their sizes; names starting with ``.`` only with
    *hidden*. ``truncated`` tells a listing cut at ``MAX_ENTRIES``."""
    directory = _start(path)
    try:
        with os.scandir(directory) as scanned:
            found = [e for e in scanned if hidden or not e.name.startswith(".")]
    except PermissionError as exc:
        raise api.ApiError(api.FORBIDDEN, f"{directory}: permission denied") from exc
    except OSError as exc:
        raise api.ApiError(api.NOT_FOUND, f"{directory}: {exc.strerror or exc}") from exc
    entries = []
    truncated = False
    for entry in sorted(found, key=lambda e: (not e.is_dir(), e.name.lower())):
        if len(entries) >= MAX_ENTRIES:
            truncated = True
            break
        try:
            is_dir = entry.is_dir()
            if is_dir:
                entries.append({"name": entry.name, "dir": True, "bag": _is_bag(Path(entry.path))})
            elif files and entry.is_file():
                entries.append({"name": entry.name, "dir": False, "size": entry.stat().st_size})
        except OSError:
            continue
    return {
        "path": str(directory),
        "parent": None if directory == directory.parent else str(directory.parent),
        "home": str(Path.home()),
        "bag": _is_bag(directory),
        "writable": os.access(directory, os.W_OK),
        "entries": entries,
        "truncated": truncated,
    }
