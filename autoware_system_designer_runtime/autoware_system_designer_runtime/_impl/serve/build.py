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

"""Build targets of an exported system, as colcon invocations over the served workspace.

Targets:
  ``deployment``  the package holding the export (regenerates launchers and visualization)
  ``workspace``   packages up to one compute unit of one mode, taken from the generated
                  ``build_scripts/<mode>/build_<unit>.sh``

The generated script is parsed, never executed: only its ``colcon build`` arguments are used, with
package names checked. Every build of a workspace shares one job key, since colcon builds of one
workspace cannot overlap.
"""

from __future__ import annotations

import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import api
from .exports import BUILD_SCRIPTS_SUBDIR, SystemExport

TARGET_DEPLOYMENT = "deployment"
TARGET_WORKSPACE = "workspace"
TARGETS = (TARGET_DEPLOYMENT, TARGET_WORKSPACE)

BUILD_JOB_KIND = "build"
INSTALL_LAYOUT_FILE = ".colcon_install_layout"
# Flags of the generated build scripts, also used for the deployment target.
DEFAULT_BUILD_ARGS = ("--symlink-install", "--cmake-args", "-DCMAKE_BUILD_TYPE=Release")
_PACKAGE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")
_STARTED = re.compile(r"^Starting >>> (\S+)")


def resolve_workspace(install_root: Path, explicit: Optional[str]) -> Optional[Path]:
    """Workspace root builds run in: the explicit one, else the install tree's parent when it holds ``src``."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    parent = install_root.parent
    return parent if (parent / "src").is_dir() else None


@dataclass(frozen=True)
class BuildPlan:
    target: str
    packages: tuple[str, ...]
    argv: tuple[str, ...]
    title: str
    params: dict


def _install_args(install_root: Path) -> list[str]:
    args = ["--install-base", str(install_root)]
    try:
        layout = (install_root / INSTALL_LAYOUT_FILE).read_text().strip()
    except OSError:
        layout = ""
    if layout == "merged":
        args.append("--merge-install")
    return args


def _check_package(name: str) -> str:
    if not _PACKAGE_NAME.match(name):
        raise api.ApiError(api.UNAVAILABLE, f"build script names an invalid package {name!r}")
    return name


def script_build_args(script: Path) -> tuple[list[str], list[str]]:
    """(colcon build options except the package selection, packages up to) of a generated build script."""
    try:
        text = script.read_text()
    except OSError as exc:
        raise api.ApiError(api.NOT_FOUND, f"no build script at {script}: {exc}") from exc
    joined = text.replace("\\\n", " ")
    command = next((line for line in joined.splitlines() if line.strip().startswith("colcon build")), None)
    if command is None:
        raise api.ApiError(api.UNAVAILABLE, f"{script} has no colcon build command")
    try:
        tokens = shlex.split(command)[2:]
    except ValueError as exc:
        raise api.ApiError(api.UNAVAILABLE, f"cannot parse {script}: {exc}") from exc
    if "--packages-up-to" not in tokens:
        raise api.ApiError(api.UNAVAILABLE, f"{script} selects no packages")
    idx = tokens.index("--packages-up-to")
    options, rest = tokens[:idx], tokens[idx + 1 :]
    end = next((i for i, token in enumerate(rest) if token.startswith("-")), len(rest))
    packages, options = [_check_package(p) for p in rest[:end]], options + rest[end:]
    if not packages:
        raise api.ApiError(api.UNAVAILABLE, f"{script} selects no packages")
    return options, packages


def plan_build(
    system: SystemExport,
    install_root: Path,
    target: str,
    *,
    mode: Optional[str] = None,
    compute_unit: Optional[str] = None,
) -> BuildPlan:
    colcon = shutil.which("colcon")
    if colcon is None:
        raise api.ApiError(api.UNAVAILABLE, "colcon is not on PATH")
    install_args = _install_args(install_root)
    if target == TARGET_DEPLOYMENT:
        package = _check_package(system.package)
        argv = [colcon, "build", *install_args, "--packages-select", package, *DEFAULT_BUILD_ARGS]
        return BuildPlan(
            target,
            (package,),
            tuple(argv),
            f"build {package}",
            {"system": system.id, "target": target},
        )
    if target != TARGET_WORKSPACE:
        raise api.ApiError(api.INVALID_PARAMS, f"target must be one of {', '.join(TARGETS)}")
    mode = mode or system.manifest.get("default_mode")
    if not isinstance(mode, str) or mode not in system.mode_keys():
        raise api.ApiError(api.INVALID_PARAMS, f"unknown mode {mode!r} of {system.id}")
    if not isinstance(compute_unit, str) or not compute_unit:
        raise api.ApiError(api.INVALID_PARAMS, "compute_unit is required for a workspace build")
    if compute_unit not in {unit["name"] for unit in system.compute_units()}:
        raise api.ApiError(api.INVALID_PARAMS, f"unknown compute unit {compute_unit!r} of {system.id}")
    script = system.export_dir / BUILD_SCRIPTS_SUBDIR / mode / f"build_{compute_unit}.sh"
    options, packages = script_build_args(script)
    argv = [colcon, "build", *install_args, "--packages-up-to", *packages, *options]
    return BuildPlan(
        target,
        tuple(packages),
        tuple(argv),
        f"build {system.name} {mode}/{compute_unit}",
        {"system": system.id, "target": target, "mode": mode, "compute_unit": compute_unit},
    )


def started_package(line: str) -> Optional[str]:
    """Package named by a colcon ``Starting >>>`` line."""
    match = _STARTED.match(line)
    return match.group(1) if match else None
