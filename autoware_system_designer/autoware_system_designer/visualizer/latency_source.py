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

The runtime writes one file per measurement run of one mode
(``autoware_system_designer/latency/2``, see the runtime README)::

    {
      "schema": "autoware_system_designer/latency/2",
      "mode": "Runtime",
      "run": {"window_s": 60.0, "probe": true, ...},
      "nodes": [
        {"node_path": "/localization/.../ekf_localizer",
         "inputs": [...], "timers": [...],
         "outputs": [{"topic": "/localization/kinematic_state", "rate_hz": 49.9,
                      "trigger": {"kind": "timer", "period_ms": 20.0, "share": 0.998},
                      "exec": {"count": 2990, "min_ms": 0.9, "mean_ms": 1.4, "max_ms": 6.2, "sd_ms": 0.4}}],
         "declared_diff": [...]}
      ],
      "links": [
        {"topic": "...", "publisher": "...", "subscriber": "...", "count": 600,
         "min_ms": 0.2, "mean_ms": 0.4, "max_ms": 3.0, "sd_ms": 0.3},
        {"topic": "...", "publisher": "...", "subscriber": "...", "intra_process": true}
      ],
      "chains": [{"from": "<node_path>:timer:<period_ms>", "to": "<node_path>:<topic>", ...}]
    }

The first shape (``latency/1``: ``processes[]`` keyed by node path and process
name, ``links[]``) stays readable. Records are keyed by node path and topic or
process, never by unique_id: ids are name hashes and change whenever the design
is edited. ``sd_ms`` and ``count`` are optional; a record without ``sd_ms`` is
drawn spread-unknown.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List

logger = logging.getLogger(__name__)

LATENCY_SCHEMA_V1 = "autoware_system_designer/latency/1"
LATENCY_SCHEMA = "autoware_system_designer/latency/2"
LATENCY_SCHEMAS = (LATENCY_SCHEMA_V1, LATENCY_SCHEMA)

# Directory beside a system definition file that holds <mode>_latency.json files.
LATENCY_DIR_NAME = "latency"
# Global the script twin of a latency file assigns to, keyed by mode; a page
# opened from file:// cannot fetch the JSON and loads the script instead.
LATENCY_SCRIPT_GLOBAL = "latencyData"

_PROCESS_REQUIRED = ("node_path", "process", "min_ms", "max_ms")
_LINK_REQUIRED = ("topic", "min_ms", "max_ms")
_SUMMARY_REQUIRED = ("min_ms", "max_ms")
_CHAIN_REQUIRED = ("from", "to", "min_ms", "max_ms")


def _check_records(records: Any, kind: str, required: Iterable[str]) -> None:
    if records is None:
        return
    if not isinstance(records, list):
        raise ValueError(f"'{kind}' must be a list")
    for index, record in enumerate(records):
        _check_record(record, f"{kind}[{index}]", required)


def _check_record(record: Any, label: str, required: Iterable[str]) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"{label} must be an object")
    for field in required:
        if record.get(field) is None:
            raise ValueError(f"{label} lacks '{field}'")
    if record.get("min_ms") is not None and record.get("max_ms") is not None and record["min_ms"] > record["max_ms"]:
        raise ValueError(f"{label} has min_ms above max_ms")


def _check_v2(data: Dict[str, Any]) -> None:
    nodes = data.get("nodes")
    if nodes is not None:
        if not isinstance(nodes, list):
            raise ValueError("'nodes' must be a list")
        for index, node in enumerate(nodes):
            _check_record(node, f"nodes[{index}]", ("node_path",))
            for out_index, output in enumerate(node.get("outputs") or []):
                label = f"nodes[{index}].outputs[{out_index}]"
                _check_record(output, label, ("topic",))
                if output.get("exec") is not None:
                    _check_record(output["exec"], f"{label}.exec", _SUMMARY_REQUIRED)
    links = data.get("links")
    if links is not None:
        if not isinstance(links, list):
            raise ValueError("'links' must be a list")
        for index, link in enumerate(links):
            _check_record(link, f"links[{index}]", ("topic",))
            if not link.get("intra_process"):
                _check_record(link, f"links[{index}]", _LINK_REQUIRED)
    _check_records(data.get("chains"), "chains", _CHAIN_REQUIRED)


def validate_latency_data(data: Any) -> Dict[str, Any]:
    """Return the data when it is a well-formed latency file, else raise ValueError."""
    if not isinstance(data, dict):
        raise ValueError("latency file must hold a JSON object")
    schema = data.get("schema")
    if schema not in LATENCY_SCHEMAS:
        raise ValueError(f"unknown latency schema {schema!r}; expected one of {list(LATENCY_SCHEMAS)}")
    if schema == LATENCY_SCHEMA_V1:
        _check_records(data.get("processes"), "processes", _PROCESS_REQUIRED)
        _check_records(data.get("links"), "links", _LINK_REQUIRED)
    else:
        _check_v2(data)
    return data


def load_latency_file(path: str | os.PathLike) -> Dict[str, Any]:
    """Read and validate one latency file."""
    with open(path, encoding="utf-8") as handle:
        return validate_latency_data(json.load(handle))


def latency_dir_for(system_file: str | None) -> str | None:
    """The latency directory kept beside a system definition file."""
    if not system_file:
        return None
    return os.path.join(os.path.dirname(system_file), LATENCY_DIR_NAME)


def latency_script_text(mode: str, data: Dict[str, Any]) -> str:
    """The script twin of a latency file: the same object under the page's latency global."""
    return (
        f"window.{LATENCY_SCRIPT_GLOBAL} = window.{LATENCY_SCRIPT_GLOBAL} || {{}};\n"
        f"window.{LATENCY_SCRIPT_GLOBAL}[{json.dumps(mode)}] = {json.dumps(data, indent=2)};\n"
    )


def copy_latency_files(latency_dir: str | None, modes: List[str], web_data_dir: str) -> List[str]:
    """Place each mode's validated latency file and its script twin in the web bundle; returns the modes served."""
    if not latency_dir or not os.path.isdir(latency_dir):
        return []
    served: List[str] = []
    for mode in modes:
        source = Path(latency_dir) / f"{mode}_latency.json"
        if not source.is_file():
            continue
        try:
            data = load_latency_file(source)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"Latency file ignored: {source} ({exc})")
            continue
        os.makedirs(web_data_dir, exist_ok=True)
        shutil.copy2(source, os.path.join(web_data_dir, source.name))
        script = Path(web_data_dir) / f"{mode}_latency.js"
        script.write_text(latency_script_text(mode, data), encoding="utf-8")
        served.append(mode)
        logger.info(f"Copied latency file: {source.name}")
    return served
