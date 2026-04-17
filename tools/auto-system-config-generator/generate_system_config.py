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
from lib.emitter import (
    _collect_all_pub_sub,
    emit_module_yaml,
    emit_module_yaml_from_tree,
    emit_parameter_set_yaml,
    emit_system_yaml,
    emit_system_yaml_from_tree,
)
from lib.graph_parser import merge_graph_topics, parse_graph_json
from lib.grouper import group_nodes
from lib.launch_parser import parse_launch_xml
from lib.namespace_tree import NamespaceNode, build_namespace_tree
from lib.node_emitter import (
    collect_nodes_by_entity,
    collect_nodes_by_entity_flat,
    emit_node_yaml,
    find_defined_node_entities,
    find_package_map,
    load_package_map,
)


def load_component_map(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    return data


def _emit_recursive_modules(
    ns_node: NamespaceNode,
    all_pub: dict,
    all_sub: dict,
    out_dir: Path,
    verbose: bool,
) -> int:
    """Recursively emit module YAMLs for ns_node and all descendants. Returns count."""
    count = 0
    if not ns_node.all_nodes:
        return 0

    module_yaml = emit_module_yaml_from_tree(ns_node, all_pub, all_sub)
    rel = ns_node.namespace.strip("/")
    module_file = out_dir / "module" / rel / f"{ns_node.entity_name}.module.yaml"
    module_file.parent.mkdir(parents=True, exist_ok=True)
    module_file.write_text(module_yaml)
    count += 1
    if verbose:
        print(f"  Written: {module_file}")

    for child in ns_node.children.values():
        count += _emit_recursive_modules(child, all_pub, all_sub, out_dir, verbose)

    return count


def _emit_node_configs(
    nodes_by_entity: dict,
    graph,
    out_dir: Path,
    package_map_path,
    verbose: bool,
) -> None:
    """Generate *.node.yaml files for entities not already defined."""
    package_map = load_package_map(package_map_path) if package_map_path else {}
    entity_names = set(nodes_by_entity.keys())
    defined = find_defined_node_entities(entity_names, package_map)

    undefined = entity_names - defined
    if verbose and defined:
        print(f"  Skipping {len(defined)} already-defined node entity/entities")

    if not undefined:
        print("  All node entities already defined — no *.node.yaml files written")
        return

    node_dir = out_dir / "node"
    node_dir.mkdir(parents=True, exist_ok=True)
    for entity_name in sorted(undefined):
        node_records = nodes_by_entity[entity_name]
        node_yaml = emit_node_yaml(entity_name, node_records, graph)
        node_file = node_dir / f"{entity_name}.yaml"
        node_file.write_text(node_yaml)
        if verbose:
            print(f"  Written: {node_file}")

    print(f"Written: {len(undefined)} node YAML file(s) in {out_dir}/node/")


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
        "--system-depth",
        type=int,
        default=1,
        metavar="N",
        help="Namespace depth for system.yaml components (default: 1 = top-level). "
             "Sub-modules below this depth are generated recursively.",
    )
    parser.add_argument(
        "--group-depth",
        type=int,
        default=None,
        metavar="N",
        help="(Legacy) Namespace depth for flat grouping. If set, uses flat mode "
             "instead of recursive tree mode.",
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
        "--parameter-sets",
        action="store_true",
        help="Generate parameter_set YAML files for each top-level component",
    )
    parser.add_argument(
        "--graph-json",
        default=None,
        metavar="FILE",
        help="Path to a ROS 2 graph snapshot JSON. Topics that are hard-coded "
             "(not visible as remaps in the launch XML) are merged into the "
             "corresponding node records from this snapshot.",
    )
    parser.add_argument(
        "--node-configs",
        action="store_true",
        help="Generate *.node.yaml files for node entities not already defined "
             "in any known package share directory",
    )
    parser.add_argument(
        "--package-map",
        default=None,
        metavar="FILE",
        help="Path to _package_map.yaml used to detect already-defined node "
             "entities (auto-discovered via ament_index when omitted)",
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

    if args.component_map:
        map_path = Path(args.component_map)
    else:
        map_path = Path(__file__).parent / "config" / "component_map.yaml"

    component_map = load_component_map(map_path)

    if args.verbose:
        print(f"Parsing {launch_xml} ...")
    nodes, containers = parse_launch_xml(launch_xml)
    if args.verbose:
        print(f"  Found {len(nodes)} nodes, {len(containers)} containers")

    graph_data = None
    if args.graph_json:
        graph_path = Path(args.graph_json)
        if not graph_path.exists():
            print(f"ERROR: graph JSON not found: {graph_path}", file=sys.stderr)
            return 1
        if args.verbose:
            print(f"Merging graph snapshot: {graph_path} ...")
        graph_data = parse_graph_json(graph_path)
        added = merge_graph_topics(nodes, graph_data)
        if args.verbose:
            print(f"  Graph snapshot: {len(graph_data)} nodes, {added} synthetic remaps added")

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    flat_mode = args.group_depth is not None

    if flat_mode:
        # ---- Legacy flat mode ----
        depth = args.group_depth
        groups = group_nodes(nodes, containers, depth=depth, overrides=component_map)
        if args.verbose:
            for g in groups:
                print(f"  Group '{g.name}' ({g.namespace}): {len(g.nodes)} nodes")

        connections = resolve_connections(groups)
        if args.verbose:
            print(f"  Resolved {len(connections)} cross-component connections")

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

        if not args.no_modules:
            for group in groups:
                if not group.nodes:
                    continue
                module_yaml = emit_module_yaml(group, groups)
                safe_entity = group.entity_name if group.namespace != "(root)" else "RosSystem"
                module_file = out / f"{safe_entity}.module.yaml"
                module_file.write_text(module_yaml)
                if args.verbose:
                    print(f"Written: {module_file}")
            module_count = sum(1 for g in groups if g.nodes)
            print(f"Written: {module_count} module YAML file(s) in {out}/")

        if args.node_configs:
            _emit_node_configs(
                nodes_by_entity=collect_nodes_by_entity_flat(groups),
                graph=graph_data,
                out_dir=out,
                package_map_path=Path(args.package_map) if args.package_map else find_package_map(),
                verbose=args.verbose,
            )

    else:
        # ---- Recursive tree mode ----
        top_nodes = build_namespace_tree(
            nodes, containers,
            overrides=component_map,
            top_depth=args.system_depth,
        )
        if args.verbose:
            for ns, ns_node in top_nodes.items():
                total = len(ns_node.all_nodes)
                print(f"  Component '{ns_node.name}' ({ns}): {total} nodes total")

        # Collect global pub/sub maps for connection resolution
        all_pub, all_sub = _collect_all_pub_sub(list(top_nodes.values()))

        # Build cross-component connections at top level
        from lib.connection_resolver import resolve_connections
        # Use flat groups for system-level connections (top depth only)
        groups = group_nodes(nodes, containers, depth=args.system_depth, overrides=component_map)
        connections = resolve_connections(groups)
        if args.verbose:
            print(f"  Resolved {len(connections)} cross-component connections")

        # Parameter sets
        ps_names: list[str] = []
        if args.parameter_sets:
            ps_dir = out / "parameter_set"
            ps_dir.mkdir(parents=True, exist_ok=True)
            for ns, ns_node in sorted(top_nodes.items()):
                ps_yaml = emit_parameter_set_yaml(args.system_name, ns_node.name, ns_node)
                ps_name = f"{args.system_name}_{ns_node.name}.parameter_set"
                ps_names.append(ps_name)
                ps_file = ps_dir / f"{ps_name}.yaml"
                ps_file.write_text(ps_yaml)
                if args.verbose:
                    print(f"  Written: {ps_file}")

        system_yaml = emit_system_yaml_from_tree(
            system_name=args.system_name,
            top_nodes=top_nodes,
            all_containers=containers,
            connections=connections,
            compute_unit=args.compute_unit,
            parameter_sets=ps_names if ps_names else None,
        )
        system_file = out / "system" / f"{args.system_name}.system.yaml"
        system_file.parent.mkdir(parents=True, exist_ok=True)
        system_file.write_text(system_yaml)
        print(f"Written: {system_file}")

        if not args.no_modules:
            total_modules = 0
            for ns_node in top_nodes.values():
                total_modules += _emit_recursive_modules(ns_node, all_pub, all_sub, out, args.verbose)
            print(f"Written: {total_modules} module YAML file(s) in {out}/module/")

        if args.node_configs:
            _emit_node_configs(
                nodes_by_entity=collect_nodes_by_entity(list(top_nodes.values())),
                graph=graph_data,
                out_dir=out,
                package_map_path=Path(args.package_map) if args.package_map else find_package_map(),
                verbose=args.verbose,
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
