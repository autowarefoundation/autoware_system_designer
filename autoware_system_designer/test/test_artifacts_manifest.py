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
