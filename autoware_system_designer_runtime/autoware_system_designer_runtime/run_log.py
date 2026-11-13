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

"""Log directory of a run: where it lives and what it holds.

A run of a compute unit owns one directory under a log base::

    <log base>/<YYYYmmdd_HHMMSS>/run.json            manifest of the run
    <log base>/<YYYYmmdd_HHMMSS>/<member>/{out,err}  output of one member

Plain Python with no ROS import: the studio reads runs without a sourced workspace.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

LOG_BASE = Path("/tmp/autoware_system_designer_logs")
RUN_MANIFEST = "run.json"
STREAMS = ("out", "err")
# Tail of a member file: at most this many lines from its last bytes.
TAIL_LINES = 200
TAIL_BYTES = 64 * 1024


def member_dir_name(path: str) -> str:
    """Name of a member's directory: the member path with every character outside letters, digits,
    ``_``, ``-`` and ``.`` replaced by ``_``, leading ``_`` dropped."""
    safe = "".join(ch if ch.isalnum() or ch in "_-." else "_" for ch in path).lstrip("_")
    return safe or "_unnamed_"


def tail_lines(path: Path, size: Optional[int] = None) -> tuple[list[str], bytes]:
    """The last complete lines of a file's first ``size`` bytes (the whole file by default), at most
    ``TAIL_LINES`` from its final ``TAIL_BYTES``, and the unterminated bytes after them."""
    try:
        if size is None:
            size = path.stat().st_size
        if size == 0:
            return [], b""
        start = max(0, size - TAIL_BYTES)
        with open(path, "rb") as handle:
            handle.seek(start)
            data = handle.read(size - start)
    except OSError:
        return [], b""
    chunks = data.split(b"\n")
    rest = chunks.pop()
    if start > 0 and chunks:
        chunks.pop(0)  # a line cut by the window start
    return [chunk.decode("utf-8", "replace") for chunk in chunks[-TAIL_LINES:]], rest


def write_run_manifest(
    out_dir: Path,
    *,
    json_path: str,
    data: Mapping[str, Any],
    ecu: Optional[str],
    deploy: Optional[str],
    members: Sequence[str],
    launch: Optional[str] = None,
) -> Path:
    """Records what a run's log directory holds: the system structure and unit it ran, the launch the
    run belongs to and the member paths whose directories lie beside it."""
    metadata = data.get("metadata")
    if not isinstance(metadata, Mapping):
        metadata = {}
    manifest = {
        "system": metadata.get("system_name"),
        "mode": metadata.get("mode") or Path(json_path).stem,
        "compute_unit": ecu,
        "deploy": deploy,
        "launch": launch,
        "system_structure": str(Path(json_path).resolve()),
        "started": time.time(),
        "members": list(members),
    }
    path = out_dir / RUN_MANIFEST
    partial = path.with_name(path.name + ".tmp")
    partial.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(partial, path)
    return path


def read_run_manifest(run_dir: Path) -> Optional[dict[str, Any]]:
    """The run manifest of a log directory; None where there is none, so the directory is not a run."""
    try:
        manifest = json.loads((run_dir / RUN_MANIFEST).read_text())
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None
