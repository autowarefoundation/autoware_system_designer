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


"""Measured latency files the sequence diagram reads: shape, validation, and the bundle copy.

A file is one measurement run of one mode::

    {
      "schema": "autoware_system_designer/latency/1",
      "mode": "Runtime",
      "source": "caret",
      "processes": [
        {"node_path": "/localization/.../ekf_localizer", "process": "fuse",
         "count": 1200, "min_ms": 1.8, "mean_ms": 3.1, "max_ms": 11.2, "sd_ms": 0.9}
      ],
      "links": [
        {"topic": "/localization/.../kinematic_state",
         "publisher": "/localization/.../ekf_localizer", "subscriber": "/control/...",
         "min_ms": 0.2, "mean_ms": 0.4, "max_ms": 3.0, "sd_ms": 0.3}
      ]
    }

Records are keyed by node path and process or topic, never by unique_id: ids
are name hashes and change whenever the design is edited. ``sd_ms`` and
``count`` are optional; a record without ``sd_ms`` is drawn spread-unknown.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

LATENCY_SCHEMA = "autoware_system_designer/latency/1"

# Directory beside a system definition file that holds <mode>_latency.json files.
LATENCY_DIR_NAME = "latency"

_PROCESS_REQUIRED = ("node_path", "process", "min_ms", "max_ms")
_LINK_REQUIRED = ("topic", "min_ms", "max_ms")


def _check_records(records: Any, kind: str, required: Iterable[str]) -> None:
    if records is None:
        return
    if not isinstance(records, list):
        raise ValueError(f"'{kind}' must be a list")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(f"{kind}[{index}] must be an object")
        for field in required:
            if record.get(field) is None:
                raise ValueError(f"{kind}[{index}] lacks '{field}'")
        if record["min_ms"] > record["max_ms"]:
            raise ValueError(f"{kind}[{index}] has min_ms above max_ms")


def validate_latency_data(data: Any) -> Dict[str, Any]:
    """Return the data when it is a well-formed latency file, else raise ValueError."""
    if not isinstance(data, dict):
        raise ValueError("latency file must hold a JSON object")
    if data.get("schema") != LATENCY_SCHEMA:
        raise ValueError(f"unknown latency schema {data.get('schema')!r}; expected {LATENCY_SCHEMA!r}")
    _check_records(data.get("processes"), "processes", _PROCESS_REQUIRED)
    _check_records(data.get("links"), "links", _LINK_REQUIRED)
    return data


def load_latency_file(path: str | os.PathLike) -> Dict[str, Any]:
    """Read and validate one latency file."""
    with open(path, encoding="utf-8") as handle:
        return validate_latency_data(json.load(handle))


def convert_caret_result(path: str | os.PathLike) -> Dict[str, Any]:
    """Convert a CARET measurement into the latency file shape.

    CARET is the measurement path in both directions: the designer emits the
    CARET architecture and target paths (``builder.export.caret_export``), CARET
    measures, and its callback and communication latencies come back as
    ``processes[]`` and ``links[]``. Reserved; not implemented.
    """
    raise NotImplementedError("CARET result conversion is not implemented; export the run as " + LATENCY_SCHEMA)


def latency_dir_for(system_file: str | None) -> str | None:
    """The latency directory kept beside a system definition file."""
    if not system_file:
        return None
    return os.path.join(os.path.dirname(system_file), LATENCY_DIR_NAME)


def copy_latency_files(latency_dir: str | None, modes: List[str], web_data_dir: str) -> List[str]:
    """Place each mode's validated latency file in the web bundle; returns the modes served."""
    if not latency_dir or not os.path.isdir(latency_dir):
        return []
    served: List[str] = []
    for mode in modes:
        source = Path(latency_dir) / f"{mode}_latency.json"
        if not source.is_file():
            continue
        try:
            load_latency_file(source)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Latency file ignored: {source} ({exc})")
            continue
        os.makedirs(web_data_dir, exist_ok=True)
        shutil.copy2(source, os.path.join(web_data_dir, source.name))
        served.append(mode)
        logger.info(f"Copied latency file: {source.name}")
    return served
