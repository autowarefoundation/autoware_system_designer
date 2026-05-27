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

Entry point is :func:`populate_builder`.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional

from ..core.config import ActorConfig
from ..core.coordinator import Coordinator, CoordinatorBuilder, _MemberEntry
from ..core.regular_actor import NodeSpec
from .common.namespace import node_fqn, unique_node_name
from .common.params import params_dict
from .composable.actor import ComposableNodeActor, ComposableSpec
from .composable.spec import composable_spec, glog_spec_for
from .container.cmdline import container_cmdline
from .container.worker import RosWorker
from .launch_file.cmdline import include_cmdline
from .single_node.cmdline import node_cmdline

logger = logging.getLogger(__name__)


# ---- system_structure traversal -----------------------------------------


def collect_nodes(entity: Mapping[str, Any], *, ecu: Optional[str] = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    _collect(entity, out, ecu)
    return out


def _collect(entity: Mapping[str, Any], out: list[dict[str, Any]], ecu: Optional[str]) -> None:
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


# ---- Global param file resolution ----------------------------------------


def _glog_available() -> bool:
    """Return True if autoware_glog_component is installed in the ament index."""
    try:
        from ament_index_python.packages import get_package_share_directory

        get_package_share_directory("autoware_glog_component")
        return True
    except Exception:  # noqa: BLE001
        return False


def _global_param_files(nodes: list[dict[str, Any]]) -> list[str]:
    """Find the vehicle_info YAML for each global_parameter_loader node.

    SetParameter from ros2 launch doesn't cross subprocess boundaries, so
    we pass the YAML via --params-file to every node instead.
    """
    files: list[str] = []
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
                logger.info("global vehicle_info params: %s (model=%s)", yaml_path, vehicle_model)
                files.append(str(yaml_path))
            else:
                logger.warning("vehicle_info.param.yaml not found at %s", yaml_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cannot resolve vehicle_info for model %r: %s", vehicle_model, exc)
    return files


# ---- Top-level orchestration --------------------------------------------


def populate_builder(
    system_data: Mapping[str, Any],
    *,
    ecu: Optional[str] = None,
    config: Optional[ActorConfig] = None,
) -> tuple[CoordinatorBuilder, RosWorker]:
    """Translate *system_data* into a CoordinatorBuilder ready to ``build()``.

    Returns ``(builder, worker)``; caller must ``await worker.start()`` before
    ``coord.run()`` and ``await worker.stop()`` in the finally block.
    """
    nodes = collect_nodes(system_data, ecu=ecu)
    builder = CoordinatorBuilder(default_config=config or ActorConfig())
    worker = RosWorker()
    global_files = _global_param_files(nodes)

    composables_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    container_entries: dict[str, _MemberEntry] = {}
    inject_glog = _glog_available()

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
            spec = NodeSpec(name=unique_node_name(n), cmd=node_cmdline(n, extra_param_files=global_files))
            builder.add_node(spec)

        elif state == "node_container":
            if not name:
                continue
            spec = NodeSpec(name=unique_node_name(n), cmd=container_cmdline(n))
            entry = builder.add_node(spec)
            container_entries[node_fqn(name, n.get("namespace"))] = entry

        elif state == "ros2_launch_file":
            is_global_loader = n["launcher"].get("package") == "autoware_global_parameter_loader"
            extra_files = [] if is_global_loader else global_files
            cmd = include_cmdline(n, global_files=extra_files)
            spec = NodeSpec(name=unique_node_name(n), cmd=cmd)
            builder.add_node(spec)

        # composable_node: handled below by the container hook

    for target_fqn, members in composables_by_target.items():
        entry = container_entries.get(target_fqn)
        if entry is None:
            logger.warning(
                "composable nodes target missing container %r; skipping %d members",
                target_fqn,
                len(members),
            )
            continue

        load_specs: list[ComposableSpec] = [glog_spec_for(target_fqn)] if inject_glog else []
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
