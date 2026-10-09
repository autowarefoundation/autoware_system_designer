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

"""System arguments stay as $(var ...) in the export and bind per deploy variant at launch."""

import json

import pytest

from autoware_system_designer_runtime._impl.ros2.common.substitutions import resolve_substitutions
from autoware_system_designer_runtime.system_runner import _bind_deploy_arguments, _parse_arg_overrides


def _export_tree(tmp_path, variants):
    exports = tmp_path / "out" / "exports" / "Solo"
    structure_dir = exports / "system_structure"
    structure_dir.mkdir(parents=True)
    (exports / "deployment.json").write_text(
        json.dumps({"argument_names": ["vehicle_id", "map_path"], "deploy_variants": variants})
    )
    return structure_dir / "default.json"


VARIANTS = [
    {
        "name": "car_a",
        "arguments": [
            {"name": "vehicle_id", "value": "car_a"},
            {"name": "map_path", "value": "$(env SOLO_MAP_ROOT)/odaiba"},
            {"name": "config_dir", "value": "/cfg/$(var vehicle_id)"},
        ],
    },
    {"name": "car_b", "arguments": [{"name": "vehicle_id", "value": "car_b"}, {"name": "map_path", "value": "/m"}]},
]

STRUCTURE = {
    "data": {
        "parameters": [
            {"name": "calibration_file", "value": "/cal/$(var vehicle_id)/lidar.csv"},
            {"name": "adapi_config", "value": "$(find-pkg-share tier4_system_launch)/adapi.param.yaml"},
        ],
        "parameter_files_all": [{"path": "$(var config_dir)/imu.param.yaml"}],
        "children": [{"parameters": [{"name": "map_path", "value": "$(var map_path)"}]}],
    }
}


def test_variant_binds_every_argument(tmp_path, monkeypatch):
    """A deploy variant resolves the arguments the way the XML deployment wrapper does."""
    monkeypatch.setenv("SOLO_MAP_ROOT", "/maps")
    structure_path = _export_tree(tmp_path, VARIANTS)

    bound = _bind_deploy_arguments(STRUCTURE, str(structure_path), deploy="car_a", overrides={})

    assert bound["data"]["parameters"][0]["value"] == "/cal/car_a/lidar.csv"
    assert bound["data"]["parameter_files_all"][0]["path"] == "/cfg/car_a/imu.param.yaml"
    assert bound["data"]["children"][0]["parameters"][0]["value"] == "/maps/odaiba"
    # Tokens other than $(var) belong to the structure and reach the node untouched.
    assert bound["data"]["parameters"][1]["value"] == "$(find-pkg-share tier4_system_launch)/adapi.param.yaml"


def test_arg_overrides_the_variant_value(tmp_path):
    structure_path = _export_tree(tmp_path, VARIANTS)

    bound = _bind_deploy_arguments(STRUCTURE, str(structure_path), deploy="car_a", overrides={"vehicle_id": "car_z"})

    assert bound["data"]["parameters"][0]["value"] == "/cal/car_z/lidar.csv"


def test_unbound_argument_names_the_variants(tmp_path):
    """Launching with a dangling $(var) would spawn nodes on bogus paths; it fails first and says how to bind."""
    structure_path = _export_tree(tmp_path, VARIANTS)

    with pytest.raises(RuntimeError, match=r"vehicle_id.*--deploy <car_a\|car_b>"):
        _bind_deploy_arguments(STRUCTURE, str(structure_path), deploy=None, overrides={})


def test_unknown_variant_is_rejected(tmp_path):
    structure_path = _export_tree(tmp_path, VARIANTS)

    with pytest.raises(RuntimeError, match="available: car_a, car_b"):
        _bind_deploy_arguments(STRUCTURE, str(structure_path), deploy="car_c", overrides={})


def test_export_without_arguments_needs_no_variant(tmp_path):
    structure_path = _export_tree(tmp_path, [])
    plain = {"data": {"parameters": [{"name": "x", "value": "$(env SOLO_X)/$(find-pkg-share solo)"}]}}

    assert _bind_deploy_arguments(plain, str(structure_path), deploy=None, overrides={}) is plain


def test_missing_manifest_is_named(tmp_path):
    structure_path = tmp_path / "exports" / "Solo" / "system_structure" / "default.json"

    with pytest.raises(RuntimeError, match=r"needs the export manifest .*deployment\.json"):
        _bind_deploy_arguments(STRUCTURE, str(structure_path), deploy="car_a", overrides={})


def test_env_default_and_nested_substitutions(monkeypatch):
    monkeypatch.delenv("SOLO_UNSET", raising=False)
    assert resolve_substitutions("$(env SOLO_UNSET fallback)/x", {}) == "fallback/x"
    assert resolve_substitutions("$(var a)", {"a": "$(var b)", "b": "deep"}) == "deep"
    assert resolve_substitutions("$(var missing)", {}) == "$(var missing)"
    monkeypatch.setenv("SOLO_SET", "/set")
    assert resolve_substitutions("$(env $(var n))", {"n": "SOLO_SET"}) == "/set"
    assert resolve_substitutions("$(env SOLO_UNSET $(var d))", {"d": "/dflt"}) == "/dflt"


def test_arg_override_syntax():
    assert _parse_arg_overrides(["a=1", "path=/x=y"]) == {"a": "1", "path": "/x=y"}
    with pytest.raises(SystemExit):
        _parse_arg_overrides(["novalue"])
