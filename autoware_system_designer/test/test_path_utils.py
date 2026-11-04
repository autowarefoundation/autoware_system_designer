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
from conftest import write_node, write_system

from autoware_system_designer.common import path_utils
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


def test_read_workspace_root_returns_recorded_root(tmp_path):
    manifest_dir = tmp_path / "resource"
    _write_package_map(manifest_dir, str(tmp_path))
    assert DeploymentBuilder._read_workspace_root(str(manifest_dir)) == canonical_path(str(tmp_path))


def test_read_workspace_root_none_when_root_is_stale(tmp_path):
    manifest_dir = tmp_path / "resource"
    _write_package_map(manifest_dir, "/nonexistent/build/machine/ws")
    assert DeploymentBuilder._read_workspace_root(str(manifest_dir)) is None


def test_read_workspace_root_none_when_unrecorded(tmp_path):
    manifest_dir = tmp_path / "resource"
    _write_package_map(manifest_dir, None)
    assert DeploymentBuilder._read_workspace_root(str(manifest_dir)) is None
    assert DeploymentBuilder._read_workspace_root(str(tmp_path / "missing")) is None


def test_contract_expand_round_trip(tmp_path):
    root = str(tmp_path)
    payload = {
        f"{root}/src/pkg/design/A.node.yaml": "pkg",
        "cmd": f"$(command 'xacro {root}/install/pkg/share/pkg/vehicle.xacro')",
        "nested": [{"path": f"{root}/install/pkg/share/pkg/config/a.param.yaml"}],
        "outside": "/opt/elsewhere/x.yaml",
        "exact": root,
    }
    contracted = path_utils.contract_workspace_paths(payload, root)
    text = str(contracted)
    assert root not in text
    assert path_utils.WORKSPACE_ROOT_TOKEN in text
    assert contracted["outside"] == "/opt/elsewhere/x.yaml"
    assert contracted["exact"] == path_utils.WORKSPACE_ROOT_TOKEN
    assert path_utils.expand_workspace_paths(contracted, root) == payload


def test_contract_matches_symlinked_root_spelling(tmp_path):
    real_root = tmp_path / "real"
    (real_root / "src").mkdir(parents=True)
    link_root = tmp_path / "link"
    link_root.symlink_to(real_root)
    payload = {"a": str(real_root / "src" / "x.yaml")}
    contracted = path_utils.contract_workspace_paths(payload, str(link_root))
    assert contracted["a"] == f"{path_utils.WORKSPACE_ROOT_TOKEN}/src/x.yaml"


def test_derive_workspace_root_matches_relative_tail(tmp_path):
    out_root = tmp_path / "install" / "pkg" / "share" / "pkg"
    out_root.mkdir(parents=True)
    tokenized = f"{path_utils.WORKSPACE_ROOT_TOKEN}/install/pkg/share/pkg"
    assert path_utils.derive_workspace_root(str(out_root), tokenized) == canonical_path(str(tmp_path))


def test_derive_workspace_root_none_on_mismatch_or_absolute(tmp_path):
    assert path_utils.derive_workspace_root(str(tmp_path), f"{path_utils.WORKSPACE_ROOT_TOKEN}/other/tail") is None
    assert path_utils.derive_workspace_root(str(tmp_path), "/absolute/never/tokenized") is None


def test_derive_workspace_root_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv(path_utils.WORKSPACE_ROOT_ENV, str(tmp_path))
    assert path_utils.derive_workspace_root("/anywhere", "/absolute") == canonical_path(str(tmp_path))


def _write_package(pkg_dir: Path, name: str) -> None:
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "package.xml").write_text(f"<package><name>{name}</name></package>")


def test_collector_scans_the_deploying_package_outside_the_source_root(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    for marker in ("build", "install"):
        (ws / marker).mkdir(parents=True)
    _write_package(ws / "src" / "pkg_src", "pkg_src")
    write_node(ws / "src" / "pkg_src", "Demo", "pkg_src")
    deploying = ws / "launcher" / "pkg_launcher"
    _write_package(deploying, "pkg_launcher")
    system = write_system(deploying / "design", "Tiny", "Demo")

    out = tmp_path / "manifests"
    monkeypatch.setattr(
        "sys.argv", ["collector", str(deploying), str(out), str(ws / "install"), "--package-map-mode", "source"]
    )
    collector.main()

    manifest = yaml.safe_load((out / "pkg_launcher.yaml").read_text())
    assert [entry["path"] for entry in manifest["deploy_config_files"]] == [os.path.realpath(system)]
    package_map = yaml.safe_load((out / "_package_map.yaml").read_text())["package_map"]
    assert set(package_map) == {"pkg_src", "pkg_launcher"}
    assert (out / "pkg_src.yaml").is_file()
