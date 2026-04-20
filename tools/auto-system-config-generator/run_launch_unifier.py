#!/usr/bin/env python3
"""
Standalone launch_unifier runner.

Flattens a ROS 2 launch file into output/generated.launch.xml (and a PlantUML
diagram) without requiring a modified launch_ros installation.  Monkey-patches
are applied at import time so the vendored launch_unifier module works against
the unmodified system launch_ros package.

Usage
-----
# By package name (requires ament_index):
python run_launch_unifier.py --package autoware_launch \
    --file-name autoware.launch.xml \
    sensor_model:=aip_xx1 vehicle_model:=sample_vehicle

# By absolute path:
python run_launch_unifier.py \
    --launch-file /path/to/my.launch.xml \
    arg1:=value1
"""

import argparse
import os
import pathlib
import sys

# Allow importing the vendored launch_unifier package from this directory.
sys.path.insert(0, str(pathlib.Path(__file__).parent))

# Apply patches FIRST — before any launch_ros class is instantiated.
from launch_unifier.patches import apply_patches  # noqa: E402

apply_patches()


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Flatten a ROS 2 launch file to XML using vendored launch_unifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--package",
        metavar="PKG",
        help="ROS 2 package that owns the launch file (use with --file-name).",
    )
    source.add_argument(
        "--launch-file",
        metavar="PATH",
        help="Absolute or relative path to the launch file.",
    )
    parser.add_argument(
        "--file-name",
        metavar="FILE",
        help="Launch file name inside the package share (required with --package).",
    )
    parser.add_argument(
        "--output-dir",
        metavar="DIR",
        default="./output",
        help="Directory for generated output (default: ./output).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable launch debug logging.",
    )
    parser.add_argument(
        "launch_arguments",
        nargs="*",
        metavar="key:=value",
        help="Launch arguments forwarded to the launch file.",
    )
    args = parser.parse_args()

    if args.package and not args.file_name:
        parser.error("--file-name is required when --package is used.")

    return args


def main():
    args = _parse_args()

    import launch

    # Resolve launch file path.
    if args.package:
        from ros2launch.api.api import get_share_file_path_from_package

        launch_file_path = get_share_file_path_from_package(package_name=args.package, file_name=args.file_name)
    else:
        launch_file_path = os.path.abspath(args.launch_file)

    if not os.path.isfile(launch_file_path):
        sys.exit(f"Launch file not found: {launch_file_path}")

    # Parse key:=value launch arguments.
    parsed_launch_arguments = []
    for arg in args.launch_arguments or []:
        if ":=" in arg:
            key, value = arg.split(":=", 1)
            parsed_launch_arguments.append((key, value))
        else:
            sys.exit(f"Launch argument must be in key:=value form, got: {arg!r}")

    root_entity = launch.actions.IncludeLaunchDescription(
        launch.launch_description_sources.AnyLaunchDescriptionSource(launch_file_path),
        launch_arguments=parsed_launch_arguments,
    )

    launch_service = launch.LaunchService(
        argv=[f"{k}:={v}" for k, v in parsed_launch_arguments],
        noninteractive=True,
        debug=args.debug,
    )

    from launch_unifier.filter import filter_entity_tree
    from launch_unifier.launch_maker import generate_launch_file
    from launch_unifier.parser import create_entity_tree
    from launch_unifier.plantuml import generate_plantuml
    from launch_unifier.serialization import make_entity_tree_serializable

    output_dir = pathlib.Path(args.output_dir)
    output_dir.mkdir(exist_ok=True, parents=True)

    raw_tree = create_entity_tree(root_entity, launch_service)
    filtered_tree = filter_entity_tree(raw_tree.copy())
    serializable_tree = make_entity_tree_serializable(filtered_tree, launch_service.context)

    generated_xml = generate_launch_file(serializable_tree)
    plantuml_text = generate_plantuml(serializable_tree)

    (output_dir / "generated.launch.xml").write_text(generated_xml)
    (output_dir / "entity_tree.pu").write_text(plantuml_text)

    print(f"Output written to {output_dir.resolve()}/")


if __name__ == "__main__":
    main()
