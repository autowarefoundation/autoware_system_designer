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

"""Per-stage structure snapshots are exported on request and for the stage a build fails at."""

import pytest
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.common.exceptions import SystemDesignerError

STAGE_FLAG = "AUTOWARE_SYSTEM_DESIGNER_STAGE_SNAPSHOTS"


def _structure_files(exports_dir):
    return sorted(path.name for path in (exports_dir / "system_structure").glob("*.json"))


def test_successful_build_exports_only_the_final_structure(tmp_path, monkeypatch):
    monkeypatch.delenv(STAGE_FLAG, raising=False)
    workspace = stage_case("single_node", tmp_path)

    run = run_pipeline(workspace, "demo_pkg/Solo.system.yaml", tmp_path)

    assert _structure_files(run.exports_dir) == ["default.json"]


def test_stage_snapshots_are_exported_on_request(tmp_path, monkeypatch):
    monkeypatch.setenv(STAGE_FLAG, "1")
    workspace = stage_case("single_node", tmp_path)

    run = run_pipeline(workspace, "demo_pkg/Solo.system.yaml", tmp_path)

    assert _structure_files(run.exports_dir) == [
        "default.json",
        "default_1_parse.json",
        "default_2_connections.json",
        "default_3_events.json",
    ]


def test_failed_build_exports_the_failing_stage(tmp_path, monkeypatch):
    monkeypatch.delenv(STAGE_FLAG, raising=False)
    workspace = stage_case("errors_bad_rate", tmp_path)

    with pytest.raises(SystemDesignerError):
        run_pipeline(workspace, "rate_pkg/Rate.system.yaml", tmp_path)

    snapshots = list((tmp_path / "out").rglob("system_structure/*.json"))
    assert len(snapshots) == 1
    assert '"error"' in snapshots[0].read_text()
