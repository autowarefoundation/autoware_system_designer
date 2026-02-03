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
        param_values: Dict[str, Dict[str, str]] = {}

        # Optional parameter-service access (fallback if rclpy.parameter_client is unavailable).
        try:
            from rclpy.parameter_client import AsyncParametersClient  # type: ignore
        except ModuleNotFoundError:
            AsyncParametersClient = None  # type: ignore

        def _param_value_to_string(val: object) -> str:
            try:
                from rcl_interfaces.msg import ParameterType
            except Exception:  # noqa: BLE001
                return str(val)
            if hasattr(val, "type"):
                t = val.type
                if t == ParameterType.PARAMETER_NOT_SET:
                    return "<not set>"
                if t == ParameterType.PARAMETER_BOOL:
                    return str(getattr(val, "bool_value", False)).lower()
                if t == ParameterType.PARAMETER_INTEGER:
                    return str(getattr(val, "integer_value", 0))
                if t == ParameterType.PARAMETER_DOUBLE:
                    return repr(getattr(val, "double_value", 0.0))
                if t == ParameterType.PARAMETER_STRING:
                    return str(getattr(val, "string_value", ""))
                if t == ParameterType.PARAMETER_BYTE_ARRAY:
                    return json.dumps(list(getattr(val, "byte_array_value", [])))
                if t == ParameterType.PARAMETER_BOOL_ARRAY:
                    return json.dumps(list(getattr(val, "bool_array_value", [])))
                if t == ParameterType.PARAMETER_INTEGER_ARRAY:
                    return json.dumps(list(getattr(val, "integer_array_value", [])))
                if t == ParameterType.PARAMETER_DOUBLE_ARRAY:
                    return json.dumps(list(getattr(val, "double_array_value", [])))
                if t == ParameterType.PARAMETER_STRING_ARRAY:
                    return json.dumps(list(getattr(val, "string_array_value", [])))
            if hasattr(val, "value"):
                return str(getattr(val, "value"))
            return str(val)

        def list_parameters_fallback(target_fq: str) -> Tuple[Optional[List[str]], Optional[str]]:
            try:
                from rcl_interfaces.srv import ListParameters
            except Exception as exc:  # noqa: BLE001
                return None, f"<error: {type(exc).__name__}: {exc}>"
            client = node.create_client(ListParameters, f"{target_fq}/list_parameters")
            if not client.wait_for_service(timeout_sec=0.5):
                return None, "<no parameter service>"
            req = ListParameters.Request()
            req.prefixes = []
            req.depth = 0
            future = client.call_async(req)
            rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
            if future.result() is None:
                return None, "<timeout listing parameters>"
            names_list = list(future.result().result.names)
            names_list.sort()
            return names_list, None

        def get_parameters_fallback(target_fq: str, names: List[str]) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
            try:
                from rcl_interfaces.srv import GetParameters
            except Exception as exc:  # noqa: BLE001
                return None, f"<error: {type(exc).__name__}: {exc}>"
            client = node.create_client(GetParameters, f"{target_fq}/get_parameters")
            if not client.wait_for_service(timeout_sec=0.5):
                return None, "<no parameter service>"
            req = GetParameters.Request()
            req.names = names
            future = client.call_async(req)
            rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
            if future.result() is None:
                return None, "<timeout getting parameters>"
            values: Dict[str, str] = {}
            for name, val in zip(names, future.result().values):
                values[name] = _param_value_to_string(val)
            return values, None

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

                if args.params in ("names", "values"):
                    # Avoid parameter queries when we know the name is duplicated; ROS 2 tools cannot
                    # disambiguate instances that share an exact node name.
                    if fq_counts.get(fq, 0) > 1:
                        param_names[fq] = ["<skipped: duplicate node name>"]
                        if args.params == "values":
                            param_values[fq] = {"<skipped: duplicate node name>": ""}
                    else:
                        try:
                            if AsyncParametersClient is not None:
                                client = AsyncParametersClient(node, fq)
                                if not client.wait_for_service(timeout_sec=0.5):
                                    param_names[fq] = ["<no parameter service>"]
                                    if args.params == "values":
                                        param_values[fq] = {"<no parameter service>": ""}
                                else:
                                    future = client.list_parameters(prefixes=[], depth=0)
                                    rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
                                    if future.result() is None:
                                        param_names[fq] = ["<timeout listing parameters>"]
                                        if args.params == "values":
                                            param_values[fq] = {"<timeout listing parameters>": ""}
                                    else:
                                        # list_parameters returns ListParameters.Response
                                        names_list = list(future.result().result.names)
                                        names_list.sort()
                                        param_names[fq] = names_list
                                        if args.params == "values":
                                            if not names_list:
                                                param_values[fq] = {}
                                            else:
                                                future_vals = client.get_parameters(names_list)
                                                rclpy.spin_until_future_complete(node, future_vals, timeout_sec=1.0)
                                                if future_vals.result() is None:
                                                    param_values[fq] = {"<timeout getting parameters>": ""}
                                                else:
                                                    values: Dict[str, str] = {}
                                                    for name, val in zip(names_list, future_vals.result().values):
                                                        values[name] = _param_value_to_string(val)
                                                    param_values[fq] = values
                            else:
                                names_list, status = list_parameters_fallback(fq)
                                if status:
                                    param_names[fq] = [status]
                                    if args.params == "values":
                                        param_values[fq] = {status: ""}
                                else:
                                    param_names[fq] = names_list or []
                                    if args.params == "values":
                                        if not names_list:
                                            param_values[fq] = {}
                                        else:
                                            values, v_status = get_parameters_fallback(fq, names_list)
                                            if v_status:
                                                param_values[fq] = {v_status: ""}
                                            else:
                                                param_values[fq] = values or {}
                        except Exception as exc:  # noqa: BLE001
                            param_names[fq] = [f"<error: {type(exc).__name__}: {exc}>"]
                            if args.params == "values":
                                param_values[fq] = {f"<error: {type(exc).__name__}: {exc}>": ""}
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
            "param_values": param_values,
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
