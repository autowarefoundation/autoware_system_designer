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

"""The runtime reads the export tree the design-time tool writes."""

import json

import pytest

from autoware_system_designer_runtime._impl.ros2.common.params import resolve_value
from autoware_system_designer_runtime.system_runner import _resolve_structure_paths

TOKEN = "${workspace_root}"


def _export_tree(tmp_path, tokenized_package_path):
    """An export tree at <root>/ws/out/exports/Solo, mirroring ExportLayout."""
    exports = tmp_path / "ws" / "out" / "exports" / "Solo"
    structure_dir = exports / "system_structure"
    structure_dir.mkdir(parents=True)
    (exports / "deployment.json").write_text(json.dumps({"deployment_package_path": tokenized_package_path}))
    structure_path = structure_dir / "default.json"
    structure_path.write_text(json.dumps({"system_file": TOKEN + "/ws/Solo.system.yaml"}))
    return structure_path


def test_workspace_token_resolves_against_the_manifest(tmp_path):
    """The manifest sits beside system_structure, and its tail anchors the root."""
    structure_path = _export_tree(tmp_path, TOKEN + "/ws/out")
    data = json.loads(structure_path.read_text())

    resolved = _resolve_structure_paths(data, str(structure_path))

    assert resolved["system_file"] == f"{tmp_path.resolve()}/ws/Solo.system.yaml"


def test_untokenized_export_needs_no_manifest(tmp_path):
    """An export written outside the workspace carries absolute paths and loads as-is."""
    absolute = {"system_file": f"{tmp_path}/ws/Solo.system.yaml"}
    assert _resolve_structure_paths(absolute, str(tmp_path / "default.json")) == absolute


def test_unresolvable_token_is_reported(tmp_path):
    """A token with no manifest to anchor it fails loudly instead of launching wrong paths."""
    structure_path = _export_tree(tmp_path, "/elsewhere/out")
    data = json.loads(structure_path.read_text())

    with pytest.raises(RuntimeError, match="workspace root"):
        _resolve_structure_paths(data, str(structure_path))


@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("False", False), ("0", False), ("on", True)],
)
def test_bool_hint_coerces_written_values(value, expected):
    assert resolve_value(value, "bool") is expected


def test_empty_bool_value_is_not_false():
    """An empty value carries no truth; coercing it would hide a missing parameter."""
    assert resolve_value("", "bool") == ""
