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

"""Apply system ``data:`` instances onto consumer node parameter overrides.

A system declares ``data:`` instances binding a ``*.data`` artifact bundle
(``entity``) to a set of ``consumers:`` node globs, with a requested ``variant``
and a ``root`` directory.  Every bundle carries its own manifest (the entity's
``manifest: {file, version_key}``), the sole source of the bundle's contents.
Each consumer node declares ``required_data`` entries (one per bundle) with its
compatibility contract — ``requires_version`` against the recorded version,
``requires_paths`` naming the manifest keys whose files it will open — and a
``binding`` mapping its own parameter names to bundle references:

* ``bundle:path``           -> ``<bundle_dir>`` itself
* ``bundle:variant.<axis>`` -> the resolved value of one variant axis
* ``bundle:version``        -> the version string the bundle records

Bundle directories are resolved at build time (``latest`` included) and the
resolved references are injected as OVERRIDE parameters on the matched node
instances, mirroring :func:`apply_parameter_set`; the node reads the manifest
itself at launch.  Every ``required_data`` entry in the deployment must be
satisfied exactly once; an unresolvable variant, an unsatisfied
``requires_version``, a ``requires_paths`` key absent from the manifest or
naming a missing file, a doubly-bound entry and an entry left unbound are all
fatal.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from autoware_system_designer.builder.instances.node_groups import iter_node_instances
from autoware_system_designer.builder.resolution.bundle_version import (
    BundleVersion,
    BundleVersionError,
    VersionRequirement,
    read_bundle_version,
    read_manifest_paths,
)
from autoware_system_designer.builder.resolution.data_variant_scanner import DataVariantError, DataVariantScanner
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.source_location import format_source, source_from_config
from autoware_system_designer.model.execution import LaunchState
from autoware_system_designer.model.parameters import ParameterType
from autoware_system_designer.parser.config_validator import entity_name_decode

if TYPE_CHECKING:
    from autoware_system_designer.builder.config.config_registry import ConfigRegistry
    from autoware_system_designer.builder.instances.instances import Instance
    from autoware_system_designer.model.config import DataConfig

logger = logging.getLogger(__name__)

_BUNDLE_KEYS = ("path", "variant.<axis>", "version")


@dataclass(frozen=True)
class _ResolvedBundle:
    """One system ``data:`` instance resolved against the filesystem."""

    config: "DataConfig"
    directory: str  # absolute bundle directory
    variant: Dict[str, Any]  # axis name -> concrete value, one per declared axis
    version: Optional[BundleVersion] = None  # None when the bundle records no version
    version_source: Optional[str] = None  # "<file>:<key path>" the version was read from


def _variant_request(variant: Any, resolver, source) -> Dict[str, Any]:
    """Flatten a system ``variant:`` list of single-key maps into ``{axis: value}``.

    Values pass through the parameter resolver, so any substitution valid in a
    system file (``$(var)``, ``$(env)``, ``$(eval)``, ...) is valid here.
    """
    request: Dict[str, Any] = {}
    for entry in variant or []:
        if not isinstance(entry, dict):
            continue
        for axis, value in entry.items():
            if axis in request:
                raise ValidationError(f"Variant axis '{axis}' is requested more than once")
            if resolver and isinstance(value, str):
                value = resolver.resolve_string(value, source=source)
            request[axis] = value
    return request


def _resolve_bundle_key(bundle: _ResolvedBundle, key: str, node_name: str) -> Tuple[Any, Optional[str]]:
    """Resolve the ``bundle:`` namespace: the directory itself or one resolved axis value."""
    if key == "path":
        return bundle.directory, "string"
    if key.startswith("variant."):
        axis = key[len("variant.") :]
        if axis not in bundle.variant:
            raise ValidationError(
                f"Data binding on node '{node_name}' references unknown variant axis '{axis}' "
                f"of entity '{bundle.config.name}' (declared axes: {sorted(bundle.variant)})"
            )
        return bundle.variant[axis], None
    if key == "version":
        if bundle.version is None:
            raise ValidationError(
                f"Data binding on node '{node_name}' references bundle:version, but the bundle "
                f"'{bundle.directory}' of entity '{bundle.config.name}' records no version"
            )
        return bundle.version.raw, "string"
    raise ValidationError(
        f"Data binding on node '{node_name}' references unknown bundle key '{key}' "
        f"(expected one of {list(_BUNDLE_KEYS)})"
    )


def _resolve_reference(
    ref: Any,
    bundle: _ResolvedBundle,
    node_name: str,
) -> Tuple[Any, Optional[str]]:
    """Resolve a ``binding`` ``from:`` reference to ``(value, type)``.

    ``type`` is ``string`` for bundle paths and versions; a variant axis value
    passes through untyped.
    """
    if not isinstance(ref, str) or not ref.startswith("bundle:"):
        raise ValidationError(f"Invalid data binding reference '{ref}' on node '{node_name}' (expected 'bundle:<key>')")
    return _resolve_bundle_key(bundle, ref.partition(":")[2], node_name)


def _required_data_entries(node: "Instance") -> List[Dict[str, Any]]:
    """``required_data`` entries declared on ``node``."""
    entries = getattr(node.configuration, "required_data", None) or []
    return [entry for entry in entries if isinstance(entry, dict)]


def _entry_entity_name(entry: Dict[str, Any]) -> Optional[str]:
    entity = entry.get("entity")
    return entity_name_decode(entity)[0] if entity else None


def _check_data_type(entry: Dict[str, Any], data_cfg: "DataConfig", node_path: str) -> None:
    """Enforce that the entry's ``type``, when declared, matches the entity's ``category``."""
    declared = entry.get("type")
    if declared is not None and declared != data_cfg.category:
        raise ValidationError(
            f"required_data entry for '{entry.get('entity')}' on node '{node_path}' "
            f"declares type '{declared}' but the data entity has category '{data_cfg.category}'"
        )


def _is_wrapper_launched(node: "Instance") -> bool:
    launch_manager = getattr(node, "launch_manager", None)
    launch_config = getattr(launch_manager, "launch_config", None)
    return getattr(launch_config, "launch_state", None) == LaunchState.ROS2_LAUNCH_FILE


def _check_bindable_name(node: "Instance", param_name: Any, data_cfg: "DataConfig") -> None:
    """A wrapper launch receives overrides as ``<arg>``s, which cannot carry a dotted parameter name."""
    if isinstance(param_name, str) and "." in param_name and _is_wrapper_launched(node):
        raise ValidationError(
            f"Data binding '{data_cfg.name}' on node '{node.path}' targets parameter '{param_name}', but the "
            f"node is launched through a ros2_launch_file, which only accepts flat launch arguments; "
            f"bind a flat name or launch the node directly (plugin/executable)"
        )


def _check_required_version(entry: Dict[str, Any], bundle: _ResolvedBundle, instance_name: str, node_path: str) -> None:
    """Enforce the entry's ``requires_version`` against the version the bundle records."""
    spec = entry.get("requires_version")
    if spec is None:
        return
    try:
        requirement = VersionRequirement.from_config(spec)
    except BundleVersionError as e:
        raise ValidationError(f"required_data entry for '{entry.get('entity')}' on node '{node_path}': {e}")

    context = (
        f"node '{node_path}' requires data entity '{bundle.config.name}' ({requirement.describe()}) "
        f"via data instance '{instance_name}', resolved to '{bundle.directory}' "
        f"by variant {bundle.variant}"
    )
    if bundle.version is None:
        raise ValidationError(f"{context}; the bundle records no version to check against")
    if not requirement.satisfied_by(bundle.version):
        raise ValidationError(
            f"{context}; the bundle records version '{bundle.version}' " f"(read from {bundle.version_source})"
        )


def _check_required_paths(entry: Dict[str, Any], bundle: _ResolvedBundle, instance_name: str, node_path: str) -> None:
    """Enforce the entry's ``requires_paths``: each manifest key must name a file present in the bundle."""
    path_keys = entry.get("requires_paths")
    if not path_keys:
        return
    try:
        read_manifest_paths(bundle.directory, bundle.config.manifest, path_keys)
    except BundleVersionError as e:
        raise ValidationError(
            f"node '{node_path}' requires manifest paths {path_keys} of data entity "
            f"'{bundle.config.name}' via data instance '{instance_name}', resolved to "
            f"'{bundle.directory}': {e}"
        )


def _apply_entry_to_node(
    node: "Instance",
    entry: Dict[str, Any],
    bundle: _ResolvedBundle,
    config_registry: "ConfigRegistry",
    source,
) -> None:
    """Resolve one ``required_data`` entry's binding and inject overrides."""
    binding = entry.get("binding") or {}
    data_cfg = bundle.config

    # param_values bindings -> direct OVERRIDE parameters
    param_values: List[Dict[str, Any]] = []
    for item in binding.get("param_values") or []:
        if not isinstance(item, dict):
            continue
        _check_bindable_name(node, item.get("name"), data_cfg)
        value, value_type = _resolve_reference(item.get("from"), bundle, node.path)
        param = {"name": item.get("name"), "value": value}
        if value_type:
            param["type"] = value_type
        param_values.append(param)

    if not param_values:
        return

    node.parameter_manager.apply_node_parameters(
        node.path,
        [],
        param_values,
        config_registry,
        file_parameter_type=ParameterType.OVERRIDE_FILE,
        direct_parameter_type=ParameterType.OVERRIDE,
        source=source,
    )
    logger.info(f"Applied data binding '{data_cfg.name}' to node '{node.path}' (values={len(param_values)})")


def _check_all_required_data_satisfied(
    deployment_instance: "Instance",
    satisfied: Dict[str, Set[str]],
) -> None:
    """Every ``required_data`` entry on every node must have received its binding."""
    unmet: List[str] = []
    for node in iter_node_instances(deployment_instance):
        for entry in _required_data_entries(node):
            if _entry_entity_name(entry) not in satisfied.get(node.path, set()):
                unmet.append(f"node '{node.path}' requires data entity '{entry.get('entity')}'")
    if unmet:
        raise ValidationError(
            "Unsatisfied required_data (no system 'data:' instance covers these nodes): " + "; ".join(unmet)
        )


def _resolve_bundle(
    data_inst: Dict[str, Any],
    instance_name: str,
    config_registry: "ConfigRegistry",
    resolver,
    source,
) -> _ResolvedBundle:
    """Resolve one ``data:`` instance against the filesystem."""
    try:
        entity_id = data_inst.get("entity")
        entity_name, entity_type = entity_name_decode(entity_id)
        if entity_type != "data":
            raise ValidationError(
                f"Data instance '{instance_name}' references non-data entity " f"'{entity_id}'{format_source(source)}"
            )

        data_cfg = _get_data_entity(config_registry, entity_name, instance_name, source)

        # Resolve the bundle root (e.g. $(var data_path)/ml_models).
        root = data_inst.get("root")
        if root is None:
            raise ValidationError(f"Data instance '{instance_name}' has no 'root'{format_source(source)}")
        if resolver and isinstance(root, str):
            root = resolver.resolve_string(root, source=source)

        requested = _variant_request(data_inst.get("variant"), resolver, source)
        scanner = DataVariantScanner(root, data_cfg.variants, data_cfg.path_pattern)
        resolved = scanner.resolve(requested)
        version, version_source = _read_version(data_cfg, resolved.path, instance_name, source)
    except DataVariantError as e:
        raise ValidationError(
            f"Data instance '{instance_name}': could not resolve variant ({e}){format_source(source)}"
        )
    except ValidationError:
        raise
    except Exception as e:
        raise ValidationError(f"Error resolving data instance '{instance_name}': {e}{format_source(source)}")

    return _ResolvedBundle(
        config=data_cfg,
        directory=str(resolved.path),
        variant=dict(resolved.values),
        version=version,
        version_source=version_source,
    )


def _get_data_entity(config_registry: "ConfigRegistry", entity_name: str, instance_name: str, source) -> "DataConfig":
    """Look up a data entity; a miss names where the file is expected and how the manifest is refreshed."""
    try:
        return config_registry.get_data(entity_name)
    except ValidationError as e:
        raise ValidationError(
            f"Data instance '{instance_name}': {e}. A data entity is declared in "
            f"<pkg>/design/data/{entity_name}.data.yaml; the manifest snapshot only sees a new "
            f"file after 'colcon build --packages-select autoware_system_designer'{format_source(source)}"
        )


def _read_version(
    data_cfg: "DataConfig", bundle_dir, instance_name: str, source
) -> Tuple[Optional[BundleVersion], Optional[str]]:
    """The version a bundle records; ``(None, None)`` when the entity is silent and the default file is absent."""
    spec = data_cfg.manifest
    try:
        return read_bundle_version(bundle_dir, spec)
    except BundleVersionError as e:
        if spec is None and "is missing from bundle" in str(e):
            return None, None
        raise ValidationError(
            f"Data instance '{instance_name}': could not read the bundle version of entity "
            f"'{data_cfg.name}' ({e}){format_source(source)}"
        )


def apply_data_bindings(
    deployment_instance: "Instance",
    config_registry: "ConfigRegistry",
) -> None:
    """Resolve system ``data:`` instances and inject their bindings into consumer nodes.

    For each ``data:`` instance the bundle directory is resolved from ``root`` +
    requested ``variant`` via :class:`DataVariantScanner` and baked into the build
    output.  Resolution failures, type/category mismatches, unsatisfied
    ``requires_version``/``requires_paths`` contracts, binding reference errors, an
    entity bound twice onto one node, and ``required_data`` entries left unbound
    after all instances are applied are fatal.
    """
    system_config = deployment_instance.configuration
    data_instances = getattr(system_config, "data", None) or []

    resolver = deployment_instance.parameter_resolver

    # node path -> data entity names bound onto that node
    satisfied: Dict[str, Set[str]] = {}

    for idx, data_inst in enumerate(data_instances):
        if not isinstance(data_inst, dict):
            continue

        instance_name = data_inst.get("name", f"data[{idx}]")
        source = source_from_config(system_config, f"/data/{idx}")

        bundle = _resolve_bundle(data_inst, instance_name, config_registry, resolver, source)
        entity_name = bundle.config.name

        for consumer_glob in data_inst.get("consumers") or []:
            target_nodes = deployment_instance.parameter_manager.find_matching_nodes(consumer_glob)
            if not target_nodes:
                logger.warning(
                    f"Data instance '{instance_name}': consumer '{consumer_glob}' "
                    f"matched no nodes{format_source(source)}"
                )
                continue
            for node in target_nodes:
                for entry in _required_data_entries(node):
                    if _entry_entity_name(entry) != entity_name:
                        continue
                    bound = satisfied.setdefault(node.path, set())
                    if entity_name in bound:
                        raise ValidationError(
                            f"Data entity '{entity_name}' is bound onto node '{node.path}' more than "
                            f"once; data instance '{instance_name}' overlaps an earlier "
                            f"instance{format_source(source)}"
                        )
                    _check_data_type(entry, bundle.config, node.path)
                    _check_required_version(entry, bundle, instance_name, node.path)
                    _check_required_paths(entry, bundle, instance_name, node.path)
                    _apply_entry_to_node(node, entry, bundle, config_registry, source)
                    requirement = entry.get("requires_version")
                    node.data_bindings.append(
                        {
                            "instance": instance_name,
                            "entity": entity_name,
                            "category": bundle.config.category,
                            "variant": dict(bundle.variant),
                            "bundle_dir": bundle.directory,
                            "version": bundle.version.raw if bundle.version else None,
                            "version_source": bundle.version_source,
                            "requires_version": (
                                VersionRequirement.from_config(requirement).describe() if requirement else None
                            ),
                            "requires_paths": list(entry.get("requires_paths") or []) or None,
                        }
                    )
                    bound.add(entity_name)

    _check_all_required_data_satisfied(deployment_instance, satisfied)
