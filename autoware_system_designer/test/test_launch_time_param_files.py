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
from pathlib import Path

import pytest
from pipeline_harness import run_pipeline

from autoware_system_designer.common.exceptions import ValidationError

_NODE = """\
autoware_system_design_format: 0.4.0
name: Probe.node
description: Node with a build-time and a launch-time parameter file.
package:
  name: lt_pkg
  provider: dummy
launch:
  plugin: lt::Probe
  executable: probe_node
  node_output: screen
subscribers: []
publishers: []
param_files:
  - name: defaults_param_path
    default: config/defaults.param.yaml
  - name: vehicle_param_path
    default: $(var params_dir)/vehicle.param.yaml
param_values:
  - name: rate_hz
    type: double
    default: 10.0
processes: []
"""

_PARAMETER_SET = """\
autoware_system_design_format: 0.4.0
name: Tune.parameter_set
description: Launch-time override file and an override value.
parameters:
  - node: /probe
    param_files:
      - defaults_param_path: $(var params_dir)/override.param.yaml
    param_values:
      - name: rate_hz
        type: double
        value: 20.0
"""

_SYSTEM = """\
autoware_system_design_format: 0.4.0
name: Probed.system
description: Probe node whose parameter files follow a launch argument.
arguments:
  - name: params_dir
variables: []
modes:
  - name: default
    description: Default mode
    default: true
components:
  - name: probe
    entity: Probe.node
    compute_unit: main_ecu
    parameter_set: [{parameter_set}]
connections: []
"""

_DEFAULTS = """\
/**:
  ros__parameters:
    gain: 1.5
"""


def _stage(tmp_path: Path, parameter_set: bool = True, system_extra: str = "") -> Path:
    pkg = tmp_path / "ws" / "lt_pkg"
    (pkg / "config").mkdir(parents=True)
    (pkg / "Probe.node.yaml").write_text(_NODE)
    (pkg / "config" / "defaults.param.yaml").write_text(_DEFAULTS)
    if parameter_set:
        (pkg / "Tune.parameter_set.yaml").write_text(_PARAMETER_SET)
    (pkg / "Probed.system.yaml").write_text(
        _SYSTEM.format(parameter_set="Tune.parameter_set" if parameter_set else "") + system_extra
    )
    return pkg.parent


def _node_params(run) -> list:
    root = ET.parse(run.launcher_dir("default") / "main_ecu" / "probe" / "probe.launch.xml").getroot()
    return [p.get("from") or p.get("name") for p in root.find("node").findall("param")]


def test_launch_time_files_load_within_their_tier(tmp_path):
    run = run_pipeline(_stage(tmp_path), "lt_pkg/Probed.system.yaml", tmp_path)
    assert _node_params(run) == [
        "gain",
        "$(var params_dir)/vehicle.param.yaml",
        "$(var params_dir)/override.param.yaml",
        "rate_hz",
    ]


def test_launch_time_files_are_listed_in_the_launcher_data(tmp_path):
    run = run_pipeline(_stage(tmp_path), "lt_pkg/Probed.system.yaml", tmp_path)
    probe = next(child for child in run.structure("default")["data"]["children"] if child["name"] == "probe")
    files = probe["launcher"]["launch_time_param_files"]
    assert [(f["path"], f["parameter_type"]) for f in files] == [
        ("$(var params_dir)/vehicle.param.yaml", "DEFAULT_FILE"),
        ("$(var params_dir)/override.param.yaml", "OVERRIDE_FILE"),
    ]


def test_strict_build_rejects_an_undeclared_reference(tmp_path):
    workspace = _stage(tmp_path, parameter_set=False)
    system = workspace / "lt_pkg" / "Probed.system.yaml"
    system.write_text(system.read_text().replace("  - name: params_dir\n", "  - name: other_dir\n"))
    with pytest.raises(ValidationError, match=r"\$\(var params_dir\)"):
        run_pipeline(workspace, "lt_pkg/Probed.system.yaml", tmp_path, strict=True)


def test_strict_build_accepts_a_reference_a_deployment_sets(tmp_path):
    workspace = _stage(tmp_path, parameter_set=False)
    system = workspace / "lt_pkg" / "Probed.system.yaml"
    system.write_text(system.read_text().replace("  - name: params_dir\n", "  - name: other_dir\n"))
    deployments = workspace / "lt_pkg" / "probed.deployments.yaml"
    deployments.write_text(
        "autoware_system_design_format: 0.4.0\n"
        "base: Probed.system\n"
        "deploy_list:\n"
        "  - name: car_a\n"
        "    arguments:\n"
        "      - name: params_dir\n"
        "        value: /cfg/car_a\n"
    )
    run = run_pipeline(workspace, "lt_pkg/probed.deployments.yaml", tmp_path, strict=True)
    assert "$(var params_dir)/vehicle.param.yaml" in _node_params(run)


def test_included_launch_file_receives_only_launch_time_default_files(tmp_path):
    workspace = _stage(tmp_path, parameter_set=False)
    node = workspace / "lt_pkg" / "Probe.node.yaml"
    node.write_text(
        node.read_text().replace(
            "  plugin: lt::Probe\n  executable: probe_node\n", "  ros2_launch_file: probe.launch.xml\n"
        )
    )
    run = run_pipeline(workspace, "lt_pkg/Probed.system.yaml", tmp_path)
    root = ET.parse(run.launcher_dir("default") / "main_ecu" / "probe" / "probe.launch.xml").getroot()
    args = {arg.get("name"): arg.get("value") for arg in root.find("include").findall("arg")}
    assert args["vehicle_param_path"] == "$(var params_dir)/vehicle.param.yaml"
    assert "defaults_param_path" not in args


def test_strict_build_accepts_a_reference_overridden_before_the_launcher(tmp_path):
    workspace = _stage(tmp_path, parameter_set=False)
    pkg = workspace / "lt_pkg"
    (pkg / "config" / "defaults.param.yaml").write_text(_DEFAULTS + "    model: $(var model_path)/model.onnx\n")
    (pkg / "Model.parameter_set.yaml").write_text(
        "autoware_system_design_format: 0.4.0\n"
        "name: Model.parameter_set\n"
        "description: Overrides the model path of the default file.\n"
        "parameters:\n"
        "  - node: /probe\n"
        "    param_files: []\n"
        "    param_values:\n"
        "      - name: model\n"
        "        type: string\n"
        "        value: /models/model.onnx\n"
    )
    system = pkg / "Probed.system.yaml"
    system.write_text(system.read_text().replace("parameter_set: []", "parameter_set: [Model.parameter_set]"))
    run_pipeline(workspace, "lt_pkg/Probed.system.yaml", tmp_path, strict=True)


def test_strict_build_rejects_a_reference_reaching_the_launcher(tmp_path):
    workspace = _stage(tmp_path, parameter_set=False)
    pkg = workspace / "lt_pkg"
    (pkg / "config" / "defaults.param.yaml").write_text(_DEFAULTS + "    model: $(var model_path)/model.onnx\n")
    with pytest.raises(ValidationError, match=r"\$\(var model_path\)"):
        run_pipeline(workspace, "lt_pkg/Probed.system.yaml", tmp_path, strict=True)
