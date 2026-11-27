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

import json
import os

from colcon_core.package_selection import PackageSelectionExtensionPoint, logger
from colcon_core.plugin_system import satisfies_version

# Written to the build base, read by the system designer manifest collector of each deploying package.
PACKAGE_MAP_FILENAME = ".autoware_system_designer_packages.json"
PACKAGE_MAP_FORMAT_VERSION = 1


def package_map(decorators):
    """Source directory of every discovered package, selected or not; the first of a duplicate name wins."""
    packages = {}
    for decorator in decorators:
        descriptor = decorator.descriptor
        if descriptor.name and descriptor.path:
            packages.setdefault(descriptor.name, os.path.abspath(str(descriptor.path)))
    return packages


def write_package_map(build_base, packages):
    """Write the map to the build base; the file is replaced in one step so readers never see it partly written."""
    os.makedirs(build_base, exist_ok=True)
    path = os.path.join(build_base, PACKAGE_MAP_FILENAME)
    tmp_path = f"{path}.{os.getpid()}.tmp"
    with open(tmp_path, "w") as stream:
        json.dump(
            {"format_version": PACKAGE_MAP_FORMAT_VERSION, "packages": packages}, stream, indent=2, sort_keys=True
        )
        stream.write("\n")
    os.replace(tmp_path, path)
    return path


class PackageMapPackageSelection(PackageSelectionExtensionPoint):
    """Records the packages colcon discovered for the build; the selection itself is left unchanged."""

    # Observes the final selection state of the other extensions.
    PRIORITY = 10

    TARGET_VERBS = ("build",)

    def __init__(self):  # noqa: D107
        super().__init__()
        satisfies_version(PackageSelectionExtensionPoint.EXTENSION_POINT_VERSION, "^1.0")

    def select_packages(self, *, args, decorators):  # noqa: D102
        if getattr(args, "verb_name", None) not in self.TARGET_VERBS:
            return
        build_base = getattr(args, "build_base", None)
        if not build_base:
            return
        try:
            path = write_package_map(build_base, package_map(decorators))
        except OSError as exc:
            logger.warning(f"autoware_system_designer: failed to write the package map to {build_base}: {exc}")
            return
        logger.debug(f"autoware_system_designer: wrote the package map {path}")
