#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import rclpy
from rclpy.node import Node


@dataclass(frozen=True)
class NodeGraphInfo:
    name: str
    namespace: str
    fq_name: str
    publishers: Dict[str, List[str]]
    subscribers: Dict[str, List[str]]
    services: Dict[str, List[str]]
    clients: Dict[str, List[str]]


def _fq_name(name: str, namespace: str) -> str:
    if namespace == "/":
        return f"/{name}" if not name.startswith("/") else name
    namespace = namespace if namespace.startswith("/") else f"/{namespace}"
    namespace = namespace.rstrip("/")
    return f"{namespace}/{name}" if not name.startswith("/") else name


def _as_type_map(pairs: List[Tuple[str, List[str]]]) -> Dict[str, List[str]]:
    # Pairs come from rclpy graph APIs: List[Tuple[name, List[types]]]
    result: Dict[str, List[str]] = {}
    for topic_or_srv, types in pairs:
        result[topic_or_srv] = list(types)
    return result


def _compile_filter(pattern: Optional[str]) -> Optional[re.Pattern]:
    if not pattern:
        return None
    return re.compile(pattern)


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
        help=(
            "Output JSON file path. Default: ./ros2_graph_snapshots/<timestamp>/graph.json"
        ),
    )
    parser.add_argument(
        "--filter",
        default=None,
        help=(
            "Regex to include only matching fully-qualified node names (e.g. '^/planning/')."
        ),
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
        "--params",
        choices=["none", "names"],
        default="none",
        help=(
            "Parameter collection mode. 'none' is safest. 'names' lists parameter names via parameter services "
            "(can still be heavy on very large graphs)."
        ),
    )

    args = parser.parse_args()

    rclpy.init(args=None)
    node = Node("graph_snapshot")

    try:
        # Let discovery settle.
        end_t = time.time() + max(0.0, args.spin_seconds)
        while time.time() < end_t:
            rclpy.spin_once(node, timeout_sec=0.1)

        node_filter = _compile_filter(args.filter)

        # rclpy returns (name, namespace) tuples.
        all_nodes = node.get_node_names_and_namespaces()

        # Build a list with fq names for filtering & duplicate reporting.
        entries: List[Tuple[str, str, str]] = []
        for name, namespace in all_nodes:
            fq = _fq_name(name, namespace)
            if (not args.include_hidden) and "/_" in fq:
                continue
            if node_filter and not node_filter.search(fq):
                continue
            entries.append((name, namespace, fq))

        # Sort for stable output.
        entries.sort(key=lambda x: x[2])
        if args.max_nodes and args.max_nodes > 0:
            entries = entries[: args.max_nodes]

        # Track duplicates (same fq name appearing multiple times).
        fq_counts: Dict[str, int] = {}
        for _, _, fq in entries:
            fq_counts[fq] = fq_counts.get(fq, 0) + 1
        duplicates = sorted([fq for fq, c in fq_counts.items() if c > 1])

        graph: List[NodeGraphInfo] = []
        errors: Dict[str, str] = {}
        param_names: Dict[str, List[str]] = {}

        for name, namespace, fq in entries:
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
                        publishers=_as_type_map(pubs),
                        subscribers=_as_type_map(subs),
                        services=_as_type_map(srvs),
                        clients=_as_type_map(clis),
                    )
                )

                if args.params == "names":
                    # Avoid parameter queries when we know the name is duplicated; ROS 2 tools cannot
                    # disambiguate instances that share an exact node name.
                    if fq_counts.get(fq, 0) > 1:
                        param_names[fq] = ["<skipped: duplicate node name>"]
                    else:
                        try:
                            from rclpy.parameter_client import AsyncParametersClient

                            client = AsyncParametersClient(node, fq)
                            if not client.wait_for_service(timeout_sec=0.5):
                                param_names[fq] = ["<no parameter service>"]
                            else:
                                future = client.list_parameters(prefixes=[], depth=0)
                                rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
                                if future.result() is None:
                                    param_names[fq] = ["<timeout listing parameters>"]
                                else:
                                    # list_parameters returns ListParameters.Response
                                    names_list = list(future.result().result.names)
                                    names_list.sort()
                                    param_names[fq] = names_list
                        except Exception as exc:  # noqa: BLE001
                            param_names[fq] = [f"<error: {type(exc).__name__}: {exc}>"]
            except Exception as exc:  # noqa: BLE001
                errors[fq] = f"{type(exc).__name__}: {exc}"

            if args.sleep_per_node and args.sleep_per_node > 0.0:
                time.sleep(args.sleep_per_node)

        payload = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "filtered": bool(args.filter),
            "filter": args.filter,
            "node_count": len(entries),
            "duplicates": duplicates,
            "errors": errors,
            "param_names": param_names,
            "nodes": [asdict(n) for n in graph],
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

        print(out_path)
        return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
