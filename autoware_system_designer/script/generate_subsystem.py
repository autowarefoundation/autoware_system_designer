#!/usr/bin/env python3
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

"""Generate a subsystem launch file and its parameter files outside a package build.

Collects the workspace design manifests, builds the subsystem, and places
launch/<name>.launch.xml and config/<name>/ under the output root, by
default the source directory of the package owning the subsystem file. The system structure
export stays in a temporary directory unless --exports-dir is given.

Design and parameter files resolve against the workspace install when one exists (built
parameter files are read from there), otherwise against the source packages.

Usage:
    generate_subsystem.py <subsystem.yaml> [--output-root DIR] [--package NAME]
        [--workspace-root DIR] [--source-only] [--exports-dir DIR]
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import deployment_process  # noqa: E402

from autoware_system_designer.common.exceptions import SystemDesignerError  # noqa: E402
from autoware_system_designer.parser.subsystem_parser import subsystem_launch_file, parse_subsystem  # noqa: E402


def _owning_package(path: Path) -> Tuple[Optional[str], Optional[Path]]:
    """Name and directory of the nearest package.xml above path."""
    for directory in path.resolve().parents:
        package_xml = directory / "package.xml"
        if package_xml.is_file():
            name = ET.parse(package_xml).getroot().findtext("name")
            return (name.strip() if name else None), directory
    return None, None


def _workspace_root(package_dir: Path) -> Optional[Path]:
    """Directory holding the nearest 'src' above the package; one with build outputs beside it comes first."""
    roots = [d.parent for d in package_dir.resolve().parents if d.name == "src"]
    built = [r for r in roots if any((r / output).is_dir() for output in ("install", "build"))]
    return (built or roots or [None])[0]


def _install_args(workspace_root: Path, package: str, source_only: bool) -> list:
    """Install prefix and package map mode for the manifest collector."""
    install = workspace_root / "install"
    layout_file = install / ".colcon_install_layout"
    layout = layout_file.read_text().strip() if layout_file.is_file() else None
    if not source_only and install.is_dir():
        if layout == "isolated" or (layout is None and (install / package).is_dir()):
            return [str(install / package)]
        return [str(install)]
    return [str(install / package), "--package-map-mode", "source"]


def _collect_manifests(package_dir: Path, manifest_dir: Path, workspace_root: Path, package: str, source_only: bool):
    command = [
        sys.executable,
        str(_SCRIPT_DIR / "collect_system_design_manifests.py"),
        str(package_dir),
        str(manifest_dir),
        *_install_args(workspace_root, package, source_only),
        "--anchor",
        str(workspace_root),
        "--workspace-root",
        str(workspace_root),
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        raise SystemExit(f"generate_subsystem.py: manifest collection failed ({result.returncode})")


def _place(build_root: Path, output_root: Path, relative: str) -> Path:
    """Replace output_root/relative with the built one."""
    source = build_root / relative
    target = output_root / relative
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()
    if not source.exists():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a subsystem launch file and its parameter files.")
    parser.add_argument("subsystem_file", help="<name>.subsystem.yaml")
    parser.add_argument("--output-root", default=None, help="Default: source directory of the owning package")
    parser.add_argument("--package", default=None, help="Package installing the output; default: owning package")
    parser.add_argument(
        "--workspace-root", default=None, help="Default: the directory holding the nearest 'src' above the package"
    )
    parser.add_argument("--source-only", action="store_true", help="Resolve packages from sources only")
    parser.add_argument("--exports-dir", default=None, help="Keep the build output (system structure) here")
    args = parser.parse_args()

    subsystem_file = Path(args.subsystem_file).resolve()
    subsystem = parse_subsystem(str(subsystem_file))
    owner, package_dir = _owning_package(subsystem_file)
    package = args.package or owner
    if not owner or not package or package_dir is None:
        raise SystemExit(f"generate_subsystem.py: no package.xml above {subsystem_file}")
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else _workspace_root(package_dir)
    if workspace_root is None:
        raise SystemExit("generate_subsystem.py: the workspace root is unknown; pass --workspace-root")
    output_root = Path(args.output_root).resolve() if args.output_root else package_dir

    os.environ.setdefault("AUTOWARE_SYSTEM_DESIGNER_STRICT", "1")
    with tempfile.TemporaryDirectory(prefix="subsystem_") as tmp:
        manifest_dir = Path(tmp) / "manifests"
        build_root = Path(args.exports_dir).resolve() if args.exports_dir else Path(tmp) / "out"
        _collect_manifests(package_dir, manifest_dir, workspace_root, owner, args.source_only)
        workspace_yaml = package_dir / "workspace.yaml"
        workspace_yaml = str(workspace_yaml) if workspace_yaml.is_file() else None
        try:
            deployment_process.build(str(subsystem_file), str(manifest_dir), str(build_root), workspace_yaml, package)
        except SystemDesignerError:
            return 1
        launch_file = _place(build_root, output_root, subsystem_launch_file(subsystem.name))
        param_dir = _place(build_root, output_root, f"config/{subsystem.name}")
    print(f"{launch_file}\n{param_dir}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
