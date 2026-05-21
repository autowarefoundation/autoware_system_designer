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

"""Translation from system_structure JSON to a populated CoordinatorBuilder.

Owns cmdline construction for regular nodes, containers, and ros2_launch_file
includes, and :class:`ComposableSpec` construction for composable nodes.
Entry point is :func:`populate_builder`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .composable_actor import ComposableNodeActor, ComposableSpec
from .config import ActorConfig
from .container_actor import RosWorker
from .coordinator import Coordinator, CoordinatorBuilder, _MemberEntry
from .regular_actor import NodeSpec

logger = logging.getLogger(__name__)

_GLOG_PKG = "autoware_glog_component"
_GLOG_PLUGIN = "autoware::glog_component::GlogComponent"
_GLOG_NAME = "glog_component"

def _ns_segments(ns: Any) -> List[str]:
    if ns is None:
        return []
    if isinstance(ns, str):
        return [s for s in ns.split("/") if s]
    if isinstance(ns, (list, tuple)):
        return [str(p).strip("/") for p in ns if p]
    return []


def parent_namespace(ns: Any, name: Optional[str] = None) -> str:
    """Return the parent ROS namespace for an entity.

    system_structure stores ``namespace`` as the full hierarchical path of
    the entity itself (e.g. ``['sensing', 'lidar', 'lidar_top']`` for a
    composable named ``lidar_top``). For ROS the namespace must exclude
    the entity's own name. We strip the last segment when it equals *name*;
    otherwise we use the segments as-is.
    """
    segs = _ns_segments(ns)
    if name and segs and segs[-1] == name:
        segs = segs[:-1]
    return "/" + "/".join(segs) if segs else "/"


def node_fqn(name: str, namespace: Any) -> str:
    """Canonical ROS FQN: ``<parent_namespace>/<name>``."""
    parent = parent_namespace(namespace, name).rstrip("/")
    return f"{parent}/{name}" if parent else f"/{name}"


_BOOL_TYPES = {"bool", "boolean"}
_INT_TYPES = {
    "int", "integer", "int8", "int16", "int32", "int64",
    "uint8", "uint16", "uint32", "uint64", "short", "long",
}
_FLOAT_TYPES = {"float", "double", "float32", "float64"}

# Matches ROS 2 launch $(command '<shell-cmd>' ['<on_error>']) substitution.
_COMMAND_SUB = re.compile(r"^\$\(command\s+'(.+?)'(?:\s+'[^']*')?\s*\)$", re.DOTALL)


# ---- system_structure traversal -----------------------------------------


def collect_nodes(entity: Mapping[str, Any], *, ecu: Optional[str] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    _collect(entity, out, ecu)
    return out


def _collect(entity: Mapping[str, Any], out: List[Dict[str, Any]], ecu: Optional[str]) -> None:
    if entity.get("entity_type") == "node" and entity.get("launcher"):
        if ecu is None or entity.get("compute_unit") == ecu:
            out.append(
                {
                    "name": entity.get("name", ""),
                    "namespace": entity.get("namespace", "/"),
                    "path": entity.get("path", ""),
                    "launcher": entity["launcher"],
                    "parameters": entity.get("parameters", []),
                    "parameter_files_all": entity.get("parameter_files_all", []),
                }
            )
    for child in entity.get("children", []):
        _collect(child, out, ecu)


# ---- Parameter / value resolution ---------------------------------------


def resolve_value(value: Any, type_hint: Optional[str] = None) -> Any:
    """Coerce a JSON param value to a Python value using *type_hint*.

    Mirrors the old ``_resolve_value`` in ``ros2_launcher/direct_launcher.py``.
    ``$(command '<cmd>' ...)`` substitutions are resolved by running the shell
    command and using its stdout.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, list)):
        return value

    s = str(value)

    m = _COMMAND_SUB.match(s.strip())
    if m:
        cmd_str = m.group(1)
        try:
            result = subprocess.run(
                cmd_str, shell=True, capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return result.stdout.strip()
            logger.warning(
                "command substitution failed (rc=%d): %s\n%s",
                result.returncode, cmd_str, result.stderr.strip(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("command substitution error: %s: %s", cmd_str, exc)
        return ""
    hint = (type_hint or "").strip().lower()

    if hint in _BOOL_TYPES:
        return s.lower() not in ("false", "0", "no", "off", "")
    if hint in _INT_TYPES:
        try:
            return int(s)
        except ValueError:
            pass
    if hint in _FLOAT_TYPES:
        try:
            return float(s)
        except ValueError:
            pass

    import yaml
    try:
        parsed = yaml.safe_load(s)
        if isinstance(parsed, (bool, int, float, list)):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    return s


def params_dict(params: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    return {p["name"]: resolve_value(p["value"], p.get("type")) for p in params}


def parameter_files(node_spec: Mapping[str, Any]) -> List[str]:
    return [
        f["path"]
        for f in node_spec.get("parameter_files_all", [])
        if f.get("parameter_type") != "DEFAULT_FILE"
    ]


def remap_pairs(ports: Iterable[Mapping[str, Any]]) -> List[Tuple[str, str]]:
    return [
        (p["remap_target"], p["topic"])
        for p in ports
        if p.get("remap_target") and p.get("topic")
    ]


# ---- ROS arg formatting -------------------------------------------------


_VALID_PARAM_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_./]*$")


def _ros_arg_for_param(name: str, value: Any) -> List[str]:
    """Render ``-p name:=value`` in a YAML-typed form ROS 2 will accept.

    Lists, dicts, strings, bools, numbers all need consistent YAML
    representation — using ``yaml.safe_dump`` gives us that for free.
    """
    import yaml

    if not _VALID_PARAM_KEY.match(name):
        logger.warning("skipping unsafe param name %r", name)
        return []
    encoded = yaml.safe_dump(value, default_flow_style=True).strip()
    return ["-p", f"{name}:={encoded}"]


def _ros_args(
    *,
    name: str,
    namespace: Any,
    inline_params: Mapping[str, Any],
    param_files: Sequence[str],
    remaps: Sequence[Tuple[str, str]],
) -> List[str]:
    args: List[str] = ["--ros-args"]
    ns = parent_namespace(namespace, name)
    if ns and ns != "/":
        args += ["-r", f"__ns:={ns}"]
    if name:
        args += ["-r", f"__node:={name}"]
    for k, v in inline_params.items():
        args += _ros_arg_for_param(k, v)
    for f in param_files:
        args += ["--params-file", f]
    for src, dst in remaps:
        args += ["-r", f"{src}:={dst}"]
    return args


# ---- Cmdline producers --------------------------------------------------


def node_cmdline(
    spec: Mapping[str, Any], extra_param_files: Optional[List[str]] = None
) -> List[str]:
    launcher = spec["launcher"]
    inline = params_dict(spec.get("parameters", []))
    extra_args = launcher.get("args", "")
    cmd = ["ros2", "run", launcher["package"], launcher["executable"]]
    if extra_args:
        cmd += extra_args.split()
    cmd += _ros_args(
        name=spec["name"],
        namespace=spec["namespace"],
        inline_params=inline,
        param_files=parameter_files(spec) + (extra_param_files or []),
        remaps=remap_pairs(launcher.get("ports", [])),
    )
    return cmd


def container_cmdline(spec: Mapping[str, Any]) -> List[str]:
    launcher = spec["launcher"]
    cmd = ["ros2", "run", launcher["package"], launcher["executable"]]
    cmd += _ros_args(
        name=spec["name"],
        namespace=spec["namespace"],
        inline_params={},
        param_files=[],
        remaps=[],
    )
    return cmd


def include_cmdline(
    spec: Mapping[str, Any], global_files: Optional[List[str]] = None
) -> List[str]:
    """Command to run a ros2_launch_file wrapper via launch_runner (mini LaunchService).

    Uses ``python3 -m autoware_system_designer.runtime.launch_runner`` instead of
    the ``ros2 launch`` CLI so that global params can be injected as SetParameter
    actions before the include — something the CLI cannot do across process boundaries.
    launch_ros is imported lazily inside launch_runner to avoid build-time errors.
    """
    launcher = spec["launcher"]
    cmd = [
        sys.executable, "-m", "autoware_system_designer.runtime.launch_runner",
        "--pkg", launcher["package"],
        "--file", launcher["ros2_launch_file"],
    ]
    for k, v in params_dict(spec.get("parameters", [])).items():
        if v is None or v == "":
            logger.debug("skipping empty launch arg %r for %s", k, launcher["ros2_launch_file"])
            continue
        cmd += ["--launch-arg", f"{k}:={v}"]
    for f in (global_files or []):
        cmd += ["--global-params-file", f]
    return cmd


# ---- Composable specs ---------------------------------------------------


def composable_spec(
    spec: Mapping[str, Any], extra_param_files: Optional[List[str]] = None
) -> ComposableSpec:
    launcher = spec["launcher"]
    inline = params_dict(spec.get("parameters", []))
    extra = (
        {"use_intra_process_comms": True}
        if launcher.get("use_intra_process_comms")
        else {}
    )
    ns = parent_namespace(spec.get("namespace"), spec.get("name"))
    return ComposableSpec(
        name=_unique_name(spec),
        package=launcher["package"],
        plugin=launcher["plugin"],
        node_name=spec["name"],
        namespace=ns,
        target_container_fqn=launcher.get("container_target", ""),
        remap_rules=remap_pairs(launcher.get("ports", [])),
        parameter_files=parameter_files(spec) + (extra_param_files or []),
        inline_parameters=inline,
        extra_arguments=extra,
    )


def glog_spec_for(container_target_fqn: str) -> ComposableSpec:
    ns_parts = container_target_fqn.rsplit("/", 1)
    container_ns = ns_parts[0] or "/"
    return ComposableSpec(
        name=f"{container_target_fqn}/{_GLOG_NAME}",
        package=_GLOG_PKG,
        plugin=_GLOG_PLUGIN,
        node_name=_GLOG_NAME,
        namespace=container_ns,
        target_container_fqn=container_target_fqn,
    )


# ---- Top-level orchestration --------------------------------------------


def _global_param_files(nodes: List[Dict[str, Any]]) -> List[str]:
    """Resolve vehicle_info YAML from global_parameter_loader nodes.

    In a ros2 launch context, ``global_params.launch.py`` uses ``SetParameter``
    to broadcast vehicle_info params (wheel_radius, wheel_base, …) to all
    subsequently launched nodes.  That mechanism is launch-context-local and
    does not work when the file runs as an independent subprocess.  We replicate
    the effect by finding the vehicle_info YAML and returning it so callers can
    append it to every node's ``--params-file`` list.
    """
    files: List[str] = []
    for n in nodes:
        if n["launcher"].get("package") != "autoware_global_parameter_loader":
            continue
        if n["launcher"].get("launch_state") != "ros2_launch_file":
            continue
        vehicle_model = params_dict(n.get("parameters", [])).get("vehicle_model")
        if not vehicle_model:
            continue
        try:
            from ament_index_python.packages import get_package_share_directory
            pkg_share = get_package_share_directory(f"{vehicle_model}_description")
            yaml_path = Path(pkg_share) / "config" / "vehicle_info.param.yaml"
            if yaml_path.exists():
                logger.info(
                    "global vehicle_info params: %s (model=%s)", yaml_path, vehicle_model
                )
                files.append(str(yaml_path))
            else:
                logger.warning("vehicle_info.param.yaml not found at %s", yaml_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cannot resolve vehicle_info for model %r: %s", vehicle_model, exc)
    return files


def populate_builder(
    system_data: Mapping[str, Any],
    *,
    ecu: Optional[str] = None,
    config: Optional[ActorConfig] = None,
) -> Tuple[CoordinatorBuilder, RosWorker]:
    """Translate *system_data* into a CoordinatorBuilder ready to ``build()``.

    Returns the builder and the :class:`RosWorker` that must be started
    before composable loads can succeed. The caller (direct_launcher) owns
    both — typical pattern::

        builder, worker = populate_builder(data, ecu=ecu)
        coord = builder.build()
        worker.start()
        try:
            asyncio.run(coord.run())
        finally:
            worker.stop()
    """
    nodes = collect_nodes(system_data, ecu=ecu)
    builder = CoordinatorBuilder(default_config=config or ActorConfig())
    worker = RosWorker()
    global_files = _global_param_files(nodes)

    composables_by_target: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    container_entries: Dict[str, _MemberEntry] = {}

    for n in nodes:
        state = n["launcher"]["launch_state"]
        if state == "composable_node":
            target = n["launcher"].get("container_target", "")
            if target:
                composables_by_target[target].append(n)

    for n in nodes:
        state = n["launcher"]["launch_state"]
        name = n.get("name", "")

        if state == "single_node":
            if not name or not n["launcher"].get("executable"):
                continue
            spec = NodeSpec(name=_unique_name(n), cmd=node_cmdline(n, extra_param_files=global_files))
            builder.add_node(spec)

        elif state == "node_container":
            if not name:
                continue
            spec = NodeSpec(name=_unique_name(n), cmd=container_cmdline(n))
            entry = builder.add_node(spec)
            # Key by FQN so composable nodes (whose container_target is a
            # leading-slash FQN) can find this container.
            container_entries[node_fqn(name, n.get("namespace"))] = entry

        elif state == "ros2_launch_file":
            # Skip injecting global params into the loader unit that produces them.
            is_global_loader = n["launcher"].get("package") == "autoware_global_parameter_loader"
            extra_files = [] if is_global_loader else global_files
            cmd = include_cmdline(n, global_files=extra_files)
            spec = NodeSpec(name=_unique_name(n), cmd=cmd)
            builder.add_node(spec)

        # composable_node: handled below by the container hook

    # Composable-node orchestration: attach a post-start hook per container.
    for target_fqn, members in composables_by_target.items():
        entry = container_entries.get(target_fqn)
        if entry is None:
            logger.warning(
                "composable nodes target missing container %r; skipping %d members",
                target_fqn,
                len(members),
            )
            continue

        load_specs: List[ComposableSpec] = [glog_spec_for(target_fqn)]
        for m in members:
            load_specs.append(composable_spec(m, extra_param_files=global_files))

        async def _hook(coord: Coordinator, *, specs=load_specs, ce=entry) -> None:
            ready_signal = ce.ready_signal
            for s in specs:
                actor = ComposableNodeActor(
                    spec=s,
                    ros_worker=worker,
                    container_ready=ready_signal,
                    state_tx=coord.state_queue,
                    shutdown=coord.shutdown_event,
                )
                coord.schedule_task(actor.run())

        builder.add_post_start_hook(_hook)

    return builder, worker


def _unique_name(node: Mapping[str, Any]) -> str:
    """Build a unique key for this node.

    Prefer the structural path when present; otherwise compose the FQN
    from namespace + name (with launch_state suffix for disambiguation
    when the same name appears as both a container and a composable).
    """
    path = node.get("path")
    if path:
        return str(path)
    fqn = node_fqn(node.get("name", "_unnamed_"), node.get("namespace"))
    state = node.get("launcher", {}).get("launch_state", "")
    return f"{fqn}#{state}" if state else fqn
