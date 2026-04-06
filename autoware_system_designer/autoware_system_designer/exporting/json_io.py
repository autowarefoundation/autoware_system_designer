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

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from ..file_io.source_location import SourceLocation, format_source
from .instance_to_json import collect_system_structure
from .schema import (
    InstanceData,
    SystemStructureMetadata,
    SystemStructurePayload,
)

logger = logging.getLogger(__name__)


def build_system_structure(instance, system_name: str, mode: str) -> SystemStructurePayload:
    """Build a schema-versioned system structure payload from an Instance.

    Delegates to the authoritative serializer in instance_serializer module.
    """
    return collect_system_structure(instance, system_name, mode)


def build_system_structure_snapshot(
    instance, system_name: str, mode: str, step: str, error: Exception | None = None
) -> SystemStructurePayload:
    """Build a system structure payload with step/error metadata for snapshots."""

    payload = build_system_structure(instance, system_name, mode)
    metadata: SystemStructureMetadata = payload.setdefault("metadata", {})
    metadata["step"] = step
    if error:
        metadata["error"] = {
            "message": str(error),
            "type": error.__class__.__name__,
        }
    return payload


def save_system_structure_snapshot(
    output_path: str,
    instance,
    system_name: str,
    mode: str,
    step: str,
    error: Exception | None = None,
) -> SystemStructurePayload:
    """Build and save a system structure snapshot payload to JSON."""

    payload = build_system_structure_snapshot(instance, system_name, mode, step, error)
    save_system_structure(output_path, payload)
    return payload


def save_system_structure(output_path: str, payload: SystemStructurePayload) -> None:
    """Save system structure payload to JSON."""

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        with open(output_path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=True)
        logger.info(f"Saved system structure JSON: {output_path}")
    except Exception as e:
        src = SourceLocation(file_path=Path(output_path))
        logger.error(f"Failed to save system structure JSON: {output_path}: {e}{format_source(src)}")
        raise


def load_system_structure(input_path: str) -> SystemStructurePayload:
    """Load system structure payload from JSON."""

    try:
        with open(input_path, "r") as f:
            return json.load(f)
    except Exception as e:
        src = SourceLocation(file_path=Path(input_path))
        logger.error(f"Failed to load system structure JSON: {input_path}: {e}{format_source(src)}")
        raise


def extract_system_structure_data(
    payload: Dict[str, Any],
) -> Tuple[InstanceData, SystemStructureMetadata]:
    """Return (data, metadata) from payload or raw data if unversioned."""

    if isinstance(payload, dict) and "schema_version" in payload and "data" in payload:
        return payload.get("data", {}), payload.get("metadata", {})
    return payload, {}
