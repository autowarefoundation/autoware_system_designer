#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

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


# ─── Process / executor discovery ────────────────────────────────────────────

# Maps component_container binary basename → ROS 2 executor type string.
_CONTAINER_EXECUTOR: Dict[str, str] = {
    "component_container": "single_threaded",
    "component_container_mt": "multi_threaded",
    "component_container_isolated": "isolated",
}


def _ros_lib_prefixes() -> List[str]:
    """
    Return the lib/ sub-directories of every entry in AMENT_PREFIX_PATH.
    Falls back to /opt/ros/ when the variable is unset.
    """
    prefixes: List[str] = []
    for p in os.environ.get("AMENT_PREFIX_PATH", "").split(":"):
        p = p.strip()
        if p:
            prefixes.append(os.path.join(p, "lib", ""))  # trailing sep for startswith
    return prefixes or [os.path.join("/opt", "ros", "")]


def _read_proc_cmdline(pid: int) -> Optional[List[str]]:
    """Read /proc/<pid>/cmdline and split on NUL bytes."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
        tokens = raw.split(b"\x00")
        return [t.decode("utf-8", errors="replace") for t in tokens if t]
    except OSError:
        return None


def _read_proc_exe(pid: int) -> Optional[str]:
    """Resolve /proc/<pid>/exe symlink → absolute binary path."""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def _read_proc_maps_libs(pid: int, lib_prefixes: List[str]) -> List[str]:
    """
    Parse /proc/<pid>/maps and return unique .so paths that live under any of
    the given lib_prefixes (ROS 2 / workspace install paths).
    """
    libs: Set[str] = set()
    try:
        with open(f"/proc/{pid}/maps", "r", errors="replace") as fh:
            for line in fh:
                cols = line.rstrip().split()
                if len(cols) < 6:
                    continue
                path = cols[5]
                if ".so" not in path:
                    continue
                for pfx in lib_prefixes:
                    if path.startswith(pfx):
                        libs.add(path)
                        break
    except OSError:
        pass
    return sorted(libs)


def _parse_ros_remappings(cmdline: List[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Walk --ros-args tokens and return (node_name, namespace) from
    ``-r __node:=<name>`` / ``-r __ns:=<ns>`` remapping rules.
    """
    node_name: Optional[str] = None
    namespace: Optional[str] = None
    i = 0
    while i < len(cmdline):
        arg = cmdline[i]
        if arg in ("-r", "--remap") and i + 1 < len(cmdline):
            remap = cmdline[i + 1]
            i += 2
            if remap.startswith("__node:="):
                node_name = remap[len("__node:="):]
            elif remap.startswith("__ns:="):
                namespace = remap[len("__ns:="):]
        else:
            i += 1
    return node_name, namespace


def _package_from_exe(exe: str) -> Optional[str]:
    """
    Derive the ROS 2 package name from an executable path.

    Handles two patterns:
    1. Install-space: <prefix>/lib/<package>/<executable>
    2. Build-space (colcon): <ws>/build/<package>/…/<executable>
    """
    if not exe:
        return None
    parts = exe.replace("\\", "/").split("/")
    # Install-space: rightmost "lib" followed by at least two more components.
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "lib" and i + 2 <= len(parts) - 1:
            return parts[i + 1]
    # Build-space: colcon places executables under <ws>/build/<package>/…
    for i in range(len(parts) - 2, -1, -1):
        if parts[i] == "build":
            return parts[i + 1]
    return None


def _resolve_exe_and_pkg(
    cmdline: List[str], exe: Optional[str]
) -> Tuple[Optional[str], Optional[str]]:
    """
    Return ``(effective_exe_path, package_name)``.
    For Python launchers (python3 …) the real script is ``cmdline[1]``.
    """
    base = os.path.basename(exe or "")
    if exe and "python" not in base:
        return exe, _package_from_exe(exe)
    # Python launcher — the actual script is the first positional argument.
    script = cmdline[1] if len(cmdline) >= 2 else None
    return script or exe, _package_from_exe(script) if script else None


def _load_ament_components(lib_prefixes: List[str]) -> Dict[str, List[str]]:
    """
    Parse the ``rclcpp_components`` ament resource index for every install
    prefix derived from *lib_prefixes*.

    Returns ``{library_basename: [class_name, ...]}`` for every registered
    component factory.  One ``.so`` can contain multiple component classes.

    Index file format (one entry per line)::

        fully::qualified::ClassName;lib/pkg/libcomp.so
    """
    comp_map: Dict[str, List[str]] = {}
    seen_roots: Set[str] = set()
    for lib_pfx in lib_prefixes:
        install_pfx = os.path.dirname(lib_pfx.rstrip("/\\"))
        if install_pfx in seen_roots:
            continue
        seen_roots.add(install_pfx)
        idx_dir = os.path.join(
            install_pfx, "share", "ament_index", "resource_index", "rclcpp_components"
        )
        if not os.path.isdir(idx_dir):
            continue
        try:
            entries = os.listdir(idx_dir)
        except OSError:
            continue
        for pkg_file in entries:
            fpath = os.path.join(idx_dir, pkg_file)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        # "ClassName;lib/pkg/libcomp.so"
                        parts = line.split(";", 1)
                        class_name = parts[0].strip()
                        if len(parts) == 2:
                            lib_file = os.path.basename(parts[1].strip())
                            comp_map.setdefault(lib_file, []).append(class_name)
            except OSError:
                pass
    return comp_map


def _component_classes_from_libs(
    libraries: List[str], ament_components: Dict[str, List[str]]
) -> List[str]:
    """Return the component class names whose plugin .so appears in *libraries*."""
    classes: List[str] = []
    for lib_path in libraries:
        lb = os.path.basename(lib_path)
        if lb in ament_components:
            classes.extend(ament_components[lb])
    return sorted(set(classes))


def _scan_ros_processes(
    lib_prefixes: List[str],
) -> Tuple[Dict[str, Dict], Dict[int, Dict]]:
    """
    Scan ``/proc`` for every process that carries ``--ros-args`` in its
    command line.

    Returns two indices:

    * ``by_fq``  — ``{fq_node_name: process_entry}``  (keyed by the node's
      fully-qualified name derived from ``__node`` / ``__ns`` remappings)
    * ``by_pid`` — ``{pid: process_entry}``
    """
    by_fq: Dict[str, Dict] = {}
    by_pid: Dict[int, Dict] = {}

    try:
        pids = [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return by_fq, by_pid

    for pid in pids:
        cmdline = _read_proc_cmdline(pid)
        if not cmdline or "--ros-args" not in cmdline:
            continue

        exe_raw = _read_proc_exe(pid)
        eff_exe, pkg = _resolve_exe_and_pkg(cmdline, exe_raw)
        libs = _read_proc_maps_libs(pid, lib_prefixes)
        exe_base = os.path.basename(eff_exe or "")
        executor_type: Optional[str] = _CONTAINER_EXECUTOR.get(exe_base)

        node_name, namespace = _parse_ros_remappings(cmdline)

        entry: Dict = {
            "pid": pid,
            "exe": eff_exe,
            "package": pkg,
            "executor_type": executor_type,
            "cmdline": cmdline,
            "ros_libraries": libs,
        }
        by_pid[pid] = entry

        if node_name:
            fq = _fq_name(node_name, namespace or "/")
            by_fq[fq] = entry

    return by_fq, by_pid


def _build_process_info_map(
    graph: List[NodeGraphInfo],
    component_info_map: Dict[str, Dict],
    by_fq: Dict[str, Dict],
    by_pid: Dict[int, Dict],
    ament_components: Dict[str, List[str]],
) -> Dict[str, Optional[Dict]]:
    """
    Assign each graph node its OS process information.

    * **Composable nodes** inherit their container's process (they have no
      process of their own).
    * **Standalone nodes** are matched by their fully-qualified name, which
      must appear in ``by_fq`` (built from ``--ros-args`` remappings).
    * In both cases ``component_classes`` lists every ``rclcpp_components``
      plugin class whose ``.so`` is loaded into the process — resolving the
      "plugin class not available at runtime" limitation for containers.
    """
    # Build container-fq → pid index
    container_to_pid: Dict[str, int] = {
        fq: e["pid"]
        for fq, e in by_fq.items()
        if os.path.basename(e.get("exe") or "") in _CONTAINER_EXECUTOR
    }

    result: Dict[str, Optional[Dict]] = {}
    for n in graph:
        comp = component_info_map.get(n.fq_name)
        if comp is not None:
            # Composable node — look up the container's process.
            pid = container_to_pid.get(comp.get("container", ""))
            if pid is not None and pid in by_pid:
                e = dict(by_pid[pid])
                e["component_classes"] = _component_classes_from_libs(
                    e.get("ros_libraries", []), ament_components
                ) or None
                result[n.fq_name] = e
            else:
                result[n.fq_name] = None
        else:
            # Standalone node — match by fq_name.
            e = by_fq.get(n.fq_name)
            if e:
                e = dict(e)
                classes = _component_classes_from_libs(
                    e.get("ros_libraries", []), ament_components
                )
                # Non-empty only for container nodes themselves (ComponentManager).
                e["component_classes"] = classes or None
                result[n.fq_name] = e
            else:
                result[n.fq_name] = None

    return result


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

        print(f"[snapshot] Found {len(entries)} node(s). Collecting graph info...", file=sys.stderr)

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

        total = len(entries)

        def _print_progress(done: int, total: int) -> None:
            width = 30
            filled = int(width * done / total) if total else width
            bar = "#" * filled + "-" * (width - filled)
            sys.stderr.write(f"\r[snapshot] [{bar}] {done}/{total}")
            sys.stderr.flush()

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

            _print_progress(done, total)

            if args.sleep_per_node and args.sleep_per_node > 0.0:
                time.sleep(args.sleep_per_node)

        sys.stderr.write("\n")
        sys.stderr.flush()

        # --- Component container detection ---
        # Composable nodes are loaded into a ComponentManager container.
        # Containers expose a /{container_fq}/_container/list_nodes service that returns
        # the fully-qualified names and unique IDs of all loaded components.
        # This lets us record which nodes are composable and which container they live in.
        print("[snapshot] Detecting component containers...", file=sys.stderr)
        component_info_map: Dict[str, Dict[str, object]] = {}
        try:
            from composition_interfaces.srv import ListNodes as _ListNodesSrv  # type: ignore

            for n in graph:
                list_svc = f"{n.fq_name}/_container/list_nodes"
                if list_svc not in n.services:
                    continue
                cli = node.create_client(_ListNodesSrv, list_svc)
                try:
                    if cli.wait_for_service(timeout_sec=0.5):
                        future = cli.call_async(_ListNodesSrv.Request())
                        rclpy.spin_until_future_complete(node, future, timeout_sec=1.0)
                        if future.result() is not None:
                            resp = future.result()
                            for comp_name, comp_id in zip(
                                resp.full_node_names, resp.unique_ids
                            ):
                                component_info_map[comp_name] = {
                                    "container": n.fq_name,
                                    "component_id": int(comp_id),
                                }
                finally:
                    node.destroy_client(cli)
        except Exception as exc:  # noqa: BLE001
            errors["__component_detection__"] = f"{type(exc).__name__}: {exc}"

        # --- Process / executor discovery ---
        # Scan /proc to map each node → (PID, binary, package, executor type,
        # loaded ROS 2 libraries, plugin class names from the ament index).
        process_info_map: Dict[str, Optional[Dict]] = {}
        if not args.no_process:
            print("[snapshot] Scanning processes...", file=sys.stderr)
            try:
                _lib_pfx = _ros_lib_prefixes()
                _ament_comps = _load_ament_components(_lib_pfx)
                _by_fq, _by_pid = _scan_ros_processes(_lib_pfx)
                process_info_map = _build_process_info_map(
                    graph, component_info_map, _by_fq, _by_pid, _ament_comps
                )
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
