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

import pytest
from pipeline_harness import run_pipeline, stage_case


def _nodes(entity, out=None):
    out = {} if out is None else out
    if entity.get("entity_type") == "node":
        out[entity["name"]] = entity
    for child in entity.get("children", []):
        _nodes(child, out)
    return out


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("global_parameters")
    workspace = stage_case("global_parameters", tmp_path)
    return run_pipeline(workspace, "wrapper_pkg/Global.system.yaml", tmp_path, strict=True)


def test_designed_node_holds_globals_below_its_own_values(run):
    user = _nodes(run.structure("default")["data"])["user"]
    params = {p["name"]: (p["value"], p["parameter_type"]) for p in user["parameters"]}

    assert params["max_steer_angle"] == ("0.7", "GLOBAL")
    assert params["use_sim_time"] == (False, "GLOBAL")
    assert params["wheel_base"] == ("1.0", "DEFAULT")


def test_launch_file_node_holds_globals_apart_from_launch_arguments(run):
    wrapper = _nodes(run.structure("default")["data"])["map"]

    assert [p["name"] for p in wrapper["parameters"]] == ["map_file"]
    assert {p["name"]: p["value"] for p in wrapper["launcher"]["global_parameters"]} == {
        "wheel_base": "2.79",
        "max_steer_angle": "0.7",
        "use_sim_time": False,
    }


def test_mode_overrides_a_global_value(run):
    nodes = _nodes(run.structure("simulation")["data"])

    user = {p["name"]: p["value"] for p in nodes["user"]["parameters"]}
    wrapper = {p["name"]: p["value"] for p in nodes["map"]["launcher"]["global_parameters"]}
    assert user["use_sim_time"] is True
    assert wrapper["use_sim_time"] is True
    assert wrapper["wheel_base"] == "2.79"


def test_launcher_scopes_the_globals_to_the_included_launch_file(run):
    launcher = (run.launcher_dir("default") / "main_ecu" / "map" / "map.launch.xml").read_text()

    group = launcher[launcher.index("<group>") : launcher.index("</group>")]
    assert '<set_parameter name="wheel_base" value="2.79"/>' in group
    assert '<set_parameter name="use_sim_time" value="False"/>' in group
    assert "map_loader.launch.xml" in group

    user = (run.launcher_dir("default") / "main_ecu" / "user" / "user.launch.xml").read_text()
    assert '<param name="max_steer_angle" value="0.7"/>' in user
    assert user.index('name="max_steer_angle"') < user.index('name="wheel_base"')
