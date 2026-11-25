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

from autoware_system_designer.builder.parameters.parameter_resolver import ParameterResolver


def test_command_substitution_is_recorded_once_and_shared_by_copies():
    resolver = ParameterResolver([], {})
    value = "$(command 'xacro model.xacro')"

    assert resolver.resolve_string(value) == value
    resolver.copy().resolve_string(value)

    assert resolver.command_uses == {value: value}


def test_build_names_each_command_value_once_on_stderr(tmp_path, capsys):
    from pipeline_harness import run_pipeline, stage_case

    workspace = stage_case("single_node", tmp_path)
    node_file = next((workspace / "demo_pkg").glob("*.node.yaml"))
    text = node_file.read_text()
    marker = "param_values:\n"
    assert marker in text
    node_file.write_text(
        text.replace(
            marker,
            marker
            + "  - name: robot_description\n"
            + "    type: string\n"
            + "    default: $(command 'xacro $(find-pkg-share demo_pkg)/model.xacro')\n",
            1,
        )
    )

    run_pipeline(workspace, "demo_pkg/Solo.system.yaml", tmp_path)

    err = capsys.readouterr().err
    assert err.count("WARNING: $(command ...) is deprecated") == 1
    assert err.count("model.xacro") == 1
    assert str(node_file.name) in err
