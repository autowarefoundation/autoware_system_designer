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

import os

from setuptools import find_packages, setup

package_name = "autoware_system_designer_studio"


def _package_files(*subdirs):
    """Files under package subdirectories, relative to the package; package_data globs do not recurse."""
    files = []
    for subdir in subdirs:
        for root, _, names in os.walk(os.path.join(package_name, subdir)):
            files += [os.path.relpath(os.path.join(root, name), package_name) for name in names]
    return files


setup(
    name=package_name,
    version="0.5.0",
    packages=find_packages(exclude=["test"]),
    # Web bundle and page templates read from the package directory.
    package_data={package_name: _package_files("web", os.path.join("pages", "templates"))},
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        # PATH entry point beside the ros2 run console script under lib/<package>.
        ("bin", ["scripts/autoware-system-designer-studio"]),
    ],
    install_requires=["setuptools", "jinja2"],
    tests_require=["pytest"],
    zip_safe=False,
    maintainer="Taekjin Lee",
    maintainer_email="taekjin.lee@tier4.jp",
    description="Studio for exported systems: visualization, build and launch control served from the export artifacts",
    license="Apache License 2.0",
    entry_points={
        "console_scripts": [
            "autoware-system-designer-studio = autoware_system_designer_studio.cli:main",
        ],
    },
)
