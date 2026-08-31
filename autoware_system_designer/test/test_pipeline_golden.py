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

"""Golden-file pipeline tests: fixture designs in, exported artifacts out.

Each case runs the full pipeline in-process and compares normalized outputs
against files in test/golden/<case>/. Regenerate with:

    pytest test/test_pipeline_golden.py --update-golden
"""

import pytest
from pipeline_harness import (
    GOLDEN_DIR,
    dump_canonical,
    normalize,
    normalize_text,
    run_pipeline,
    stage_case,
)

# case name -> (deployment target, modes to snapshot)
CASES = {
    "single_node": ("demo_pkg/Solo.system.yaml", ["default"]),
    "module_pipeline": ("pipeline_pkg/Chain.system.yaml", ["default"]),
    "modes_variant": ("variant_pkg/VehicleY.system.yaml", ["default", "simulation"]),
    "parameter_sets": ("tuned_pkg/Tuned.system.yaml", ["default"]),
    "launch_wrapper": ("wrapper_pkg/Wrapped.system.yaml", ["default"]),
    "deployments_table": ("fleet_pkg/deployment/fleet.deployments.yaml", ["default"]),
}

# Text artifacts snapshotted alongside the structure JSON, per case:
# relative to exports/<system>/, with {mode} substituted.
TEXT_ARTIFACTS = {
    "single_node": [
        "launcher/{mode}/main_ecu/main_ecu.launch.xml",
        "launcher/{mode}/main_ecu/talker/talker.launch.xml",
        "system_monitor/{mode}/component_state_monitor/topics.yaml",
        "parameter_set/{mode}/Solo__talker.parameter_set.yaml",
    ],
    "module_pipeline": [
        "launcher/{mode}/main_ecu/main_ecu.launch.xml",
        "launcher/{mode}/main_ecu/pipeline/pipeline.launch.xml",
    ],
    "launch_wrapper": ["launcher/{mode}/main_ecu/map/map.launch.xml"],
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_pipeline_golden(case, tmp_path, golden_check):
    target, modes = CASES[case]
    workspace = stage_case(case, tmp_path)
    run = run_pipeline(workspace, target, tmp_path)

    replacements = run.replacements()
    for mode in modes:
        structure = normalize(run.structure(mode), replacements)
        golden_check(GOLDEN_DIR / case / f"{mode}.json", dump_canonical(structure))

        for artifact in TEXT_ARTIFACTS.get(case, []):
            rel = artifact.format(mode=mode)
            path = run.exports_dir / rel
            assert path.is_file(), f"expected generated artifact {rel}"
            text = normalize_text(path.read_text(), replacements)
            golden_name = rel.replace("/", "__")
            golden_check(GOLDEN_DIR / case / golden_name, text)


def test_parameter_set_template_exported(tmp_path):
    """Parameter set export produces per-node templates for the tuned system."""
    workspace = stage_case("parameter_sets", tmp_path)
    run = run_pipeline(workspace, "tuned_pkg/Tuned.system.yaml", tmp_path)
    files = sorted(p.name for p in run.parameter_set_dir("default").rglob("*") if p.is_file())
    assert files, "no parameter set templates were exported"


def test_deployments_table_generates_deploy_launchers(tmp_path):
    """Each deploy-list entry gets a launcher entry point."""
    workspace = stage_case("deployments_table", tmp_path)
    run = run_pipeline(workspace, "fleet_pkg/deployment/fleet.deployments.yaml", tmp_path)
    launcher_files = [
        str(p.relative_to(run.exports_dir)) for p in (run.exports_dir / "launcher").rglob("*") if p.is_file()
    ]
    for vehicle in ("vehicle_a", "vehicle_b"):
        assert any(vehicle in f for f in launcher_files), f"no launcher generated for {vehicle}: {launcher_files}"
