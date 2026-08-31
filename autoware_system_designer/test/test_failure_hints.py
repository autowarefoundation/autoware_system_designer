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

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from conftest import write_node, write_system

from autoware_system_designer.builder.deploy import Deployment
from autoware_system_designer.common.deployment_config import DeploymentConfig

_PROCESS_PATH = Path(__file__).resolve().parents[1] / "script" / "deployment_process.py"
_spec = importlib.util.spec_from_file_location("deployment_process", _PROCESS_PATH)
deployment_process = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(deployment_process)


def test_construction_failure_carries_registry_on_exception(tmp_path):
    pkg_a = tmp_path / "pkg_a"
    system_file = write_system(pkg_a, "Broken", "Missing")
    node_file = write_node(pkg_a, "Demo", "pkg_a")
    manifest_dir = tmp_path / "resource"
    manifest_dir.mkdir()
    (manifest_dir / "pkg_a.yaml").write_text(
        yaml.safe_dump(
            {
                "package_name": "pkg_a",
                "deploy_config_files": [
                    {"path": str(system_file), "type": "system"},
                    {"path": str(node_file), "type": "node"},
                ],
            }
        )
    )

    config = DeploymentConfig()
    config.deployment_file = "Broken.system"
    config.manifest_dir = str(manifest_dir)
    config.output_root_dir = str(tmp_path / "out")

    with pytest.raises(Exception) as excinfo:
        Deployment(config)
    assert getattr(excinfo.value, "config_registry", None) is not None


def test_find_registry_prefers_the_exception_attachment():
    exc = RuntimeError("boom")
    exc.config_registry = "registry-from-exception"
    assert deployment_process._find_registry(None, exc) == "registry-from-exception"


def test_find_registry_falls_back_to_the_deployment():
    exc = RuntimeError("boom")
    deployment = SimpleNamespace(config_registry="registry-from-deployment")
    assert deployment_process._find_registry(deployment, exc) == "registry-from-deployment"
    assert deployment_process._find_registry(None, exc) is None
