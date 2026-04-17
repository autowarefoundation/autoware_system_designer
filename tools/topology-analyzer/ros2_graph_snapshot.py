#!/usr/bin/env python3

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime

import rclpy
from rclpy.node import Node

from lib.snapshot.components import detect_components
from lib.snapshot.graph import NodeGraphInfo, as_type_map, compile_filter, fq_name
from lib.snapshot.params import collect_node_params
from lib.snapshot.proc import scan_processes


def _print_progress(done: int, total: int) -> None:
    width = 30
    filled = int(width * done / total) if total else width
    bar = "#" * filled + "-" * (width - filled)
    sys.stderr.write(f"\r[snapshot] [{bar}] {done}/{total}")
    sys.stderr.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Lightweight ROS 2 graph snapshot (nodes + pubs/subs/services/clients). "
            "Avoids spawning many ros2 CLI processes."
        )
    )
    parser.add_argument(
        "--out",
        default=None,
        help=("Output JSON file path. Default: ./ros2_graph_snapshots/<timestamp>/graph.json"),
    )
    parser.add_argument(
        "--filter",
        default=None,
        help=("Regex to include only matching fully-qualified node names (e.g. '^/planning/')."),
    )
    parser.add_argument(
        "--max-nodes",
        type=int,
        default=0,
        help="Limit number of nodes processed (0 = no limit).",
    )
    parser.add_argument(
        "--spin-seconds",
        type=float,
        default=1.0,
        help="Seconds to wait for discovery before snapshotting.",
    )
    parser.add_argument(
        "--sleep-per-node",
        type=float,
        default=0.0,
        help="Optional small sleep per node to reduce CPU/network spikes (e.g. 0.01).",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="Include hidden node names if exposed by the graph APIs.",
    )
    parser.add_argument(
        "--no-process",
        action="store_true",
        help=(
            "Skip OS process / executor discovery. "
            "By default the snapshot enriches every node with its PID, "
            "binary path, originating package, executor type, loaded ROS 2 "
            "libraries, and (for component containers) resolved plugin class "
            "names from the ament index. Use this flag when /proc is "
            "unavailable or a lean snapshot is preferred."
        ),
    )
    parser.add_argument(
        "--params",
        choices=["none", "names", "values"],
        default="values",
        help=(
            "Parameter collection mode. 'values' is default and fetches current parameter values "
            "(includes names; can be heavy on very large graphs). "
            "'names' lists parameter names only. 'none' disables parameter queries."
        ),
    )

    args = parser.parse_args()

    rclpy.init(args=None)
    node = Node("graph_snapshot")

    try:
        # Let discovery settle.
        print(f"[snapshot] Waiting {args.spin_seconds}s for graph discovery...", file=sys.stderr)
        end_t = time.time() + max(0.0, args.spin_seconds)
        while time.time() < end_t:
            rclpy.spin_once(node, timeout_sec=0.1)

        node_filter = compile_filter(args.filter)

        # rclpy returns (name, namespace) tuples.
        all_nodes = node.get_node_names_and_namespaces()

        # Build a list with fq names for filtering & duplicate reporting.
        entries = []
        for name, namespace in all_nodes:
            fq = fq_name(name, namespace)
            if (not args.include_hidden) and "/_" in fq:
                continue
            if node_filter and not node_filter.search(fq):
                continue
            entries.append((name, namespace, fq))

        # Sort for stable output.
        entries.sort(key=lambda x: x[2])
        if args.max_nodes and args.max_nodes > 0:
            entries = entries[: args.max_nodes]

        print(f"[snapshot] Found {len(entries)} node(s). Collecting graph info...", file=sys.stderr)

        # Track duplicates (same fq name appearing multiple times).
        fq_counts = {}
        for _, _, fq in entries:
            fq_counts[fq] = fq_counts.get(fq, 0) + 1
        duplicates = sorted(fq for fq, c in fq_counts.items() if c > 1)

        graph = []
        errors = {}
        param_names = {}
        param_values = {}

        # Optional parameter-service access (fallback if rclpy.parameter_client is unavailable).
        try:
            from rclpy.parameter_client import AsyncParametersClient  # type: ignore
        except ModuleNotFoundError:
            AsyncParametersClient = None  # type: ignore

        total = len(entries)
        _print_progress(0, total)

        for done, (name, namespace, fq) in enumerate(entries, 1):
            try:
                pubs = node.get_publisher_names_and_types_by_node(name, namespace)
                subs = node.get_subscriber_names_and_types_by_node(name, namespace)
                srvs = node.get_service_names_and_types_by_node(name, namespace)
                clis = node.get_client_names_and_types_by_node(name, namespace)

                graph.append(
                    NodeGraphInfo(
                        name=name,
                        namespace=namespace,
                        fq_name=fq,
                        publishers=as_type_map(pubs),
                        subscribers=as_type_map(subs),
                        services=as_type_map(srvs),
                        clients=as_type_map(clis),
                    )
                )

                if args.params in ("names", "values"):
                    # Avoid parameter queries when we know the name is duplicated; ROS 2 tools cannot
                    # disambiguate instances that share an exact node name.
                    if fq_counts.get(fq, 0) > 1:
                        param_names[fq] = ["<skipped: duplicate node name>"]
                        if args.params == "values":
                            param_values[fq] = {"<skipped: duplicate node name>": ""}
                    else:
                        names_list, values_dict = collect_node_params(
                            node, fq, args.params, AsyncParametersClient
                        )
                        param_names[fq] = names_list
                        if args.params == "values":
                            param_values[fq] = values_dict

            except Exception as exc:  # noqa: BLE001
                errors[fq] = f"{type(exc).__name__}: {exc}"

            _print_progress(done, total)

            if args.sleep_per_node and args.sleep_per_node > 0.0:
                time.sleep(args.sleep_per_node)

        sys.stderr.write("\n")
        sys.stderr.flush()

        # --- Component container detection ---
        print("[snapshot] Detecting component containers...", file=sys.stderr)
        component_info_map, comp_err = detect_components(node, graph)
        if comp_err:
            errors["__component_detection__"] = comp_err

        # --- Process / executor discovery ---
        process_info_map = {}
        if not args.no_process:
            print("[snapshot] Scanning processes...", file=sys.stderr)
            try:
                process_info_map = scan_processes(graph, component_info_map)
            except Exception as exc:  # noqa: BLE001
                errors["__process_discovery__"] = f"{type(exc).__name__}: {exc}"

        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "filtered": bool(args.filter),
            "filter": args.filter,
            "node_count": len(entries),
            "duplicates": duplicates,
            "errors": errors,
            "param_names": param_names,
            "param_values": param_values,
            "nodes": [
                {
                    **asdict(n),
                    "component_info": component_info_map.get(n.fq_name),
                    "process": process_info_map.get(n.fq_name),
                }
                for n in graph
            ],
        }

        if args.out:
            out_path = args.out
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = os.path.join(os.getcwd(), "ros2_graph_snapshots", ts)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, "graph.json")

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)

        print(f"[snapshot] Done. Written to: {out_path}", file=sys.stderr)
        print(out_path)
        return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
