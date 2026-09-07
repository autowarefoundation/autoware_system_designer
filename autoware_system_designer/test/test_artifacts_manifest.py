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

"""The exported artifacts manifest is a complete generator input.

Every generator must run from load_build_artifacts alone — no registry, no
rebuild — and reproduce the outputs of the original pipeline run.
"""

import shutil

from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.common.path_utils import WORKSPACE_ROOT_TOKEN
from autoware_system_designer.deploy import (
    _collect_deploy_variable_names,
    generate_build_scripts,
    generate_launchers,
    generate_system_monitor_config,
    load_build_artifacts,
)


def _tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def test_generators_rerun_from_manifest_alone(tmp_path):
    workspace = stage_case("deployments_table", tmp_path)
    run = run_pipeline(workspace, "fleet_pkg/deployment/fleet.deployments.yaml", tmp_path)

    artifacts = load_build_artifacts(str(run.out_root), run.system_name)
    assert artifacts.config_registry is None
    assert artifacts.mode_keys == ["default"]
    assert artifacts.deploy_variants, "deploy-list metadata must survive the manifest round-trip"
    assert "vehicle_id" in _collect_deploy_variable_names(artifacts)

    launcher_dir = run.exports_dir / "launcher"
    monitor_dir = run.exports_dir / "system_monitor"
    scripts_dir = run.exports_dir / "build_scripts"
    before = {name: _tree(d) for name, d in (("l", launcher_dir), ("m", monitor_dir), ("s", scripts_dir))}

    shutil.rmtree(launcher_dir)
    shutil.rmtree(monitor_dir)
    shutil.rmtree(scripts_dir)

    generate_launchers(artifacts)
    generate_system_monitor_config(artifacts)
    generate_build_scripts(artifacts)

    after = {name: _tree(d) for name, d in (("l", launcher_dir), ("m", monitor_dir), ("s", scripts_dir))}
    assert after == before


def test_export_json_holds_no_absolute_workspace_paths(tmp_path):
    """system_structure files carry the workspace token, never machine paths."""
    workspace = stage_case("single_node", tmp_path)
    run = run_pipeline(workspace, "demo_pkg/Solo.system.yaml", tmp_path)

    structure_dir = run.exports_dir / "system_structure"
    json_files = sorted(structure_dir.glob("*.json"))
    assert json_files
    for json_file in json_files:
        text = json_file.read_text()
        for spelling in {str(tmp_path), str(tmp_path.resolve())}:
            assert spelling not in text, f"absolute workspace path leaked into {json_file.name}"

    artifacts = load_build_artifacts(str(run.out_root), run.system_name)
    assert artifacts.workspace_root == str(tmp_path.resolve())
    assert artifacts.system_file.startswith(str(tmp_path.resolve()))


def test_mode_named_deployment_keeps_its_structure_export(tmp_path):
    """The manifest lives outside the mode namespace, so no mode name can claim its file."""
    workspace = stage_case("modes_variant", tmp_path)
    system_file = workspace / "variant_pkg" / "Base.system.yaml"
    system_file.write_text(system_file.read_text().replace("simulation", "deployment"))

    run = run_pipeline(workspace, "variant_pkg/VehicleY.system.yaml", tmp_path)

    structure = run.structure("deployment")
    assert structure["data"], "the mode export must survive the artifacts manifest write"
    assert (run.exports_dir / "deployment.json").is_file()

    artifacts = load_build_artifacts(str(run.out_root), run.system_name)
    assert "deployment" in artifacts.mode_keys


def test_export_outside_workspace_stays_absolute(tmp_path):
    """An export outside the workspace cannot re-derive the root, so it carries no token."""
    workspace = stage_case("single_node", tmp_path)
    # The harness records tmp_path as the workspace root; export beside it, not under it.
    out_root = tmp_path.parent / f"{tmp_path.name}_external_export"
    shutil.rmtree(out_root, ignore_errors=True)
    try:
        run = run_pipeline(workspace, "demo_pkg/Solo.system.yaml", tmp_path, out_root=out_root)

        for json_file in sorted(run.exports_dir.rglob("*.json")):
            assert WORKSPACE_ROOT_TOKEN not in json_file.read_text(), f"{json_file.name} is unresolvable here"

        artifacts = load_build_artifacts(str(run.out_root), run.system_name)
        assert artifacts.workspace_root is None
        assert artifacts.system_file.startswith(str(tmp_path.resolve()))
        generate_launchers(artifacts)
    finally:
        shutil.rmtree(out_root, ignore_errors=True)
