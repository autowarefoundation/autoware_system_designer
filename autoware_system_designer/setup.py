#!/usr/bin/env python3
# Copyright 2025 TIER IV, inc.
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

"""Setup script for autoware_system_designer package."""

from setuptools import setup, find_packages
from pathlib import Path

# Read the long description from README
readme_path = Path(__file__).parent / "autoware_system_designer" / "README.md"
long_description = ""
if readme_path.exists():
    with open(readme_path, "r", encoding="utf-8") as f:
        long_description = f.read()

setup(
    name="autoware_system_designer",
    version="1.0.0",
    description="Auotware System Designerure Package for building and deploying Autoware systems.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Taekjin Lee",
    author_email="taekjin.lee@tier4.jp",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=[
        "PyYAML>=5.4.0",
        "Jinja2>=3.0.0",
        "pathlib",
    ],
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-cov",
            "black",
            "flake8",
        ],
    },
    entry_points={
        "console_scripts": [
            "autoware-architect-build=autoware_system_designer.cli.build:main",
            "autoware-architect-generate=autoware_system_designer.cli.generate:main",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Apache Software License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Distributed Computing",
    ],
    include_package_data=True,
    zip_safe=False,
)
