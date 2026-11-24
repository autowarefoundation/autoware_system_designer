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

from pathlib import Path

import yaml
from conftest import write_system
from pipeline_harness import _load_deployment_process

from autoware_system_designer.parser.yaml_parser import yaml_parser

NODE = """\
autoware_system_design_format: 0.3.0

name: Demo.node

package:
  name: pkg_node
  provider: dummy

launch:
  plugin: dummy::Demo

subscribers: []
publishers: []
param_files:
  - name: demo_param
    default: $(find-pkg-share pkg_node)/config/demo.param.yaml
param_values: []
processes: []
"""


def _build(tmp_path: Path, with_source_map: bool) -> str:
    """Build a one-node system whose parameter file exists only in the node's source package."""
    src = tmp_path / "ws" / "src"
    node_pkg = src / "pkg_node"
    (node_pkg / "design").mkdir(parents=True)
    (node_pkg / "design" / "Demo.node.yaml").write_text(NODE)
    (node_pkg / "config").mkdir()
    (node_pkg / "config" / "demo.param.yaml").write_text("/**:\n  ros__parameters:\n    from_source_file: 42\n")
    system = write_system(src / "pkg_system" / "design", "Tiny", "Demo")

    install = tmp_path / "ws" / "install"
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    for pkg, design in (("pkg_node", node_pkg / "design" / "Demo.node.yaml"), ("pkg_system", system)):
        manifest = {
            "package_name": pkg,
            "deploy_config_files": [{"path": str(design), "type": design.name.split(".")[-2]}],
        }
        (manifests / f"{pkg}.yaml").write_text(yaml.safe_dump(manifest))
    package_map = {
        "package_map": {pkg: str(install / pkg / "share" / pkg) for pkg in ("pkg_node", "pkg_system")},
        "workspace_root": str(tmp_path),
    }
    if with_source_map:
        package_map["source_map"] = {pkg: str(src / pkg) for pkg in ("pkg_node", "pkg_system")}
    (manifests / "_package_map.yaml").write_text(yaml.safe_dump(package_map))

    yaml_parser.clear_cache()
    out = tmp_path / "out"
    _load_deployment_process().build(str(system), str(manifests), str(out), None)
    return "\n".join(path.read_text() for path in sorted((out / "exports").rglob("*.launch.xml")))


def test_parameter_file_falls_back_to_the_package_source(tmp_path):
    launch = _build(tmp_path, with_source_map=True)

    assert 'name="from_source_file" value="42"' in launch
    # The launcher keeps the install path; the source tree is only read at build time.
    assert str(tmp_path / "ws" / "src") not in launch


def test_parameter_file_without_source_map_is_not_loaded(tmp_path):
    launch = _build(tmp_path, with_source_map=False)

    assert "from_source_file" not in launch
