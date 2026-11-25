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


def _index_package(prefix: Path, name: str) -> None:
    index_dir = prefix / "share" / "ament_index" / "resource_index" / "packages"
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / name).write_text("")
    (prefix / "share" / name).mkdir(parents=True, exist_ok=True)


def _scoped_workspace(tmp_path: Path) -> dict:
    """Deploying package in a non-src workspace layer, with packages in, beside and outside the scope."""
    ws = tmp_path / "ws"
    install = ws / "install"
    (ws / "build").mkdir(parents=True)
    _write_package(ws / "src" / "pkg_src", "pkg_src")
    deploying = ws / "launcher" / "pkg_launcher"
    _write_package(deploying, "pkg_launcher")
    write_system(deploying / "design", "Tiny", "Demo")
    _write_package(ws / "launcher" / "vehicle" / "pkg_vehicle", "pkg_vehicle")
    # Another workspace layer and an underlay, both outside the scope; only known by their install.
    _write_package(ws / "other" / "pkg_other", "pkg_other")
    _index_package(install / "pkg_other", "pkg_other")
    _index_package(install / "pkg_launcher", "pkg_launcher")
    underlay = tmp_path / "underlay"
    _index_package(underlay, "pkg_underlay")
    _index_package(underlay, "pkg_src")
    return {"ws": ws, "install": install, "deploying": deploying, "underlay": underlay}


def _run_collector(monkeypatch, out: Path, deploying: Path, install_prefix: Path, *extra: str) -> dict:
    monkeypatch.setattr("sys.argv", ["collector", str(deploying), str(out), str(install_prefix), *extra])
    collector.main()
    return yaml.safe_load((out / "_package_map.yaml").read_text())


def test_collector_scope_is_the_package_its_workspace_layer_and_src(tmp_path, monkeypatch):
    ws = _scoped_workspace(tmp_path)
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(ws["underlay"]))

    data = _run_collector(monkeypatch, tmp_path / "manifests", ws["deploying"], ws["install"] / "pkg_launcher")

    install = ws["install"]
    assert data["package_map"] == {
        "pkg_launcher": str(install / "pkg_launcher" / "share" / "pkg_launcher"),
        "pkg_vehicle": str(install / "pkg_vehicle" / "share" / "pkg_vehicle"),
        "pkg_src": str(install / "pkg_src" / "share" / "pkg_src"),
    }
    assert data["source_map"] == {
        "pkg_launcher": os.path.realpath(ws["deploying"]),
        "pkg_vehicle": os.path.realpath(ws["ws"] / "launcher" / "vehicle" / "pkg_vehicle"),
        "pkg_src": os.path.realpath(ws["ws"] / "src" / "pkg_src"),
    }


def test_collector_scope_follows_the_given_workspace_root(tmp_path, monkeypatch):
    ws = _scoped_workspace(tmp_path)
    # Without build/install markers the layer is only known through the given root.
    (ws["ws"] / "build").rmdir()

    data = _run_collector(
        monkeypatch,
        tmp_path / "manifests",
        ws["deploying"],
        tmp_path / "elsewhere" / "pkg_launcher",
        "--workspace-root",
        str(ws["ws"]),
    )

    assert set(data["package_map"]) == {"pkg_launcher", "pkg_vehicle", "pkg_src"}


def test_collector_ignores_a_workspace_root_that_does_not_hold_the_package(tmp_path, monkeypatch):
    # colcon run from another directory: the root that CMake derives from the build tree holds no sources.
    sources = tmp_path / "autoware" / "src" / "core"
    deploying = sources / "demo" / "pkg_systems"
    _write_package(deploying, "pkg_systems")
    write_system(deploying / "design", "Tiny", "Demo")
    _write_package(sources / "demo" / "pkg_nodes", "pkg_nodes")
    build_cwd = tmp_path / "home"
    (build_cwd / "build").mkdir(parents=True)

    data = _run_collector(
        monkeypatch,
        tmp_path / "manifests",
        deploying,
        tmp_path / "install" / "pkg_systems",
        "--workspace-root",
        str(build_cwd),
    )

    assert set(data["package_map"]) == {"pkg_systems", "pkg_nodes"}


def test_collector_keeps_a_workspace_root_that_holds_a_symlinked_package(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    target = tmp_path / "external" / "pkg_linked"
    _write_package(target, "pkg_linked")
    write_system(target / "design", "Tiny", "Demo")
    _write_package(ws / "src" / "pkg_nodes", "pkg_nodes")
    (ws / "src" / "pkg_linked").symlink_to(target)

    data = _run_collector(
        monkeypatch,
        tmp_path / "manifests",
        ws / "src" / "pkg_linked",
        tmp_path / "install" / "pkg_linked",
        "--workspace-root",
        str(ws),
    )

    assert set(data["package_map"]) == {"pkg_linked", "pkg_nodes"}


def test_collector_maps_installed_packages_outside_the_scope_on_request(tmp_path, monkeypatch):
    ws = _scoped_workspace(tmp_path)
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(ws["underlay"]))

    data = _run_collector(
        monkeypatch, tmp_path / "manifests", ws["deploying"], ws["install"] / "pkg_launcher", "--include-installed"
    )

    package_map = data["package_map"]
    install = ws["install"]
    assert package_map["pkg_other"] == str(install / "pkg_other" / "share" / "pkg_other")
    assert package_map["pkg_underlay"] == str(ws["underlay"] / "share" / "pkg_underlay")
    # A workspace package keeps its workspace install over an underlay of the same name.
    assert package_map["pkg_src"] == str(install / "pkg_src" / "share" / "pkg_src")
    # Installed packages carry no source directory.
    assert "pkg_other" not in data["source_map"]


def test_collector_source_mode_ignores_installed_packages(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "build").mkdir(parents=True)
    (ws / "install").mkdir(parents=True)
    deploying = ws / "src" / "pkg_launcher"
    _write_package(deploying, "pkg_launcher")
    write_system(deploying / "design", "Tiny", "Demo")
    underlay = tmp_path / "underlay"
    _index_package(underlay, "pkg_underlay")
    monkeypatch.setenv("AMENT_PREFIX_PATH", str(underlay))

    out = tmp_path / "manifests"
    monkeypatch.setattr(
        "sys.argv", ["collector", str(deploying), str(out), str(ws / "install"), "--package-map-mode", "source"]
    )
    collector.main()

    package_map = yaml.safe_load((out / "_package_map.yaml").read_text())["package_map"]
    assert set(package_map) == {"pkg_launcher"}
