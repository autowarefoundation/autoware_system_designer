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

"""Composable-node launcher: spec, actor, and parameter helpers.

ComposableNodeActor lifecycle: Blocked → Unloaded → Loading → Loaded|Failed.

Parameter helpers convert YAML param files into rcl_interfaces/Parameter
messages for the LoadNode service (composition_interfaces has no params_file
field, so files must be read and flattened here).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

import yaml

from ...core import events as ev
from ...core.state import (
    BlockReason,
    ComposableBlocked,
    ComposableFailed,
    ComposableLoaded,
    ComposableLoading,
    ComposableUnloaded,
)
from ..common.namespace import join_fqn, parent_namespace, unique_node_name
from ..common.params import parameter_files, params_dict, remap_pairs

logger = logging.getLogger(__name__)

_GLOG_PKG = "autoware_glog_component"
_GLOG_PLUGIN = "autoware::glog_component::GlogComponent"
_GLOG_NAME = "glog_component"


# ---- Parameter helpers (LoadNode service uses inline params only) -----------


def flatten_for_fqn(yaml_paths: Iterable[str], node_fqn: str) -> "dict[str, Any]":
    """Read YAMLs, merge params that match *node_fqn* from ``/**`` → wildcard → exact."""
    merged: "dict[str, Any]" = {}
    for path in yaml_paths:
        try:
            with open(path) as f:
                doc = yaml.safe_load(f)
        except FileNotFoundError:
            logger.warning("param file not found: %s", path)
            continue
        except yaml.YAMLError as e:
            logger.warning("param file %s parse error: %s", path, e)
            continue

        if not isinstance(doc, Mapping):
            continue

        for key in _match_order(node_fqn, doc.keys()):
            section = doc.get(key, {})
            if not isinstance(section, Mapping):
                continue
            ros_params = section.get("ros__parameters", {})
            if not isinstance(ros_params, Mapping):
                continue
            for k, v in _walk(ros_params, prefix=""):
                merged[k] = v
    return merged


def _match_order(node_fqn: str, candidate_keys) -> list[str]:
    keys = list(candidate_keys)
    matches: list[tuple] = []

    for key in keys:
        if key == "/**":
            matches.append((0, key))
            continue
        if key == node_fqn:
            matches.append((3, key))
            continue
        if key.endswith("/**"):
            base = key[:-3] or "/"
            if base == "/" or node_fqn.startswith(base.rstrip("/") + "/"):
                matches.append((1 + base.count("/"), key))

    matches.sort(key=lambda t: t[0])
    return [k for _, k in matches]


def _walk(node, prefix: str):
    """Yield (dotted_key, leaf_value) pairs; nested dicts become dotted keys."""
    if isinstance(node, Mapping):
        for key, val in node.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk(val, child_prefix)
    else:
        yield prefix, node


def to_parameter_msgs(values: "Mapping[str, Any]") -> "list":
    """Convert flat dict to ``rcl_interfaces/Parameter[]``; ROS imports deferred for testability."""
    from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue

    out = []
    for name, value in values.items():
        param = Parameter()
        param.name = name
        param.value = _to_parameter_value(value, ParameterValue, ParameterType)
        out.append(param)
    return out


def _to_parameter_value(value, ParameterValue, ParameterType):
    pv = ParameterValue()
    if value is None:
        pv.type = ParameterType.PARAMETER_NOT_SET
        return pv
    if isinstance(value, bool):
        pv.type = ParameterType.PARAMETER_BOOL
        pv.bool_value = bool(value)
        return pv
    if isinstance(value, int):
        pv.type = ParameterType.PARAMETER_INTEGER
        pv.integer_value = int(value)
        return pv
    if isinstance(value, float):
        pv.type = ParameterType.PARAMETER_DOUBLE
        pv.double_value = float(value)
        return pv
    if isinstance(value, str):
        pv.type = ParameterType.PARAMETER_STRING
        pv.string_value = value
        return pv
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return _seq_to_param_value(list(value), pv, ParameterType)
    pv.type = ParameterType.PARAMETER_STRING
    pv.string_value = str(value)
    return pv


def _seq_to_param_value(items: list, pv, ParameterType):
    if not items:
        pv.type = ParameterType.PARAMETER_STRING_ARRAY
        pv.string_array_value = []
        return pv

    if all(isinstance(x, bool) for x in items):
        pv.type = ParameterType.PARAMETER_BOOL_ARRAY
        pv.bool_array_value = [bool(x) for x in items]
    elif all(isinstance(x, int) and not isinstance(x, bool) for x in items):
        pv.type = ParameterType.PARAMETER_INTEGER_ARRAY
        pv.integer_array_value = [int(x) for x in items]
    elif all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in items):
        pv.type = ParameterType.PARAMETER_DOUBLE_ARRAY
        pv.double_array_value = [float(x) for x in items]
    elif all(isinstance(x, str) for x in items):
        pv.type = ParameterType.PARAMETER_STRING_ARRAY
        pv.string_array_value = list(items)
    else:
        pv.type = ParameterType.PARAMETER_STRING_ARRAY
        pv.string_array_value = [str(x) for x in items]
    return pv


# ---- Spec dataclass ---------------------------------------------------------


@dataclass
class ComposableSpec:
    """Static description of a composable node load request."""

    name: str  # unique identifier (use FQN: namespace + "/" + node_name)
    package: str
    plugin: str
    node_name: str
    namespace: str
    target_container_fqn: str
    remap_rules: Sequence[tuple[str, str]] = field(default_factory=list)
    parameter_files: Sequence[str] = field(default_factory=list)
    inline_parameters: Mapping[str, Any] = field(default_factory=dict)
    extra_arguments: Mapping[str, Any] = field(default_factory=dict)
    log_level: Optional[int] = None


def composable_spec(spec: Mapping, extra_param_files: Optional[list[str]] = None) -> ComposableSpec:
    launcher = spec["launcher"]
    inline = params_dict(spec.get("parameters", []))
    extra = {"use_intra_process_comms": True} if launcher.get("use_intra_process_comms") else {}
    ns = parent_namespace(spec.get("namespace"), spec.get("name"))
    return ComposableSpec(
        name=unique_node_name(spec),
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


# ---- Actor ------------------------------------------------------------------


class ComposableNodeActor:
    """Loads one composable node into its container (single-shot, no retry)."""

    def __init__(
        self,
        spec: ComposableSpec,
        *,
        ros_worker: "RosWorker",
        container_ready: "asyncio.Future[int]",
        state_tx: asyncio.Queue,
        shutdown: asyncio.Event,
        service_wait_timeout: float = 30.0,
        load_call_timeout: float = 30.0,
    ) -> None:
        self._spec = spec
        self._ros_worker = ros_worker
        self._container_ready = container_ready
        self._state_tx = state_tx
        self._shutdown = shutdown
        self._service_wait_timeout = service_wait_timeout
        self._load_call_timeout = load_call_timeout
        self._state: Any = ComposableBlocked(reason=BlockReason.NOT_STARTED)

    @property
    def name(self) -> str:
        return self._spec.name

    @property
    def state(self):
        return self._state

    async def run(self) -> None:
        try:
            await self._wait_for_container()
            if self._shutdown.is_set():
                self._state = ComposableBlocked(reason=BlockReason.SHUTDOWN)
                return

            self._state = ComposableUnloaded()
            await self._emit(ev.LoadStarted(name=self.name))
            self._state = ComposableLoading(started_at=time.monotonic())

            load_task = asyncio.ensure_future(self._load())
            shutdown_task = asyncio.create_task(self._shutdown.wait())
            try:
                await asyncio.wait(
                    {load_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                if not shutdown_task.done():
                    shutdown_task.cancel()

            if not load_task.done():
                load_task.cancel()
                try:
                    await load_task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
                self._state = ComposableBlocked(reason=BlockReason.SHUTDOWN)
                return

            try:
                unique_id = load_task.result()
            except Exception as e:  # noqa: BLE001
                msg = f"{type(e).__name__}: {e}"
                logger.warning("[%s] load failed: %s", self.name, msg)
                self._state = ComposableFailed(error=msg)
                await self._emit(ev.LoadFailed(name=self.name, error=msg))
                return

            self._state = ComposableLoaded(unique_id=unique_id)
            await self._emit(
                ev.LoadSucceeded(
                    name=self.name,
                    full_node_name=join_fqn(self._spec.namespace, self._spec.node_name),
                    unique_id=unique_id,
                )
            )
        finally:
            await self._emit(ev.Terminated(name=self.name))

    async def _wait_for_container(self) -> None:
        shutdown_task = asyncio.create_task(self._shutdown.wait())
        try:
            await asyncio.wait(
                {shutdown_task, asyncio.ensure_future(self._container_ready)},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if not shutdown_task.done():
                shutdown_task.cancel()

    async def _load(self) -> int:
        if self._spec.parameter_files:
            target_fqn = join_fqn(self._spec.namespace, self._spec.node_name)
            flat = flatten_for_fqn(self._spec.parameter_files, target_fqn)
        else:
            flat = {}
        flat.update(self._spec.inline_parameters)

        param_msgs = to_parameter_msgs(flat) if flat else []
        extra_msgs = to_parameter_msgs(self._spec.extra_arguments) if self._spec.extra_arguments else []
        remap_strs = [f"{src}:={dst}" for src, dst in self._spec.remap_rules]

        unique_id = await self._ros_worker.load_node(
            container_fqn=self._spec.target_container_fqn,
            package=self._spec.package,
            plugin=self._spec.plugin,
            node_name=self._spec.node_name,
            node_namespace=self._spec.namespace,
            remap_rules=remap_strs,
            parameters=param_msgs,
            extra_arguments=extra_msgs,
            log_level=self._spec.log_level or 0,
            service_wait_timeout=self._service_wait_timeout,
            load_call_timeout=self._load_call_timeout,
        )
        return unique_id

    async def _emit(self, event) -> None:
        await ev.emit_event(self._state_tx, self.name, event)
