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

"""End-to-end error-path tests over broken fixture designs."""

import pytest
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.common.exceptions import SystemDesignerError


def test_missing_entity_fails_with_entity_name(tmp_path):
    workspace = stage_case("errors_missing_entity", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "broken_pkg/Broken.system.yaml", tmp_path)
    assert "Ghost" in str(excinfo.value)


def test_circular_module_fails(tmp_path):
    workspace = stage_case("errors_circular", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "loop_pkg/Loopy.system.yaml", tmp_path)
    message = str(excinfo.value).lower()
    assert "circular" in message or "too deep" in message


def test_duplicate_entity_strict_fails(tmp_path):
    workspace = stage_case("errors_duplicate", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "pkg_a/Dup.system.yaml", tmp_path, strict=True)
    assert "duplicated" in str(excinfo.value).lower()
    assert "Echo" in str(excinfo.value)


def test_duplicate_entity_non_strict_builds(tmp_path):
    workspace = stage_case("errors_duplicate", tmp_path)
    run = run_pipeline(workspace, "pkg_a/Dup.system.yaml", tmp_path, strict=False)
    assert (run.exports_dir / "system_structure" / "default.json").is_file()


def test_bad_major_version_fails(tmp_path):
    workspace = stage_case("errors_bad_major", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "major_pkg/Major.system.yaml", tmp_path)
    assert "format version" in str(excinfo.value).lower()


def test_minor_version_hint_surfaces_on_failure(tmp_path, capfd):
    workspace = stage_case("errors_minor_hint", tmp_path)
    with pytest.raises(SystemDesignerError):
        run_pipeline(workspace, "hint_pkg/Hint.system.yaml", tmp_path)
    stderr = capfd.readouterr().err
    assert "newer minor format version" in stderr, "expected the minor-version hint on stderr"
