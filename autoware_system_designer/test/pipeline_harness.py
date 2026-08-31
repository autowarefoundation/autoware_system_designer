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

"""In-process pipeline harness for fixture design workspaces.

A fixture case under ``test/fixtures/designs/<case>/`` is a tree of design
packages (one directory per package). The harness fabricates the manifest
directory the CMake collector would produce, runs the full deployment
pipeline, and exposes the export tree for golden comparison.
"""

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

import yaml

TEST_DIR = Path(__file__).resolve().parent
REPO_DIR = TEST_DIR.parent
FIXTURES_DIR = TEST_DIR / "fixtures" / "designs"
GOLDEN_DIR = TEST_DIR / "golden"

_DESIGN_TYPE_BY_SUFFIX = {
    ".node.yaml": "node",
    ".module.yaml": "module",
    ".system.yaml": "system",
    ".parameter_set.yaml": "parameter_set",
}


def design_type(filename: str) -> Optional[str]:
    for suffix, design in _DESIGN_TYPE_BY_SUFFIX.items():
        if filename.endswith(suffix):
            return design
    return None


def _load_deployment_process():
    """Import script/deployment_process.py by path; the script dir is not a package."""
    script_path = REPO_DIR / "script" / "deployment_process.py"
    spec = importlib.util.spec_from_file_location("deployment_process_under_test", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PipelineRun:
    """Handle to one completed pipeline run's export tree."""

    def __init__(self, workspace: Path, out_root: Path, system_name: str):
        self.workspace = workspace
        self.out_root = out_root
        self.system_name = system_name

    @property
    def exports_dir(self) -> Path:
        return self.out_root / "exports" / self.system_name

    def structure(self, mode: str) -> dict:
        path = self.exports_dir / "system_structure" / f"{mode}.json"
        return json.loads(path.read_text())

    def launcher_dir(self, mode: str) -> Path:
        return self.exports_dir / "launcher" / mode

    def parameter_set_dir(self, mode: str) -> Path:
        return self.exports_dir / "parameter_set" / mode

    def replacements(self) -> Dict[str, str]:
        """Path prefixes to strip from outputs before golden comparison."""
        return {
            str(self.out_root): "<OUT>",
            str(self.workspace): "<WS>",
        }


def stage_case(case_name: str, tmp_path: Path) -> Path:
    """Copy a fixture case into a temp workspace so runs never touch the repo tree."""
    src = FIXTURES_DIR / case_name
    workspace = tmp_path / "ws"
    shutil.copytree(src, workspace)
    return workspace


def write_manifests(workspace: Path, manifest_dir: Path) -> None:
    """Fabricate the manifest tree the CMake collector produces.

    Every immediate subdirectory of *workspace* is one design package.
    Paths are written absolute; the package map carries no workspace_root.
    """
    manifest_dir.mkdir(parents=True, exist_ok=True)
    package_map: Dict[str, str] = {}
    for pkg_dir in sorted(p for p in workspace.iterdir() if p.is_dir()):
        package_map[pkg_dir.name] = str(pkg_dir)
        entries = []
        for design_file in sorted(pkg_dir.rglob("*.yaml")):
            dtype = design_type(design_file.name)
            if dtype is None:
                continue
            entries.append({"path": str(design_file), "type": dtype})
        manifest = {"package_name": pkg_dir.name, "deploy_config_files": entries}
        (manifest_dir / f"{pkg_dir.name}.yaml").write_text(yaml.safe_dump(manifest))
    (manifest_dir / "_package_map.yaml").write_text(yaml.safe_dump({"package_map": package_map}))


def run_pipeline(
    workspace: Path,
    target: str,
    tmp_path: Path,
    strict: bool = False,
    workspace_yaml: Optional[str] = None,
) -> PipelineRun:
    """Run the full pipeline (build, export, generate, visualize) in-process.

    *target* is a path relative to *workspace* (deployment/system/deployments file)
    or an absolute path / bare entity name passed through unchanged.
    """
    from autoware_system_designer.parser.yaml_parser import yaml_parser

    manifest_dir = tmp_path / "manifests"
    out_root = tmp_path / "out"
    write_manifests(workspace, manifest_dir)

    target_path = workspace / target
    target_arg = str(target_path) if target_path.exists() else target

    # The parser singleton caches by path; keep runs independent within one process.
    yaml_parser.clear_cache()

    deployment_process = _load_deployment_process()
    saved_strict = os.environ.get("AUTOWARE_SYSTEM_DESIGNER_STRICT")
    os.environ["AUTOWARE_SYSTEM_DESIGNER_STRICT"] = "1" if strict else "0"
    try:
        deployment_process.build(target_arg, str(manifest_dir), str(out_root), workspace_yaml)
    finally:
        if saved_strict is None:
            os.environ.pop("AUTOWARE_SYSTEM_DESIGNER_STRICT", None)
        else:
            os.environ["AUTOWARE_SYSTEM_DESIGNER_STRICT"] = saved_strict

    exports = out_root / "exports"
    system_names = sorted(p.name for p in exports.iterdir()) if exports.is_dir() else []
    assert len(system_names) == 1, f"expected one exported system, got {system_names}"
    return PipelineRun(workspace, out_root, system_names[0])


def normalize(value, replacements: Dict[str, str]):
    """Make a payload run-independent: drop timestamps, tokenize path prefixes."""
    if isinstance(value, dict):
        return {k: normalize(v, replacements) for k, v in value.items() if k != "generated_at"}
    if isinstance(value, list):
        return [normalize(v, replacements) for v in value]
    if isinstance(value, str):
        for prefix, token in replacements.items():
            value = value.replace(prefix, token)
        return value
    return value


def normalize_text(text: str, replacements: Dict[str, str]) -> str:
    for prefix, token in replacements.items():
        text = text.replace(prefix, token)
    return text


def dump_canonical(payload) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
