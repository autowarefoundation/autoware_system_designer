import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1.0"


def build_system_structure(instance, system_name: str, mode: str) -> Dict[str, Any]:
    """Build a schema-versioned system structure payload from an Instance."""
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "system_name": system_name,
            "mode": mode,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "data": instance.collect_instance_data(),
    }


def build_system_structure_snapshot(
    instance, system_name: str, mode: str, step: str, error: Exception | None = None
) -> Dict[str, Any]:
    """Build a system structure payload with step/error metadata for snapshots."""
    payload = build_system_structure(instance, system_name, mode)
    metadata = payload.setdefault("metadata", {})
    metadata["step"] = step
    if error:
        metadata["error"] = {
            "message": str(error),
            "type": error.__class__.__name__,
        }
    return payload


def save_system_structure(output_path: str, payload: Dict[str, Any]) -> None:
    """Save system structure payload to JSON."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    try:
        with open(output_path, "w") as f:
            json.dump(payload, f, indent=2, ensure_ascii=True)
        logger.info(f"Saved system structure JSON: {output_path}")
    except Exception as e:
        logger.error(f"Failed to save system structure JSON: {output_path}: {e}")
        raise


def load_system_structure(input_path: str) -> Dict[str, Any]:
    """Load system structure payload from JSON."""
    try:
        with open(input_path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load system structure JSON: {input_path}: {e}")
        raise


def extract_system_structure_data(payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (data, metadata) from payload or raw data if unversioned."""
    if isinstance(payload, dict) and "schema_version" in payload and "data" in payload:
        return payload.get("data", {}), payload.get("metadata", {})
    return payload, {}
