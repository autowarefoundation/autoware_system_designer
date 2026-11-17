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

"""Files of a replay run and the topics of a bag, for tools beside the runtime.

A replay writes into the ``replay`` directory of its run directory::

    <run dir>/replay/boundary.json   scope, boundary, fed and recorded topics, pre-flight problems
    <run dir>/replay/result.json     counts, violations, digest of the outputs, verdict summary
    <run dir>/replay/verdict.json    per topic comparison with the reference

Its log carries progress lines, ``replay progress: {"phase", "step", "fraction"}``: the phase is
pre-process (scope, bag pre-flight, launch and startup), replay (the bag stepped through, with the
fraction of its time span done) or post-process (shutdown, verdict, result). A phase that completed
reports fraction 1 last.

Plain Python with no ROS import while the bag has a ``metadata.yaml``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ._impl.replay.bag import METADATA_FILE, BagError, bag_directory, read_bag_info

REPLAY_COMMAND = "autoware-system-designer-replay"
REPLAY_SUBDIR = "replay"
BOUNDARY_FILE = "boundary.json"
OPTIONS_FILE = "options.json"
RESULT_FILE = "result.json"
VERDICT_FILE = "verdict.json"
STEP_LOG_FILE = "steps.log"

EXIT_OK = 0
EXIT_DIFFERS = 1
EXIT_FAILED = 2
EXIT_INTERRUPTED = 130

PROGRESS_MARK = "replay progress: "
PHASE_PRE = "pre-process"
PHASE_REPLAY = "replay"
PHASE_POST = "post-process"
PHASES = (PHASE_PRE, PHASE_REPLAY, PHASE_POST)

__all__ = [
    "BOUNDARY_FILE",
    "OPTIONS_FILE",
    "BagError",
    "EXIT_DIFFERS",
    "EXIT_FAILED",
    "EXIT_OK",
    "PHASES",
    "PHASE_POST",
    "PHASE_PRE",
    "PHASE_REPLAY",
    "PROGRESS_MARK",
    "REPLAY_COMMAND",
    "REPLAY_SUBDIR",
    "RESULT_FILE",
    "STEP_LOG_FILE",
    "VERDICT_FILE",
    "bag_topics",
    "parse_progress",
    "progress_message",
    "read_replay_file",
]


def bag_topics(path: Path, *, generate: bool = False) -> dict[str, Any]:
    """Topics, storage and time span of the bag at *path*; raises ``BagError`` for no readable bag.

    ``metadata`` tells where they were read from: the bag's ``file``, one ``generated`` on this call
    (with *generate*, for a bag without one), or the ``storage`` file itself.
    """
    path = Path(path)
    had_metadata = (bag_directory(path) / METADATA_FILE).is_file()
    info = read_bag_info(path, generate=generate)
    has_metadata = (bag_directory(path) / METADATA_FILE).is_file()
    return {
        "path": str(info.path),
        "metadata": "file" if had_metadata else "generated" if has_metadata else "storage",
        "storage": info.storage_id,
        "start_ns": info.start_ns,
        "duration_ns": info.duration_ns,
        "topics": [
            {"name": topic.name, "type": topic.msg_type, "count": topic.count}
            for topic in sorted(info.topics.values(), key=lambda topic: topic.name)
        ],
    }


def read_replay_file(run_dir: Path, name: str) -> Optional[dict[str, Any]]:
    """One JSON file of a run's replay directory; None when the run wrote none."""
    try:
        payload = json.loads((Path(run_dir) / REPLAY_SUBDIR / name).read_text())
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def progress_message(phase: str, step: str = "", fraction: Optional[float] = None) -> str:
    """One progress line of a replay's log; *fraction* is the share of the phase done."""
    payload: dict[str, Any] = {"phase": phase, "step": step}
    if fraction is not None:
        payload["fraction"] = round(min(max(fraction, 0.0), 1.0), 4)
    return PROGRESS_MARK + json.dumps(payload)


def parse_progress(line: str) -> Optional[dict[str, Any]]:
    """The progress a log line carries; None for any other line."""
    index = line.find(PROGRESS_MARK)
    if index < 0:
        return None
    try:
        payload = json.loads(line[index + len(PROGRESS_MARK) :])
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("phase") not in PHASES:
        return None
    fraction = payload.get("fraction")
    return {
        "phase": payload["phase"],
        "step": str(payload.get("step") or ""),
        "fraction": float(fraction) if isinstance(fraction, (int, float)) and not isinstance(fraction, bool) else None,
    }
