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

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..exceptions import ValidationError
from ..file_io.system_structure_json import extract_system_structure_data, load_system_structure
from ..models.config import SystemConfig
from ..models.parsing.data_validator import entity_name_decode
from ..models.parsing.yaml_parser import yaml_parser


def iter_mode_payload_and_data(
    mode_keys: List[str],
    system_structure_dir: str,
) -> Iterator[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
    """Yield (mode_key, payload, extracted_data) for each mode."""

    for mode_key in mode_keys:
        structure_path = os.path.join(system_structure_dir, f"{mode_key}.json")
        payload = load_system_structure(structure_path)
        data, _ = extract_system_structure_data(payload)
        yield mode_key, payload, data


def _normalize_system_name(system_ref: str) -> str:
    system_name = os.path.basename(system_ref)
    if system_name.endswith(".yaml"):
        system_name = system_name[:-5]
    if "." in system_name:
        decoded_name, _ = entity_name_decode(system_name)
        return decoded_name
    return system_name


def _resolve_deployments_path(input_path: str) -> str:
    candidate = Path(input_path)
    if candidate.suffix != ".yaml":
        candidate = Path(f"{input_path}.yaml")

    if candidate.exists() and candidate.is_file():
        return str(candidate.resolve())

    raise ValidationError(
        f"Deployments table file not found: {candidate}. "
        f"Pass an existing '*.deployments.yaml' path to the build target."
    )


def _parse_deployments_list(deployments_path: str) -> Tuple[str, List[Dict[str, Any]]]:
    config_yaml = yaml_parser.load_config(deployments_path)
    if not isinstance(config_yaml, dict):
        raise ValidationError(f"Invalid deployments table format: {deployments_path}")

    base = config_yaml.get("base")
    if not isinstance(base, str) or not base:
        raise ValidationError(
            f"Deployments table must define non-empty 'base' (string): {deployments_path}"
        )

    raw_deploy_list = config_yaml.get("deploy_list", [])
    if not isinstance(raw_deploy_list, list):
        raise ValidationError(
            f"'deploy_list' must be a list in deployments table: {deployments_path}"
        )

    deploy_list: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_deploy_list):
        if not isinstance(item, dict):
            raise ValidationError(
                f"deploy_list[{idx}] must be an object in deployments table: {deployments_path}"
            )
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValidationError(
                f"deploy_list[{idx}] requires non-empty 'name' in deployments table: {deployments_path}"
            )
        arguments = item.get("arguments", item.get("variables", []))
        if arguments is None:
            arguments = []
        if not isinstance(arguments, list):
            raise ValidationError(
                f"deploy_list[{idx}].arguments must be a list in deployments table: {deployments_path}"
            )
        deploy_list.append({"name": name, "arguments": arguments})

    return base, deploy_list


def resolve_input_target(
    input_path: str,
    config_registry: Any,
) -> Tuple[SystemConfig, List[Dict[str, Any]], Optional[str]]:
    """Resolve the build target for a deployment.

    Supports:
    - deployments table mode: '*.deployments[.yaml]'
    - legacy system-only mode: input identifies one system entity
    """

    if input_path.endswith(".deployments") or input_path.endswith(".deployments.yaml"):
        table_path = _resolve_deployments_path(input_path)
        base_name, deploy_list = _parse_deployments_list(table_path)
        system_name = _normalize_system_name(base_name)
        system_config = config_registry.get_system(system_name)
        return system_config, deploy_list, table_path

    system_name = _normalize_system_name(input_path)
    system_config = config_registry.get_system(system_name)
    return system_config, [], None
