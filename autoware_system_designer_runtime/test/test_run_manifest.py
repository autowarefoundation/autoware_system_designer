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

from autoware_system_designer_runtime.system_runner import RUN_MANIFEST, write_run_manifest


def test_run_manifest_names_what_the_log_directory_holds(tmp_path):
    structure = tmp_path / "LoggingSimulation.json"
    structure.write_text("{}")
    path = write_run_manifest(
        tmp_path,
        json_path=str(structure),
        data={"metadata": {"system_name": "AutowareSample", "mode": "LoggingSimulation"}},
        ecu="main_ecu",
        deploy=None,
        members=["/sensing/lidar/driver", "/perception/detector"],
        launch="20260521_153012-a1b2",
    )
    assert path == tmp_path / RUN_MANIFEST
    manifest = json.loads(path.read_text())
    assert manifest["system"] == "AutowareSample"
    assert manifest["mode"] == "LoggingSimulation"
    assert manifest["compute_unit"] == "main_ecu"
    assert manifest["deploy"] is None
    assert manifest["launch"] == "20260521_153012-a1b2"
    assert manifest["system_structure"] == str(structure.resolve())
    assert manifest["started"] > 0
    assert manifest["members"] == ["/sensing/lidar/driver", "/perception/detector"]


def test_run_manifest_falls_back_to_the_structure_file_name(tmp_path):
    path = write_run_manifest(
        tmp_path, json_path="/exports/X/system_structure/Vehicle.json", data={}, ecu=None, deploy="site", members=[]
    )
    manifest = json.loads(path.read_text())
    assert manifest["system"] is None and manifest["mode"] == "Vehicle" and manifest["deploy"] == "site"
    assert manifest["launch"] is None


def test_runs_starting_in_one_second_get_their_own_log_directory(tmp_path):
    from autoware_system_designer_runtime import ensure_output_dir

    dirs = [ensure_output_dir(tmp_path) for _ in range(3)]
    assert len(set(dirs)) == 3
    assert all(d.is_dir() and d.parent == tmp_path for d in dirs)
