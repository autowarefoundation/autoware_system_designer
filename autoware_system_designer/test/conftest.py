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

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite golden files from the current pipeline output instead of comparing",
    )


@pytest.fixture
def golden_check(request):
    """Compare text against a golden file, or rewrite it under --update-golden."""
    update = request.config.getoption("--update-golden")

    def check(golden_path: Path, actual_text: str) -> None:
        if update:
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(actual_text)
            return
        assert golden_path.is_file(), f"Missing golden file {golden_path}; run pytest with --update-golden to create it"
        expected = golden_path.read_text()
        assert actual_text == expected, (
            f"Output differs from golden {golden_path}; " f"rerun with --update-golden if the change is intended"
        )

    return check


NODE_TEMPLATE = """\
autoware_system_design_format: 0.3.0

name: {name}.node

package:
  name: {package}
  provider: dummy

launch:
  plugin: dummy::{name}

subscribers: []
publishers: []
param_files: []
param_values: []
processes: []
"""

SYSTEM_TEMPLATE = """\
autoware_system_design_format: 0.3.0

name: {name}.system

modes:
  - name: default
    description: Default mode
    default: true

components:
  - name: demo
    entity: {node}.node
    compute_unit: main_ecu

connections: []
"""


def write_node(pkg_dir: Path, name: str, package: str) -> Path:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    path = pkg_dir / f"{name}.node.yaml"
    path.write_text(NODE_TEMPLATE.format(name=name, package=package))
    return path


def write_system(pkg_dir: Path, name: str, node: str) -> Path:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    path = pkg_dir / f"{name}.system.yaml"
    path.write_text(SYSTEM_TEMPLATE.format(name=name, node=node))
    return path


@pytest.fixture
def duplicate_workspace(tmp_path):
    """Two packages declaring the same node name; pkg_a also declares the system."""
    pkg_a = tmp_path / "pkg_a"
    pkg_b = tmp_path / "pkg_b"
    files = {
        "node_a": write_node(pkg_a, "Demo", "pkg_a"),
        "node_b": write_node(pkg_b, "Demo", "pkg_b"),
        "system_a": write_system(pkg_a, "Tiny", "Demo"),
    }
    file_package_map = {
        str(files["node_a"]): "pkg_a",
        str(files["node_b"]): "pkg_b",
        str(files["system_a"]): "pkg_a",
    }
    return {
        "root": tmp_path,
        "pkg_a": pkg_a,
        "pkg_b": pkg_b,
        "files": files,
        "file_paths": [str(path) for path in files.values()],
        "file_package_map": file_package_map,
    }
