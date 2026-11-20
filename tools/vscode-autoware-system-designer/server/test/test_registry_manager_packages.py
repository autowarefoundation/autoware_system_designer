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

from types import SimpleNamespace

from registry_manager import RegistryManager


def _config(path, package=None):
    return SimpleNamespace(full_name="Demo.node", file_path=path, package=package)


def test_package_qualified_reference_distinguishes_same_named_entities(tmp_path):
    for pkg in ("pkg_a", "pkg_b"):
        (tmp_path / pkg).mkdir()
        (tmp_path / pkg / "package.xml").write_text(f"<package><name>{pkg}</name></package>")
    manager = RegistryManager()
    node_a = _config(tmp_path / "pkg_a" / "Demo.node.yaml")
    node_b = _config(tmp_path / "pkg_b" / "Demo.node.yaml")
    manager.register_entity(node_a)
    manager.register_entity(node_b)
    assert manager.get_entity("Demo.node") is node_b
    assert manager.get_entity("pkg_a/Demo.node") is node_a
    assert manager.get_entity("pkg_b/Demo.node") is node_b
    assert manager.get_entity("pkg_c/Demo.node") is None
    manager.unregister_entity(str(node_a.file_path))
    assert manager.get_entity("pkg_a/Demo.node") is None
    assert manager.get_entity("Demo.node") is node_b
