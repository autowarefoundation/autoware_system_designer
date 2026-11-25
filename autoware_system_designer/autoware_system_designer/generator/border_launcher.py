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

"""Entry launch file of a border: its launch arguments around the generated compute-unit launcher."""

import logging
import os

from autoware_system_designer.common.exceptions import DeploymentError
from autoware_system_designer.common.path_utils import export_path
from autoware_system_designer.common.template_renderer import TemplateRenderer
from autoware_system_designer.parser.border_parser import BorderConfig

logger = logging.getLogger(__name__)


def generate_border_launcher(border: BorderConfig, launcher_dir: str, mode_key: str, output_file: str) -> str:
    """Write the border entry launch file; the include path is relative, so the install tree stays relocatable."""
    unit = border.name
    unit_launcher = export_path(launcher_dir, mode_key, unit, f"{unit}.launch.xml")
    if not os.path.isfile(unit_launcher):
        raise DeploymentError(f"Border {border.name}: compute-unit launcher not generated: {unit_launcher}")
    output_file = os.path.abspath(output_file)
    relative = os.path.relpath(unit_launcher, os.path.dirname(output_file)).replace(os.sep, "/")
    TemplateRenderer().render_template_to_file(
        "border_launcher.xml.jinja2",
        output_file,
        border_name=border.name,
        components=[c["name"] for c in border.components],
        arguments=border.arguments,
        unit_launcher=relative,
    )
    logger.info(f"Generated border launcher: {output_file}")
    return output_file
