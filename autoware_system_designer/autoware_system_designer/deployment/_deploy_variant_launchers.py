# Copyright 2025 TIER IV, inc.
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

import logging
import os
from typing import Any, Dict, List

from ..file_io.system_structure_json import extract_system_structure_data, load_system_structure
from ..file_io.template_renderer import TemplateRenderer

logger = logging.getLogger(__name__)


def _generate_deploy_variant_launchers(
    *,
    mode_keys: List[str],
    system_structure_dir: str,
    launcher_dir: str,
    deployment_package_path: str,
    system_name: str,
    deploy_variants: List[Dict[str, Any]],
) -> None:
    renderer = TemplateRenderer()

    for mode_key in mode_keys:
        structure_path = os.path.join(system_structure_dir, f"{mode_key}.json")
        payload = load_system_structure(structure_path)
        data, _ = extract_system_structure_data(payload)

        compute_units = sorted(
            {
                child.get("compute_unit")
                for child in data.get("children", [])
                if child.get("compute_unit")
            }
        )

        for deploy_item in deploy_variants:
            deploy_name = deploy_item.get("name")
            arguments = deploy_item.get("arguments", deploy_item.get("variables", []))
            if not deploy_name:
                continue

            for compute_unit in compute_units:
                output_dir = os.path.join(
                    launcher_dir,
                    "deployments",
                    deploy_name,
                    mode_key,
                    compute_unit,
                )
                output_filename = f"{compute_unit.lower()}.launch.xml"
                base_launcher_path = os.path.join(
                    deployment_package_path,
                    "exports",
                    system_name,
                    "launcher",
                    mode_key,
                    compute_unit,
                    output_filename,
                ).replace("\\\\", "/")

                renderer.render_template_to_file(
                    "deployment_variant_wrapper.launch.xml.jinja2",
                    os.path.join(output_dir, output_filename),
                    deploy_name=deploy_name,
                    mode_key=mode_key,
                    compute_unit=compute_unit,
                    arguments=arguments,
                    base_launcher_path=base_launcher_path,
                )

            logger.info(
                "Generated deployment variant launchers for deploy='%s', mode='%s'",
                deploy_name,
                mode_key,
            )
