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

"""Mini LaunchService runner for ros2_launch_file wrapper components.

Each wrapper unit runs its own LaunchService so the actor supervisor can manage
it as an independent process while still using the ROS 2 Python launch library
rather than the ``ros2 launch`` CLI.

The key advantage over ``ros2 launch``: global parameters (vehicle_info etc.)
can be injected as ``SetParameter`` actions *before* the include, so all nodes
launched by the included file receive them — even though each wrapper runs in a
separate process from the global_parameter_loader.

All imports from ``launch`` / ``launch_ros`` are deferred to function bodies to
avoid build-time import errors in the colcon workspace.

Invoked by builder.include_cmdline() as:

    python3 -m autoware_system_designer.runtime.ros2_launch_runner \\
        --pkg <package> --file <launch_file.py> \\
        [--launch-arg key:=value ...] \\
        [--global-params-file /path/to/vehicle_info.param.yaml ...]
"""

from __future__ import annotations

import argparse
import sys


def _parse_kv(s: str) -> tuple[str, str]:
    if ":=" not in s:
        raise argparse.ArgumentTypeError(f"Expected KEY:=VALUE, got {s!r}")
    k, v = s.split(":=", 1)
    return k, v


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a single ROS 2 launch file via LaunchService with optional global params.",
    )
    parser.add_argument("--pkg", required=True, help="ROS 2 package containing the launch file")
    parser.add_argument(
        "--file",
        required=True,
        help="Launch file name relative to share/<pkg>/launch/",
    )
    parser.add_argument(
        "--launch-arg",
        action="append",
        default=[],
        metavar="KEY:=VALUE",
        type=_parse_kv,
        help="Launch argument passed to the included file (repeatable)",
    )
    parser.add_argument(
        "--global-params-file",
        action="append",
        default=[],
        metavar="PATH",
        help="YAML parameter file injected as SetParameter actions before the include (repeatable)",
    )
    args = parser.parse_args()

    sys.exit(
        _run(
            package=args.pkg,
            launch_file=args.file,
            launch_args=dict(args.launch_arg),
            global_params_files=args.global_params_file,
        )
    )


def _run(
    package: str,
    launch_file: str,
    launch_args: dict[str, str],
    global_params_files: list[str],
) -> int:
    import signal
    from pathlib import Path

    from ament_index_python.packages import get_package_share_directory
    from launch import LaunchDescription, LaunchService
    from launch.actions import IncludeLaunchDescription
    from launch.launch_description_sources import AnyLaunchDescriptionSource

    pkg_share = get_package_share_directory(package)
    full_path = str(Path(pkg_share) / "launch" / launch_file)

    actions = []
    for params_file in global_params_files:
        actions.extend(_set_params_from_yaml(params_file))

    # Undeclared args become SetParameter; declared args are launch-config variables
    # that must NOT be SetParameter (type conflict can crash nodes, e.g. initial_pose:=[]).
    declared = _declared_args(full_path)
    actions.extend(_undeclared_args_as_set_params(launch_args, declared))

    # Launch args are always strings in the ROS 2 launch system.
    str_args = {k: str(v) for k, v in launch_args.items()}
    actions.append(
        IncludeLaunchDescription(
            AnyLaunchDescriptionSource(full_path),
            launch_arguments=str_args.items(),
        )
    )

    ls = LaunchService()
    ls.include_launch_description(LaunchDescription(actions))

    # SIGTERM triggers graceful shutdown so LaunchService waits for all children.
    signal.signal(signal.SIGTERM, lambda sig, frame: ls.shutdown())

    return ls.run()


def _declared_args(launch_file_path: str) -> set:
    """Return arg names declared at the top level of an XML launch file; empty for non-XML."""
    if not launch_file_path.endswith(".xml"):
        return set()
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(launch_file_path).getroot()
        return {el.get("name") for el in root.findall("arg") if el.get("name")}
    except Exception as exc:
        print(f"launch_runner: warning: cannot parse {launch_file_path}: {exc}", file=sys.stderr)
        return set()


def _undeclared_args_as_set_params(launch_args: dict[str, str], declared: set) -> list:
    """Return SetParameter actions for args not declared by the launch file."""
    from launch_ros.actions import SetParameter

    return [SetParameter(name=key, value=_coerce(raw)) for key, raw in launch_args.items() if key not in declared]


def _coerce(raw: str) -> object:
    """Coerce a CLI string value to the natural Python type for SetParameter."""
    low = raw.strip().lower()
    if low in ("true", "1", "yes"):
        return True
    if low in ("false", "0", "no"):
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _set_params_from_yaml(yaml_path: str) -> list:
    """Return SetParameter actions for all params under ros__parameters in a YAML file."""
    import yaml
    from launch_ros.actions import SetParameter

    try:
        with open(yaml_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        print(f"launch_runner: warning: cannot read {yaml_path}: {exc}", file=sys.stderr)
        return []

    params: dict = {}
    for ns_val in data.values():
        if isinstance(ns_val, dict):
            ros_params = ns_val.get("ros__parameters", {})
            if isinstance(ros_params, dict):
                params.update(ros_params)

    return [SetParameter(name=str(k), value=v) for k, v in params.items()]


if __name__ == "__main__":
    main()
