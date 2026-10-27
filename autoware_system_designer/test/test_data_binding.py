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

from types import SimpleNamespace

import pytest

from autoware_system_designer.builder.parameters import data_binding_applier as dba
from autoware_system_designer.builder.resolution.bundle_version import parse_bundle_version
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.model.config import DataConfig
from autoware_system_designer.model.execution import LaunchState


def make_bundle(tmp_path, version=None, **files):
    """A resolved ml_model bundle at ``tmp_path`` holding ``files`` (name -> text)."""
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    config = DataConfig(
        name="Model",
        full_name="Model.data",
        entity_type="data",
        config={},
        file_path=str(tmp_path / "Model.data.yaml"),
        category="ml_model",
        variants=[{"name": "model_variant", "values": ["base", "tiny"], "default": "base"}],
        path_pattern="model/$(var model_variant)",
    )
    return dba._ResolvedBundle(
        config=config,
        directory=str(tmp_path),
        variant={"model_variant": "base"},
        version=parse_bundle_version(version) if version else None,
        version_source="deploy_metadata.yaml:version" if version else None,
    )


class _RecordingParameterManager:
    def __init__(self):
        self.calls = []

    def apply_node_parameters(self, node_path, param_files, param_values, config_registry, **kwargs):
        self.calls.append((param_files, param_values))


def make_node(launch_state=LaunchState.COMPOSABLE_NODE):
    return SimpleNamespace(
        path="/perception/detector",
        parameter_manager=_RecordingParameterManager(),
        launch_manager=SimpleNamespace(launch_config=SimpleNamespace(launch_state=launch_state)),
    )


# ── bundle: namespace ────────────────────────────────────────────────────────


def test_bundle_path_resolves_to_the_bundle_directory(tmp_path):
    bundle = make_bundle(tmp_path)
    assert dba._resolve_reference("bundle:path", bundle, "n") == (str(tmp_path), "string")


def test_bundle_variant_axis_resolves_to_the_selected_value(tmp_path):
    bundle = make_bundle(tmp_path)
    assert dba._resolve_reference("bundle:variant.model_variant", bundle, "n") == ("base", None)


def test_bundle_variant_unknown_axis_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="unknown variant axis 'nope'"):
        dba._resolve_reference("bundle:variant.nope", make_bundle(tmp_path), "n")


def test_bundle_unknown_key_lists_the_valid_keys(tmp_path):
    with pytest.raises(ValidationError, match=r"unknown bundle key 'root'.*'path'.*'variant\.<axis>'"):
        dba._resolve_reference("bundle:root", make_bundle(tmp_path), "n")


def test_bundle_version_resolves_to_the_recorded_string(tmp_path):
    bundle = make_bundle(tmp_path, version="v4.1")
    assert dba._resolve_reference("bundle:version", bundle, "n") == ("v4.1", "string")


def test_bundle_version_on_a_versionless_bundle_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="records no version"):
        dba._resolve_reference("bundle:version", make_bundle(tmp_path), "n")


def test_unknown_namespace_is_rejected(tmp_path):
    with pytest.raises(ValidationError, match="expected 'bundle:<key>'"):
        dba._resolve_reference("nope:x", make_bundle(tmp_path), "n")


def test_type_check_is_skipped_when_the_entry_declares_none(tmp_path):
    dba._check_data_type({"entity": "Model.data"}, make_bundle(tmp_path).config, "/n")
    with pytest.raises(ValidationError, match="declares type 'map'"):
        dba._check_data_type({"entity": "Model.data", "type": "map"}, make_bundle(tmp_path).config, "/n")


# ── binding application ──────────────────────────────────────────────────────


def test_bundle_path_is_injected_as_an_override_parameter(tmp_path):
    bundle = make_bundle(tmp_path)
    node = make_node()
    entry = {"entity": "Model.data", "binding": {"param_values": [{"name": "model_path", "from": "bundle:path"}]}}
    dba._apply_entry_to_node(node, entry, bundle, config_registry=None, source=None)
    ((_, param_values),) = node.parameter_manager.calls
    assert param_values == [{"name": "model_path", "value": str(tmp_path), "type": "string"}]


def test_dotted_parameter_on_a_wrapper_launched_node_is_rejected(tmp_path):
    bundle = make_bundle(tmp_path)
    node = make_node(LaunchState.ROS2_LAUNCH_FILE)
    entry = {
        "entity": "Model.data",
        "binding": {"param_values": [{"name": "model.base_model_directory", "from": "bundle:path"}]},
    }
    with pytest.raises(ValidationError, match="launched through a ros2_launch_file"):
        dba._apply_entry_to_node(node, entry, bundle, config_registry=None, source=None)
    assert node.parameter_manager.calls == []


def test_dotted_parameter_on_a_directly_launched_node_is_accepted(tmp_path):
    bundle = make_bundle(tmp_path)
    node = make_node(LaunchState.SINGLE_NODE)
    entry = {
        "entity": "Model.data",
        "binding": {"param_values": [{"name": "model.base_model_directory", "from": "bundle:path"}]},
    }
    dba._apply_entry_to_node(node, entry, bundle, config_registry=None, source=None)
    assert len(node.parameter_manager.calls) == 1


# ── requires_version ─────────────────────────────────────────────────────────


def test_satisfied_requirement_passes(tmp_path):
    entry = {"entity": "Model.data", "requires_version": {"major": 4, "min_minor": 1}}
    dba._check_required_version(entry, make_bundle(tmp_path, version="v4.2"), "inst", "/n")


def test_unsatisfied_requirement_names_everything_involved(tmp_path):
    entry = {"entity": "Model.data", "requires_version": {"major": 4, "min_minor": 1}}
    with pytest.raises(ValidationError) as excinfo:
        dba._check_required_version(entry, make_bundle(tmp_path, version="v3.9"), "ml_model_data", "/perception/d")
    message = str(excinfo.value)
    for fragment in (
        "Model",
        "ml_model_data",
        "/perception/d",
        "major 4, minor >= 1",
        str(tmp_path),
        "v3.9",
        "deploy_metadata.yaml:version",
        "model_variant",
    ):
        assert fragment in message


def test_requirement_on_a_versionless_bundle_is_an_error(tmp_path):
    entry = {"entity": "Model.data", "requires_version": {"major": 4}}
    with pytest.raises(ValidationError, match="records no version"):
        dba._check_required_version(entry, make_bundle(tmp_path), "inst", "/n")


def test_entry_without_requirement_skips_the_check(tmp_path):
    dba._check_required_version({"entity": "Model.data"}, make_bundle(tmp_path), "inst", "/n")


def test_missing_manifest_file_is_fatal_when_the_entity_declares_one(tmp_path):
    bundle = make_bundle(tmp_path)
    bundle.config.manifest = {"file": "ml_package.param.yaml", "version_key": ["/**", "ros__parameters", "version"]}
    with pytest.raises(ValidationError, match="could not read the bundle version.*is missing from bundle"):
        dba._read_version(bundle.config, tmp_path, "inst", None)


def test_absent_default_manifest_file_yields_no_version(tmp_path):
    bundle = make_bundle(tmp_path)
    assert dba._read_version(bundle.config, tmp_path, "inst", None) == (None, None)


# ── requires_paths ───────────────────────────────────────────────────────────


def test_required_paths_are_existence_checked(tmp_path):
    bundle = make_bundle(tmp_path, **{"deploy_metadata.yaml": "version: v4.1\nonnx_path: model.onnx\n"})
    entry = {"entity": "Model.data", "requires_paths": ["onnx_path"]}
    with pytest.raises(ValidationError) as excinfo:
        dba._check_required_paths(entry, bundle, "ml_model_data", "/perception/d")
    message = str(excinfo.value)
    for fragment in ("Model", "ml_model_data", "/perception/d", "onnx_path", "names a file missing"):
        assert fragment in message
    (tmp_path / "model.onnx").write_text("")
    dba._check_required_paths(entry, bundle, "ml_model_data", "/perception/d")


def test_entry_without_required_paths_skips_the_check(tmp_path):
    dba._check_required_paths({"entity": "Model.data"}, make_bundle(tmp_path), "inst", "/n")


def test_required_paths_need_the_manifest_file(tmp_path):
    entry = {"entity": "Model.data", "requires_paths": ["onnx_path"]}
    with pytest.raises(ValidationError, match="is missing from bundle"):
        dba._check_required_paths(entry, make_bundle(tmp_path), "inst", "/n")


# ── entity lookup diagnostics ────────────────────────────────────────────────


def test_missing_data_entity_names_the_expected_location_and_the_fix():
    class _Registry:
        def get_data(self, name):
            raise ValidationError(f"Data '{name}' not found. Available datas: []")

    with pytest.raises(ValidationError) as excinfo:
        dba._get_data_entity(_Registry(), "LidarCenterPointModel", "ml_model_data", None)
    message = str(excinfo.value)
    assert "<pkg>/design/data/LidarCenterPointModel.data.yaml" in message
    assert "colcon build --packages-select autoware_system_designer" in message
