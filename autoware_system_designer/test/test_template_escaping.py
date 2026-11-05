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

import xml.etree.ElementTree as ET

from autoware_system_designer.common.template_renderer import TemplateRenderer


def test_launch_xml_attributes_are_escaped():
    value = "a\"<b>&c'd"
    xml = TemplateRenderer().render_template(
        "node_launcher.xml.jinja2",
        node_name="n",
        inputs=[{"name": "in<"}],
        outputs=[],
        param_files=[],
        parameter_files=[],
        param_values=[{"name": "p", "default_value": value}],
        launch_state="node",
        package_name="pkg",
        executable_name="exe",
        node_output="screen",
    )
    root = ET.fromstring(xml)
    args = {arg.get("name"): arg.get("default") for arg in root.findall("arg")}
    assert args["p"] == value
    assert "input/in<" in args
    assert root.find("node").find("remap").get("from") == "~/input/in<"
