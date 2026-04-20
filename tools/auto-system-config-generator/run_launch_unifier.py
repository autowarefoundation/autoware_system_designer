#!/usr/bin/env python3
"""Standalone launch_unifier runner.

Flattens a ROS 2 launch file into output/generated.launch.xml (and a PlantUML
diagram) without requiring a modified launch_ros installation.

For the full pipeline (flatten + generate system config in one step) use
generate_system_config.py with --launch-package / --launch-path instead.

Usage
-----
# By package name (requires ament_index):
python run_launch_unifier.py --launch-package autoware_launch \\
    --launch-file autoware.launch.xml \\
    sensor_model:=aip_xx1 vehicle_model:=sample_vehicle

# By absolute path:
python run_launch_unifier.py \\
    --launch-path /path/to/my.launch.xml \\
    arg1:=value1
"""

import argparse
import pathlib
import sys

# Patches must be applied before any launch entity class is instantiated.
# They live in lib/unifier.py which handles the guard automatically.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from lib.unifier import resolve_launch_path, unify_launch  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Flatten a ROS 2 launch file to XML using vendored launch_unifier.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--launch-package",
        metavar="PKG",
        help="ROS 2 package that owns the launch file (use with --launch-file).",
    )
    source.add_argument(
        "--launch-path",
        metavar="PATH",
        help="Absolute or relative path to the launch file.",
    )
    parser.add_argument(
        "--launch-file",
        metavar="FILE",
        help="Launch file name inside the package share (required with --launch-package).",
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

    if args.launch_package and not args.launch_file:
        parser.error("--launch-file is required when --launch-package is used.")

    return args


def main() -> None:
    args = _parse_args()

    parsed_args: list[tuple[str, str]] = []
    for arg in args.launch_arguments or []:
        if ":=" in arg:
            key, value = arg.split(":=", 1)
            parsed_args.append((key, value))
        else:
            sys.exit(f"Launch argument must be key:=value, got: {arg!r}")

    launch_file = resolve_launch_path(
        package=args.launch_package,
        file_name=args.launch_file,
        launch_path=args.launch_path,
    )

    xml_path = unify_launch(
        launch_file=launch_file,
        launch_arguments=parsed_args,
        output_dir=pathlib.Path(args.output_dir),
        debug=args.debug,
    )

    print(f"Output written to {xml_path.parent.resolve()}/")


if __name__ == "__main__":
    main()
