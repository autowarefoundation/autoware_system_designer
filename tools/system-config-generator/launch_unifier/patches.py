# Copyright 2026 TIER IV, Inc.
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

"""
Monkey-patches for unmodified launch_ros to expose resolved node attributes.

Replicates the FinalAttributes instrumentation from the modified launch_ros fork
(commit 70da87a) without requiring any changes to the installed launch_ros package.

Apply once at startup, before any launch entities are created:

    from launch_unifier.patches import apply_patches
    apply_patches()
"""

import os
import pathlib

_APPLIED = False


def apply_patches():
    global _APPLIED
    if _APPLIED:
        return
    _APPLIED = True
    _patch_node()
    _patch_composable_node()
    _patch_load_composable_nodes()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_context_lists(context, keys):
    """Convert any generator stored under the given context keys to a list.

    launch_ros sometimes stores generators in launch_configurations (e.g.
    'ros_remaps' from SetRemap).  A generator can only be iterated once, so
    we materialise it as a list before the first consumer sees it, making the
    value reusable for both the original execute() and our capture code.
    """
    for key in keys:
        val = context.launch_configurations.get(key)
        if val is not None and not isinstance(val, list):
            context.launch_configurations[key] = list(val)


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def _patch_node():
    from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions
    from launch_ros.actions.node import Node
    from launch_ros.descriptions import Parameter
    from launch_ros.utilities import evaluate_parameters

    class FinalAttributes:
        def __init__(self):
            self.package = None
            self.node_name = None
            self.node_executable = None
            self.node_namespace = None
            self.target_container = None
            self.remap_rules = None
            self.remap_rules_global = None
            self.params_files = None
            self.params_dicts = None
            self.params_descs = []  # list avoids append-to-None bug present in original fork
            self.params_global_tuples = None
            self.params_global_files = None
            self.arguments = None

    Node.FinalAttributes = FinalAttributes

    _orig_init = Node.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self.final_attributes = FinalAttributes()

    Node.__init__ = _patched_init

    def _patched_execute(self, context):
        # Materialise generators so both _perform_substitutions and our capture
        # code can iterate the same data.
        _normalize_context_lists(context, ("global_params", "ros_remaps"))

        # Resolve all node attributes without spawning a process.
        # _perform_substitutions sets __expanded_node_name, __expanded_node_namespace,
        # __expanded_remappings, and processes parameters — all we need for analysis.
        self._perform_substitutions(context)
        ret = []

        fa = self.final_attributes

        # --- Global params (SetParameter → context['global_params']) ---
        params_container = context.launch_configurations.get("global_params")
        if params_container is not None:
            for param in params_container:
                if isinstance(param, tuple):
                    if fa.params_global_tuples is None:
                        fa.params_global_tuples = []
                    name, value = param
                    fa.params_global_tuples.append((name, value))
                else:
                    if fa.params_global_files is None:
                        fa.params_global_files = []
                    fa.params_global_files.append(os.path.abspath(param))

        # --- Node-specific parameters ---
        node_parameters = self._Node__parameters
        if node_parameters is not None:
            try:
                for params in evaluate_parameters(context, node_parameters):
                    if isinstance(params, dict):
                        if fa.params_dicts is None:
                            fa.params_dicts = []
                        fa.params_dicts.append(params)
                    elif isinstance(params, pathlib.Path):
                        if fa.params_files is None:
                            fa.params_files = []
                        fa.params_files.append(str(params))
                    elif isinstance(params, Parameter):
                        fa.params_descs.append(params.evaluate(context))
            except Exception:
                pass

        # --- Global remaps (SetRemap → context['ros_remaps']) ---
        global_remaps = context.launch_configurations.get("ros_remaps")
        if global_remaps:
            fa.remap_rules_global = list(global_remaps)

        # --- Local remaps from Node constructor ---
        node_remappings = self._Node__remappings
        if node_remappings:
            fa.remap_rules = [
                (perform_substitutions(context, src), perform_substitutions(context, dst))
                for src, dst in node_remappings
            ]

        # --- Node identity ---
        # Mirrors the exact conditional logic from the fork's diff so that
        # unspecified placeholders become empty strings.
        if self._Node__node_name is not None:
            expanded_name = self._Node__expanded_node_name
            fa.node_name = "" if expanded_name == Node.UNSPECIFIED_NODE_NAME else expanded_name

        expanded_ns = self._Node__expanded_node_namespace
        if expanded_ns != "":
            fa.node_namespace = "" if expanded_ns == Node.UNSPECIFIED_NODE_NAMESPACE else expanded_ns

        fa.package = perform_substitutions(context, normalize_to_list_of_substitutions(self.node_package))
        fa.node_executable = perform_substitutions(context, normalize_to_list_of_substitutions(self.node_executable))

        node_arguments = self._Node__arguments
        if node_arguments is not None:
            fa.arguments = [
                perform_substitutions(context, normalize_to_list_of_substitutions(arg)) for arg in node_arguments
            ]

        return ret

    Node.execute = _patched_execute


# ---------------------------------------------------------------------------
# ComposableNode
# ---------------------------------------------------------------------------


def _patch_composable_node():
    from launch_ros.descriptions.composable_node import ComposableNode

    class FinalAttributes:
        def __init__(self):
            self.package = None
            self.node_name = None
            self.node_plugin = None
            self.node_namespace = None
            self.target_container = None
            self.remap_rules = None
            self.remap_rules_global = None
            self.params_files = None
            self.params_dicts = None
            self.params_descs = []
            self.extra_arguments = None

    ComposableNode.FinalAttributes = FinalAttributes

    _orig_init = ComposableNode.__init__

    def _patched_init(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        self.final_attributes = FinalAttributes()

    ComposableNode.__init__ = _patched_init


# ---------------------------------------------------------------------------
# LoadComposableNodes + get_composable_node_load_request
# ---------------------------------------------------------------------------


def _patch_load_composable_nodes():
    import launch_ros.actions.load_composable_nodes as lcn_module
    from launch.utilities import perform_substitutions
    from launch_ros.actions.load_composable_nodes import LoadComposableNodes
    from launch_ros.descriptions import Parameter
    from launch_ros.parameter_descriptions import ParameterFile
    from launch_ros.utilities import evaluate_parameters
    from launch_ros.utilities.normalize_parameters import normalize_parameter_dict

    # --- Wrap module-level get_composable_node_load_request ---

    _orig_get_request = lcn_module.get_composable_node_load_request

    def _patched_get_request(composable_node_description, context):
        _normalize_context_lists(context, ("global_params", "ros_remaps"))

        request = _orig_get_request(composable_node_description, context)

        fa = composable_node_description.final_attributes

        # Simple string fields are directly available from the built request.
        fa.package = request.package_name
        fa.node_plugin = request.plugin_name
        fa.node_name = request.node_name or None
        fa.node_namespace = request.node_namespace or None

        # --- Global remaps ---
        global_remaps = context.launch_configurations.get("ros_remaps")
        if global_remaps:
            fa.remap_rules_global = global_remaps if isinstance(global_remaps, list) else list(global_remaps)

        # --- Local remaps ---
        if composable_node_description.remappings:
            fa.remap_rules = [
                (perform_substitutions(context, src), perform_substitutions(context, dst))
                for src, dst in composable_node_description.remappings
            ]

        # --- Parameters: rebuild the combined list and evaluate structurally ---
        # This mirrors what get_composable_node_load_request does internally so
        # we get Path / dict / Parameter objects instead of proto messages.
        params_container = context.launch_configurations.get("global_params")
        all_params = []
        if params_container is not None:
            for param in params_container:
                if isinstance(param, tuple):
                    all_params.append(normalize_parameter_dict({param[0]: param[1]}))
                else:
                    all_params.append(ParameterFile(pathlib.Path(param).resolve()))
        if composable_node_description.parameters is not None:
            all_params.extend(list(composable_node_description.parameters))

        if all_params:
            try:
                for params in evaluate_parameters(context, all_params):
                    if isinstance(params, pathlib.Path):
                        if fa.params_files is None:
                            fa.params_files = []
                        fa.params_files.append(str(params))
                    elif isinstance(params, dict):
                        if fa.params_dicts is None:
                            fa.params_dicts = []
                        fa.params_dicts.append(params)
                    elif isinstance(params, Parameter):
                        fa.params_descs.append(params.evaluate(context))
            except Exception:
                pass

        # --- Extra arguments (use_intra_process_comms, etc.) ---
        if composable_node_description.extra_arguments is not None:
            try:
                for params in evaluate_parameters(context, composable_node_description.extra_arguments):
                    if isinstance(params, dict):
                        if fa.extra_arguments is None:
                            fa.extra_arguments = []
                        fa.extra_arguments.append(params)
            except Exception:
                pass

        return request

    lcn_module.get_composable_node_load_request = _patched_get_request

    # --- Wrap LoadComposableNodes.execute to backfill target_container ---
    # target_container is resolved inside execute() and must be set on every
    # composable node's final_attributes after get_composable_node_load_request
    # has already populated the other fields.

    def _patched_lcn_execute(self, context):
        # Resolve target container name — mirrors LoadComposableNodes.execute() logic
        # but skips creating the rclpy client and scheduling _load_in_sequence so that
        # no nodes are actually loaded into a running system.
        from launch.utilities import is_a_subclass
        from launch_ros.actions.composable_node_container import ComposableNodeContainer  # noqa: PLC0415

        target = self._LoadComposableNodes__target_container
        if is_a_subclass(target, ComposableNodeContainer):
            container_name = target.node_name
        else:
            from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions  # noqa: PLC0415

            container_name = perform_substitutions(context, normalize_to_list_of_substitutions(target))

        self._LoadComposableNodes__final_target_container_name = container_name

        # Build load requests via our already-patched module function so that
        # final_attributes are populated on each ComposableNode descriptor.
        for node_desc in self._LoadComposableNodes__composable_node_descriptions:
            try:
                lcn_module.get_composable_node_load_request(node_desc, context)
            except Exception:
                pass
            node_desc.final_attributes.target_container = container_name

        # Return no child actions — do NOT schedule _load_in_sequence.
        return []

    LoadComposableNodes.execute = _patched_lcn_execute
