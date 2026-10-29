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


def test_launch_commands_page_escapes_commands_and_names():
    hostile = "<script>alert(1)</script>"
    html = TemplateRenderer().render_template(
        "launch_commands.html.jinja2",
        system_name=hostile,
        systems_index_path="../systems.html",
        overview_path="x_overview.html",
        has_native=False,
        default_method="xml",
        deploy_names=[hostile],
        has_deploy_variants=True,
        command_rows=[
            {
                "show_mode_cell": True,
                "mode_rowspan": 1,
                "mode": "default",
                "show_ecu_cell": True,
                "ecu_rowspan": 1,
                "ecu": hostile,
                "xml": f"ros2 launch {hostile}",
                "native": "",
            }
        ],
        rows_by_deploy={hostile: []},
    )
    assert hostile not in html
    assert "&lt;script&gt;" in html


def test_config_js_quotes_strings_as_json():
    hostile = 'x"; alert(1); //'
    js = TemplateRenderer().render_template(
        "data/deployment_config.js.jinja2",
        deployment_name=hostile,
        package_name="pkg",
        systems_index_path="../systems.html",
        available_modes=["default"],
        default_mode="default",
        available_diagram_types=["overview"],
        default_diagram_type="overview",
        editor_scheme="vscode",
        system_definition_file="/tmp/x.yaml",
    )
    assert f"deploymentName: {json.dumps(hostile)}" in js


def test_data_js_keeps_declaration_key_order():
    js = TemplateRenderer().render_template(
        "data/common_design_data.js.jinja2",
        window_variable="w",
        mode="default",
        name="n",
        unique_id="u",
        entity_type="node",
        namespace="/ns",
        path="/ns/n",
        compute_unit="main",
        vis_guide={"zeta": 1, "alpha": 2},
        in_ports=[],
        out_ports=[],
        children=[],
        links=[],
        events=[],
        parameters=[],
    )
    assert '"vis_guide": {"zeta": 1, "alpha": 2}' in js
