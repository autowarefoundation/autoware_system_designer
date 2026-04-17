#!/usr/bin/env python3
"""Generate Autoware system designer YAML configs from a ROS launch XML.

Typical usage
-------------
  # 1. Run launch_unifier on a launch command:
  #    ros2 run launch_unifier launch_unifier --ros-args \\
  #      -p launch_command:="ros2 launch autoware_launch logging_simulator.launch.xml \\
  #         vehicle_model:=sample_vehicle sensor_model:=awsim_sensor_kit map_path:=..."
  #    → produces output/generated.launch.xml

  # 2. Run this tool on the flattened XML:
  python generate_system_config.py \\
    --launch-xml output/generated.launch.xml \\
    --system-name LoggingSimulator \\
    --output-dir generated/

Requirements: lxml, PyYAML  (pip install lxml pyyaml)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not found. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import lxml  # noqa: F401
except ImportError:
    print("ERROR: lxml not found. Run: pip install lxml", file=sys.stderr)
    sys.exit(1)

from lib.connection_resolver import resolve_connections
from lib.emitter import emit_module_yaml, emit_system_yaml
from lib.grouper import group_nodes
from lib.launch_parser import parse_launch_xml


def load_component_map(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse a ROS launch XML and generate Autoware system designer YAML configs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--launch-xml",
        required=True,
        metavar="FILE",
        help="Path to generated.launch.xml produced by launch_unifier",
    )
    parser.add_argument(
        "--output-dir",
        default="generated",
        metavar="DIR",
        help="Output directory for generated YAML files (default: ./generated)",
    )
    parser.add_argument(
        "--system-name",
        default="GeneratedSystem",
        metavar="NAME",
        help="System name (used as filename prefix and system YAML name)",
    )
    parser.add_argument(
        "--compute-unit",
        default="main_ecu",
        metavar="NAME",
        help="Compute unit label assigned to all components (default: main_ecu)",
    )
    parser.add_argument(
        "--group-depth",
        type=int,
        default=1,
        metavar="N",
        help="Namespace depth for component grouping (default: 1 = top-level)",
    )
    parser.add_argument(
        "--component-map",
        default=None,
        metavar="FILE",
        help="YAML file mapping namespaces to component name/entity overrides "
             "(default: config/component_map.yaml next to this script)",
    )
    parser.add_argument(
        "--no-modules",
        action="store_true",
        help="Skip generating per-component module YAML files",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress information",
    )
    args = parser.parse_args(argv)

    launch_xml = Path(args.launch_xml)
    if not launch_xml.exists():
        print(f"ERROR: launch XML not found: {launch_xml}", file=sys.stderr)
        return 1

    # Locate component map
    if args.component_map:
        map_path = Path(args.component_map)
    else:
        map_path = Path(__file__).parent / "config" / "component_map.yaml"

    component_map = load_component_map(map_path)

    # Parse
    if args.verbose:
        print(f"Parsing {launch_xml} ...")
    nodes, containers = parse_launch_xml(launch_xml)
    if args.verbose:
        print(f"  Found {len(nodes)} nodes, {len(containers)} containers")

    # Group
    groups = group_nodes(nodes, containers, depth=args.group_depth, overrides=component_map)
    if args.verbose:
        for g in groups:
            print(f"  Group '{g.name}' ({g.namespace}): {len(g.nodes)} nodes, {len(g.containers)} containers")

    # Connections
    connections = resolve_connections(groups)
    if args.verbose:
        print(f"  Resolved {len(connections)} cross-component connections")

    # Output
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # system.yaml
    system_yaml = emit_system_yaml(
        system_name=args.system_name,
        groups=groups,
        all_containers=containers,
        connections=connections,
        compute_unit=args.compute_unit,
    )
    system_file = out / f"{args.system_name}.system.yaml"
    system_file.write_text(system_yaml)
    print(f"Written: {system_file}")

    # module.yaml files
    if not args.no_modules:
        for group in groups:
            if not group.nodes:
                continue
            module_yaml = emit_module_yaml(group, groups)
            # (root) group gets a safe filename
            safe_entity = group.entity_name if group.namespace != "(root)" else "RosSystem"
            module_file = out / f"{safe_entity}.module.yaml"
            module_file.write_text(module_yaml)
            if args.verbose:
                print(f"Written: {module_file}")
        module_count = sum(1 for g in groups if g.nodes)
        print(f"Written: {module_count} module YAML file(s) in {out}/")

    return 0


if __name__ == "__main__":
    sys.exit(main())
