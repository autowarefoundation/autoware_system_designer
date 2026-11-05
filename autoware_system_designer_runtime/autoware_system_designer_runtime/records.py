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


"""Measurement records of exported systems: where they live and how they are named.

A record is a run's result kept for later comparison, so it lives outside the install
tree, which every rebuild replaces, and outside the per-run log directories::

    <record root>/<package>/<system>/latency/<Mode>/<YYYYmmdd-HHMMSS>.json

The record root is ``$AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR``, else
``$ROS_HOME/autoware_system_designer/records`` (``~/.ros`` when ``ROS_HOME`` is unset).
Plain Python with no ROS import: the studio reads records without a sourced workspace.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .export_tree import read_manifest

RECORD_DIR_ENV = "AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR"
RECORD_SUBPATH = Path("autoware_system_designer") / "records"
LATENCY_KIND = "latency"
STAMP_FORMAT = "%Y%m%d-%H%M%S"
RECORD_SUFFIX = ".json"

SYSTEM_STRUCTURE_DIR = "system_structure"


def record_root() -> Path:
    override = os.environ.get(RECORD_DIR_ENV)
    if override:
        return Path(override).expanduser()
    ros_home = os.environ.get("ROS_HOME") or os.path.join("~", ".ros")
    return Path(ros_home).expanduser() / RECORD_SUBPATH


def system_record_dir(package: str, system: str, root: Optional[Path] = None) -> Path:
    return (root or record_root()) / package / system


def latency_record_dir(package: str, system: str, mode: str, root: Optional[Path] = None) -> Path:
    return system_record_dir(package, system, root) / LATENCY_KIND / mode


def new_record_name(stamp: Optional[str] = None) -> str:
    return f"{stamp or time.strftime(STAMP_FORMAT)}{RECORD_SUFFIX}"


def latency_records(package: str, system: str, mode: str, root: Optional[Path] = None) -> list[Path]:
    """The mode's latency records, newest first; stamps sort by time."""
    directory = latency_record_dir(package, system, mode, root)
    if not directory.is_dir():
        return []
    files = [p for p in directory.iterdir() if p.is_file() and p.suffix == RECORD_SUFFIX]
    return sorted(files, key=lambda p: p.name, reverse=True)


def export_identity(json_path: Path) -> Optional[tuple[str, str]]:
    """(package, system) of the export a ``system_structure/<Mode>.json`` belongs to, from its manifest."""
    json_path = Path(json_path).resolve()
    if json_path.parent.name != SYSTEM_STRUCTURE_DIR:
        return None
    manifest = read_manifest(json_path) or {}
    package = manifest.get("deployment_package_name")
    if not isinstance(package, str) or not package:
        return None
    return package, json_path.parent.parent.name
