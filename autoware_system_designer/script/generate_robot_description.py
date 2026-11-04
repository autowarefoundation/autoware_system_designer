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

"""
Expand a xacro file into a robot_state_publisher parameter file.

The URDF is written as the robot_description parameter of '/**'.
Requires a sourced workspace so that xacro resolves the description packages.

Usage:
    generate_robot_description.py <xacro_file> <output_file> --package-dir <dir> [name:=value ...]
"""

import argparse
import sys
from pathlib import Path

import xacro
from generate_param_files import describe_source, write_param_file


def expand_urdf(xacro_file: Path, mappings: dict) -> str:
    # xacro adds the defaults of undeclared arguments to the given mappings.
    doc = xacro.process_file(str(xacro_file), mappings=dict(mappings))
    # The xacro banner carries the host-specific source path.
    for node in list(doc.childNodes):
        if node.nodeType == node.COMMENT_NODE:
            doc.removeChild(node)
    urdf = doc.toprettyxml(indent="  ")
    # Literal block scalars cannot carry trailing whitespace.
    return "\n".join(line.rstrip() for line in urdf.splitlines() if line.strip()) + "\n"


def parse_mappings(items):
    mappings = {}
    for item in items:
        name, sep, value = item.partition(":=")
        if not sep or not name:
            raise ValueError(f"xacro argument must be name:=value, got '{item}'")
        mappings[name] = value
    return mappings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("xacro_file", type=Path, help="xacro file to expand")
    parser.add_argument("output_file", type=Path, help="parameter file to write")
    parser.add_argument("--package-dir", type=Path, required=True, help="root of the header paths")
    parser.add_argument("mappings", nargs="*", help="xacro arguments as name:=value")
    args = parser.parse_intermixed_args(argv)

    package_dir = args.package_dir.resolve()
    mappings = parse_mappings(args.mappings)
    urdf = expand_urdf(args.xacro_file, mappings)
    source = describe_source([str(args.xacro_file)] + args.mappings, package_dir)
    write_param_file(args.output_file, {"robot_description": urdf}, source)
    return 0


if __name__ == "__main__":
    sys.exit(main())
