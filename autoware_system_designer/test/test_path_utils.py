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
import os
from pathlib import Path

import yaml

from autoware_system_designer.common.path_utils import canonical_path, resolve_manifest_path
from autoware_system_designer.deploy import DeploymentBuilder

_COLLECTOR_PATH = Path(__file__).resolve().parents[1] / "script" / "collect_system_design_manifests.py"
_spec = importlib.util.spec_from_file_location("collect_system_design_manifests", _COLLECTOR_PATH)
collector = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collector)


def test_canonical_path_unifies_symlinked_paths(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    (real_dir / "x.yaml").write_text("")
    link = tmp_path / "link"
    link.symlink_to(real_dir)
    assert canonical_path(str(link / "x.yaml")) == canonical_path(str(real_dir / "x.yaml"))


def test_resolve_manifest_path_joins_relative_entries(tmp_path):
    resolved = resolve_manifest_path(os.path.join("pkg_a", "x.yaml"), str(tmp_path))
    assert resolved == canonical_path(str(tmp_path / "pkg_a" / "x.yaml"))


def test_resolve_manifest_path_keeps_absolute_entries(tmp_path):
    target = tmp_path / "pkg_a" / "x.yaml"
    assert resolve_manifest_path(str(target), "/elsewhere") == canonical_path(str(target))


def test_to_manifest_path_relativizes_inside_anchor(tmp_path):
    target = tmp_path / "pkg_a" / "x.yaml"
    assert collector.to_manifest_path(str(target), str(tmp_path)) == os.path.join("pkg_a", "x.yaml")


def test_to_manifest_path_keeps_paths_outside_anchor_absolute(tmp_path):
    outside = "/opt/elsewhere/x.yaml"
    assert collector.to_manifest_path(outside, str(tmp_path)) == outside


def test_to_manifest_path_without_anchor_is_identity():
    assert collector.to_manifest_path("/ws/pkg_a/x.yaml", None) == "/ws/pkg_a/x.yaml"


def test_manifest_round_trip(tmp_path):
    target = tmp_path / "pkg_a" / "x.yaml"
    entry = collector.to_manifest_path(str(target), str(tmp_path))
    assert resolve_manifest_path(entry, str(tmp_path)) == canonical_path(str(target))


def _write_package_map(manifest_dir: Path, workspace_root):
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {"package_map": {}}
    if workspace_root is not None:
        payload["workspace_root"] = workspace_root
    (manifest_dir / "_package_map.yaml").write_text(yaml.safe_dump(payload))


def test_read_manifest_anchor_returns_recorded_root(tmp_path):
    manifest_dir = tmp_path / "resource"
    _write_package_map(manifest_dir, str(tmp_path))
    assert DeploymentBuilder._read_manifest_anchor(str(manifest_dir)) == str(tmp_path)


def test_read_manifest_anchor_falls_back_when_root_is_stale(tmp_path):
    manifest_dir = tmp_path / "resource"
    _write_package_map(manifest_dir, "/nonexistent/build/machine/ws")
    assert DeploymentBuilder._read_manifest_anchor(str(manifest_dir)) == str(manifest_dir)


def test_read_manifest_anchor_falls_back_when_unrecorded(tmp_path):
    manifest_dir = tmp_path / "resource"
    _write_package_map(manifest_dir, None)
    assert DeploymentBuilder._read_manifest_anchor(str(manifest_dir)) == str(manifest_dir)
    assert DeploymentBuilder._read_manifest_anchor(str(tmp_path / "missing")) == str(tmp_path / "missing")
