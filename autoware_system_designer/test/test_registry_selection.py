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

from autoware_system_designer.builder.config.config_registry import ConfigRegistry, _package_distance
from autoware_system_designer.builder.deploy import Deployment


def _registry(workspace, anchor_dir=None):
    return ConfigRegistry(
        workspace["file_paths"],
        file_package_map=workspace["file_package_map"],
        anchor_dir=anchor_dir,
    )


def test_anchor_proximity_selects_the_near_candidate(duplicate_workspace):
    registry = _registry(duplicate_workspace, anchor_dir=str(duplicate_workspace["pkg_b"]))
    node = registry.get_node("Demo")
    assert str(node.file_path) == str(duplicate_workspace["files"]["node_b"])


def test_preferred_package_beats_proximity(duplicate_workspace):
    registry = _registry(duplicate_workspace, anchor_dir=str(duplicate_workspace["pkg_b"]))
    registry.deployment_package_name = "pkg_a"
    node = registry.get_node("Demo")
    assert str(node.file_path) == str(duplicate_workspace["files"]["node_a"])


def test_symlinked_anchor_ranks_against_real_candidate_paths(duplicate_workspace):
    link = duplicate_workspace["root"] / "ws_link"
    link.symlink_to(duplicate_workspace["root"])
    registry = _registry(duplicate_workspace, anchor_dir=str(link / "pkg_b"))
    node = registry.get_node("Demo")
    assert str(node.file_path) == str(duplicate_workspace["files"]["node_b"])


def _make_package(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "package.xml").write_text("<package><name>x</name></package>")
    return root


def test_package_distance_ignores_intra_package_layout(tmp_path):
    pkg = _make_package(tmp_path / "src" / "launcher" / "pkg")
    anchor = pkg / "design" / "system"
    shallow = pkg / "design" / "a.node.yaml"
    deep = pkg / "design" / "system" / "deep" / "er" / "b.node.yaml"
    for path in (anchor, deep.parent):
        path.mkdir(parents=True, exist_ok=True)
    shallow.touch()
    deep.touch()
    assert _package_distance(anchor, shallow) == 0
    assert _package_distance(anchor, deep) == 0


def test_package_distance_follows_repository_layout(tmp_path):
    anchor_pkg = _make_package(tmp_path / "src" / "launcher" / "autoware_launch" / "designs")
    sibling = _make_package(tmp_path / "src" / "launcher" / "autoware_launch" / "other") / "design" / "x.node.yaml"
    far = _make_package(tmp_path / "src" / "universe" / "autoware_universe" / "perception" / "pkg") / "x.node.yaml"
    sibling.parent.mkdir(parents=True)
    sibling.touch()
    far.touch()
    anchor = anchor_pkg / "design" / "system"
    anchor.mkdir(parents=True)
    assert _package_distance(anchor, sibling) == 2
    assert _package_distance(anchor, far) == 7
    assert _package_distance(anchor, sibling) < _package_distance(anchor, far)


def test_iter_used_configs_yields_only_resolved_selections(duplicate_workspace):
    registry = _registry(duplicate_workspace, anchor_dir=str(duplicate_workspace["pkg_b"]))
    assert list(registry.iter_used_configs()) == []
    node_group = registry.entities["Demo.node"]
    node_group.choose(registry.selection_policy)
    assert list(registry.iter_used_configs()) == []
    registry.get_node("Demo")
    used = list(registry.iter_used_configs())
    assert [str(config.file_path) for config in used] == [str(duplicate_workspace["files"]["node_b"])]
    assert not registry.system_group("Tiny").used


def test_system_group_accepts_short_and_full_names(duplicate_workspace):
    registry = _registry(duplicate_workspace)
    assert registry.system_group("Tiny") is registry.system_group("Tiny.system")
    assert registry.system_group("Missing") is None


def test_finalize_selection_policy_pins_anchor_and_package_for_bare_names(duplicate_workspace):
    registry = _registry(duplicate_workspace)
    assert registry.anchor_dir is None
    Deployment._finalize_selection_policy("Tiny.system", registry, duplicate_workspace["file_package_map"])
    assert registry.anchor_dir == str(Path(str(duplicate_workspace["files"]["system_a"])).parent)
    assert registry.deployment_package_name == "pkg_a"
    node = registry.get_node("Demo")
    assert str(node.file_path) == str(duplicate_workspace["files"]["node_a"])


def test_finalize_selection_policy_leaves_unknown_targets_untouched(duplicate_workspace):
    registry = _registry(duplicate_workspace)
    Deployment._finalize_selection_policy("Missing.system", registry, duplicate_workspace["file_package_map"])
    assert registry.anchor_dir is None
    assert registry.deployment_package_name is None
