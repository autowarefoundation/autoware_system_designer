#!/usr/bin/env python3
# OS process / executor discovery for ros2_graph_snapshot.py.
# Reads /proc to map each ROS 2 node to its PID, binary, package name,
# executor type, loaded ROS 2 libraries, and ament-index component classes.

import os
from typing import Dict, List, Optional, Set, Tuple

from .graph import NodeGraphInfo
from .graph import fq_name as _fq_name

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
                node_name = remap[len("__node:=") :]
            elif remap.startswith("__ns:="):
                namespace = remap[len("__ns:=") :]
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


def _resolve_exe_and_pkg(cmdline: List[str], exe: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
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
        idx_dir = os.path.join(install_pfx, "share", "ament_index", "resource_index", "rclcpp_components")
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


def _component_classes_from_libs(libraries: List[str], ament_components: Dict[str, List[str]]) -> List[str]:
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
            by_fq[_fq_name(node_name, namespace or "/")] = entry

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
        fq: e["pid"] for fq, e in by_fq.items() if os.path.basename(e.get("exe") or "") in _CONTAINER_EXECUTOR
    }

    result: Dict[str, Optional[Dict]] = {}
    for n in graph:
        comp = component_info_map.get(n.fq_name)
        if comp is not None:
            # Composable node — look up the container's process.
            pid = container_to_pid.get(comp.get("container", ""))
            if pid is not None and pid in by_pid:
                e = dict(by_pid[pid])
                e["component_classes"] = (
                    _component_classes_from_libs(e.get("ros_libraries", []), ament_components) or None
                )
                result[n.fq_name] = e
            else:
                result[n.fq_name] = None
        else:
            # Standalone node — match by fq_name.
            e = by_fq.get(n.fq_name)
            if e:
                e = dict(e)
                classes = _component_classes_from_libs(e.get("ros_libraries", []), ament_components)
                # Non-empty only for container nodes themselves (ComponentManager).
                e["component_classes"] = classes or None
                result[n.fq_name] = e
            else:
                result[n.fq_name] = None

    return result


def scan_processes(
    graph: List[NodeGraphInfo],
    component_info_map: Dict[str, Dict],
) -> Dict[str, Optional[Dict]]:
    """Full process discovery pipeline. Returns {fq_name: process_entry_or_None}."""
    lib_pfx = _ros_lib_prefixes()
    ament_comps = _load_ament_components(lib_pfx)
    by_fq, by_pid = _scan_ros_processes(lib_pfx)
    return _build_process_info_map(graph, component_info_map, by_fq, by_pid, ament_comps)
