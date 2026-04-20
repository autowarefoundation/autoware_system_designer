#!/usr/bin/env python3
"""Generate Autoware System Designer YAML configs from a ROS 2 launch file.

Unified pipeline
----------------
  1. Parse launcher  — flatten the launch file tree with launch_unifier
  2. Load runtime    — capture a live ROS 2 graph snapshot (optional)
  3. Combine         — merge snapshot topics into the launch-XML node records
  4. Generate        — emit system.yaml, module.yaml, and optional extras

Quick start (full pipeline):

  python generate_system_config.py \\
    --launch-package autoware_launch \\
    --launch-file    autoware.launch.xml \\
    --launch-arg     vehicle_model:=sample_vehicle \\
    --launch-arg     sensor_model:=aip_xx1 \\
    --live-snapshot \\
    --system-name    Autoware \\
    --output-dir     generated/

From a pre-generated XML (skip launch_unifier):

  python generate_system_config.py \\
    --launch-xml output/generated.launch.xml \\
    --system-name MySystem \\
    --output-dir  generated/

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
    namespace_for_entity,
)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


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

    node_base_dir = out_dir / "node"
    for entity_name in sorted(undefined):
        node_records = nodes_by_entity[entity_name]
        node_yaml = emit_node_yaml(entity_name, node_records, graph)
        ns = namespace_for_entity(node_records)
        entity_dir = node_base_dir / ns if ns else node_base_dir
        entity_dir.mkdir(parents=True, exist_ok=True)
        node_file = entity_dir / f"{entity_name}.yaml"
        node_file.write_text(node_yaml)
        if verbose:
            print(f"  Written: {node_file}")

    print(f"Written: {len(undefined)} node YAML file(s) in {out_dir}/node/")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified launcher → snapshot → system-config pipeline for Autoware System Designer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ---- Phase 1: Launch source ----------------------------------------
    launch_src = parser.add_argument_group(
        "Launch source (choose one)",
        "Provide a pre-flattened XML *or* a launch file to flatten on-the-fly.",
    )
    launch_src.add_argument(
        "--launch-xml",
        metavar="FILE",
        help="Path to a pre-generated generated.launch.xml (skips launch_unifier).",
    )
    launch_src.add_argument(
        "--launch-package",
        metavar="PKG",
        help="ROS 2 package that owns the launch file (use with --launch-file).",
    )
    launch_src.add_argument(
        "--launch-file",
        metavar="FILE",
        help="Launch file name inside the package share (required with --launch-package).",
    )
    launch_src.add_argument(
        "--launch-path",
        metavar="PATH",
        help="Absolute or relative path to a launch file (alternative to --launch-package).",
    )
    launch_src.add_argument(
        "--launch-args",
        metavar="key:=value",
        dest="launch_args",
        nargs="+",
        default=[],
        help="Launch arguments forwarded to the launch file as space-separated key:=value pairs.",
    )
    launch_src.add_argument(
        "--launch-debug",
        action="store_true",
        help="Enable launch debug logging during launch_unifier step.",
    )

    # ---- Phase 2: Runtime snapshot --------------------------------------
    snap = parser.add_argument_group(
        "Runtime snapshot (optional)",
        "Enrich connection data with live pub/sub topics from a running system.",
    )
    snap.add_argument(
        "--graph-json",
        metavar="FILE",
        help="Path to a pre-captured ROS 2 graph snapshot JSON.",
    )
    snap.add_argument(
        "--live-snapshot",
        action="store_true",
        help="Capture a live graph snapshot from the running ROS 2 system "
             "(requires rclpy and a sourced ROS 2 environment).",
    )
    snap.add_argument(
        "--snapshot-spin-seconds",
        type=float,
        default=3.0,
        metavar="N",
        help="Seconds to wait for node discovery when --live-snapshot is used (default: 3.0).",
    )
    snap.add_argument(
        "--snapshot-params",
        choices=["none", "names", "values"],
        default="names",
        help="Parameter collection depth for --live-snapshot (default: names).",
    )

    # ---- Phase 4: Generation options ------------------------------------
    gen = parser.add_argument_group("Generation options")
    gen.add_argument(
        "--output-dir",
        default="generated",
        metavar="DIR",
        help="Output directory for generated YAML files (default: ./generated).",
    )
    gen.add_argument(
        "--system-name",
        default="GeneratedSystem",
        metavar="NAME",
        help="System name used as filename prefix and YAML name field (default: GeneratedSystem).",
    )
    gen.add_argument(
        "--compute-unit",
        default="main_ecu",
        metavar="NAME",
        help="Compute unit label assigned to all components (default: main_ecu).",
    )
    gen.add_argument(
        "--system-depth",
        type=int,
        default=1,
        metavar="N",
        help="Namespace depth for system.yaml components (default: 1). "
             "Sub-modules below this depth are generated recursively.",
    )
    gen.add_argument(
        "--group-depth",
        type=int,
        default=None,
        metavar="N",
        help="(Legacy) Fixed-depth flat grouping. If set, recursive tree mode is disabled.",
    )
    gen.add_argument(
        "--component-map",
        default=None,
        metavar="FILE",
        help="YAML file mapping namespaces to name/entity overrides "
             "(default: config/component_map.yaml next to this script).",
    )
    gen.add_argument(
        "--no-modules",
        action="store_true",
        help="Skip generating per-component module YAML files.",
    )
    gen.add_argument(
        "--parameter-sets",
        action="store_true",
        help="Generate parameter_set YAML files for each top-level component.",
    )
    gen.add_argument(
        "--node-configs",
        action="store_true",
        help="Generate *.node.yaml files for node entities not already defined in any package.",
    )
    gen.add_argument(
        "--package-map",
        default=None,
        metavar="FILE",
        help="Path to _package_map.yaml used to detect already-defined node entities "
             "(auto-discovered via ament_index when omitted).",
    )
    gen.add_argument(
        "--verbose",
        action="store_true",
        help="Print progress information.",
    )

    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    has_xml = bool(args.launch_xml)
    has_pkg = bool(args.launch_package)
    has_path = bool(args.launch_path)

    if not has_xml and not has_pkg and not has_path:
        parser.error(
            "Specify a launch source: --launch-xml, "
            "--launch-package + --launch-file, or --launch-path."
        )
    if has_xml and (has_pkg or has_path):
        parser.error("--launch-xml cannot be combined with --launch-package or --launch-path.")
    if has_pkg and has_path:
        parser.error("--launch-package and --launch-path are mutually exclusive.")
    if has_pkg and not args.launch_file:
        parser.error("--launch-file is required when --launch-package is used.")
    if args.graph_json and args.live_snapshot:
        parser.error("--graph-json and --live-snapshot are mutually exclusive.")


# ---------------------------------------------------------------------------
# Pipeline phases
# ---------------------------------------------------------------------------


def _phase1_get_launch_xml(args: argparse.Namespace, out: Path, verbose: bool) -> Path:
    """Return the path to a flattened launch XML, running launch_unifier if needed."""
    if args.launch_xml:
        launch_xml = Path(args.launch_xml)
        if not launch_xml.exists():
            print(f"ERROR: launch XML not found: {launch_xml}", file=sys.stderr)
            sys.exit(1)
        return launch_xml

    # Resolve the launch file path and run launch_unifier into <output-dir>/launch/.
    from lib.unifier import resolve_launch_path, unify_launch

    launch_file = resolve_launch_path(
        package=args.launch_package,
        file_name=args.launch_file,
        launch_path=args.launch_path,
    )

    parsed_args: list[tuple[str, str]] = []
    for raw in args.launch_args:
        if ":=" not in raw:
            print(f"ERROR: --launch-args entries must be key:=value, got: {raw!r}", file=sys.stderr)
            sys.exit(1)
        k, v = raw.split(":=", 1)
        parsed_args.append((k, v))

    if verbose:
        print(f"Running launch_unifier on: {launch_file}")
        if parsed_args:
            print(f"  Args: {parsed_args}")

    xml_path = unify_launch(
        launch_file=launch_file,
        launch_arguments=parsed_args,
        output_dir=out / "unified_launch",
        debug=args.launch_debug,
    )

    if verbose:
        print(f"  Generated: {xml_path}")

    return xml_path


def _phase2_get_graph(args: argparse.Namespace, out_dir: Path, verbose: bool) -> dict | None:
    """Return parsed graph data, capturing a live snapshot if requested."""
    if args.graph_json:
        graph_path = Path(args.graph_json)
        if not graph_path.exists():
            print(f"ERROR: graph JSON not found: {graph_path}", file=sys.stderr)
            sys.exit(1)
        if verbose:
            print(f"Loading graph snapshot: {graph_path}")
        return parse_graph_json(graph_path)

    if args.live_snapshot:
        from lib.snapshot import capture_live_snapshot

        snapshot_path = out_dir / "snapshot" / "graph.json"
        if verbose:
            print(
                f"Capturing live ROS 2 graph snapshot "
                f"(spin={args.snapshot_spin_seconds}s, params={args.snapshot_params}) ..."
            )
        capture_live_snapshot(
            output_path=snapshot_path,
            spin_seconds=args.snapshot_spin_seconds,
            params=args.snapshot_params,
        )
        if verbose:
            print(f"  Snapshot written: {snapshot_path}")
        return parse_graph_json(snapshot_path)

    return None


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)

    verbose = args.verbose

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- Phase 1: Resolve launch XML ------------------------------------
    launch_xml = _phase1_get_launch_xml(args, out, verbose)

    if verbose:
        print(f"Parsing {launch_xml} ...")
    nodes, containers = parse_launch_xml(launch_xml)
    if verbose:
        print(f"  Found {len(nodes)} nodes, {len(containers)} containers")

    # ---- Phase 2: Resolve graph snapshot --------------------------------
    graph_data = _phase2_get_graph(args, out, verbose)

    # ---- Phase 3: Merge snapshot topics into node records ---------------
    if graph_data is not None:
        added = merge_graph_topics(nodes, graph_data)
        if verbose:
            print(
                f"  Graph snapshot: {len(graph_data)} nodes, "
                f"{added} synthetic remaps added"
            )

    # ---- Phase 4: Load component map ------------------------------------
    if args.component_map:
        map_path = Path(args.component_map)
    else:
        map_path = Path(__file__).parent / "config" / "component_map.yaml"
    component_map = load_component_map(map_path)

    # ---- Phase 5: Generate configs --------------------------------------
    flat_mode = args.group_depth is not None
    sdf = out / "system_design_files"
    sdf.mkdir(parents=True, exist_ok=True)

    if flat_mode:
        _generate_flat(args, nodes, containers, component_map, graph_data, sdf, verbose)
    else:
        _generate_tree(args, nodes, containers, component_map, graph_data, sdf, verbose)

    return 0


def _generate_flat(args, nodes, containers, component_map, graph_data, out, verbose):
    """Legacy flat-mode generation."""
    depth = args.group_depth
    groups = group_nodes(nodes, containers, depth=depth, overrides=component_map)
    if verbose:
        for g in groups:
            print(f"  Group '{g.name}' ({g.namespace}): {len(g.nodes)} nodes")

    connections = resolve_connections(groups)
    if verbose:
        print(f"  Resolved {len(connections)} cross-component connections")

    system_yaml = emit_system_yaml(
        system_name=args.system_name,
        groups=groups,
        all_containers=containers,
        connections=connections,
        compute_unit=args.compute_unit,
    )
    system_dir = out / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    system_file = system_dir / f"{args.system_name}.system.yaml"
    system_file.write_text(system_yaml)
    print(f"Written: {system_file}")

    if not args.no_modules:
        module_dir = out / "module"
        module_dir.mkdir(parents=True, exist_ok=True)
        for group in groups:
            if not group.nodes:
                continue
            module_yaml = emit_module_yaml(group, groups)
            safe_entity = group.entity_name if group.namespace != "(root)" else "RosSystem"
            module_file = module_dir / f"{safe_entity}.module.yaml"
            module_file.write_text(module_yaml)
            if verbose:
                print(f"Written: {module_file}")
        module_count = sum(1 for g in groups if g.nodes)
        print(f"Written: {module_count} module YAML file(s) in {module_dir}/")

    if args.node_configs:
        _emit_node_configs(
            nodes_by_entity=collect_nodes_by_entity_flat(groups),
            graph=graph_data,
            out_dir=out,
            package_map_path=Path(args.package_map) if args.package_map else find_package_map(),
            verbose=verbose,
        )


def _generate_tree(args, nodes, containers, component_map, graph_data, out, verbose):
    """Recursive tree-mode generation (default)."""
    top_nodes = build_namespace_tree(
        nodes, containers,
        overrides=component_map,
        top_depth=args.system_depth,
    )
    if verbose:
        for ns, ns_node in top_nodes.items():
            print(f"  Component '{ns_node.name}' ({ns}): {len(ns_node.all_nodes)} nodes total")

    all_pub, all_sub = _collect_all_pub_sub(list(top_nodes.values()))

    groups = group_nodes(nodes, containers, depth=args.system_depth, overrides=component_map)
    connections = resolve_connections(groups)
    if verbose:
        print(f"  Resolved {len(connections)} cross-component connections")

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
            if verbose:
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
            total_modules += _emit_recursive_modules(ns_node, all_pub, all_sub, out, verbose)
        print(f"Written: {total_modules} module YAML file(s) in {out / 'module'}/")

    if args.node_configs:
        _emit_node_configs(
            nodes_by_entity=collect_nodes_by_entity(list(top_nodes.values())),
            graph=graph_data,
            out_dir=out,
            package_map_path=Path(args.package_map) if args.package_map else find_package_map(),
            verbose=verbose,
        )


if __name__ == "__main__":
    sys.exit(main())
