import logging
from fnmatch import fnmatch
from typing import TYPE_CHECKING

from ...exceptions import ValidationError
from ...file_io.source_location import format_source, source_from_config
from ...models.parsing.data_validator import entity_name_decode
from ..config.launch_manager import LaunchManager
from ..parameters.parameter_set_applier import apply_parameter_set
from ..runtime.parameters import ParameterType

if TYPE_CHECKING:
    from ..config.config_registry import ConfigRegistry
    from .instances import Instance

logger = logging.getLogger(__name__)


def set_instances(
    instance: "Instance",
    entity_id: str,
    config_registry: "ConfigRegistry",
) -> None:
    try:
        entity_name, entity_type = entity_name_decode(entity_id)
        if entity_type == "system":
            set_system_instances(instance, config_registry)
        elif entity_type == "module":
            set_module_instances(instance, entity_id, entity_name, config_registry)
        elif entity_type == "node":
            set_node_instances(instance, entity_id, entity_name, config_registry)
    except Exception:
        raise ValidationError(f"Error setting instances for {entity_id}, at {instance.configuration.file_path}")


def set_system_instances(instance: "Instance", config_registry: "ConfigRegistry") -> None:
    """Set instances for system entity type.

    Creates component instances from the system configuration.
    """
    components_to_instantiate = instance.configuration.components

    # First pass: create all component instances
    for cfg_component in components_to_instantiate:
        compute_unit_name = cfg_component.get("compute_unit")
        instance_name = cfg_component.get("name")
        entity_id = cfg_component.get("entity")
        namespace = cfg_component.get("namespace")
        if namespace:
            if isinstance(namespace, str):
                namespace = namespace.split("/") if "/" in namespace else [namespace]
        else:
            namespace = []

        # create instance
        child_instance = _create_child_instance(instance_name, compute_unit_name, namespace, instance)

        try:
            set_instances(child_instance, entity_id, config_registry)
        except Exception:
            # add the instance to the children dict for debugging
            instance.children[instance_name] = child_instance
            raise ValidationError(
                f"Error in setting component instance '{instance_name}', at {instance.configuration.file_path}"
            )

        instance.children[instance_name] = child_instance
        logger.debug(
            f"System instance '{instance.namespace_str}' added component '{instance_name}' (uid={child_instance.unique_id})"
        )

    # Apply system-level parameter sets
    if hasattr(instance.configuration, "parameter_sets") and instance.configuration.parameter_sets:
        parameter_sets_to_apply = instance.configuration.parameter_sets
        # parameter_sets can be a string or a list of strings
        # apply_parameter_set expects the value under "parameter_set" key, which can be either
        count = 1 if isinstance(parameter_sets_to_apply, str) else len(parameter_sets_to_apply)
        logger.info(f"Applying {count} system-level parameter set(s)")

        # Create a dummy component config to reuse apply_parameter_set
        # Note: apply_parameter_set looks for "parameter_set" key (singular), not "parameter_sets"
        dummy_component_config = {"parameter_set": parameter_sets_to_apply}

        # Apply to self (root), disabling namespace check to allow global parameters
        apply_parameter_set(
            instance,
            instance,
            dummy_component_config,
            config_registry,
            check_namespace=False,
            file_parameter_type=ParameterType.MODE_FILE,
            direct_parameter_type=ParameterType.MODE,
        )

    # Second pass: apply parameter sets after all instances are created
    # This ensures that parameter_sets can target nodes across different components
    for cfg_component in components_to_instantiate:
        instance_name = cfg_component.get("name")
        child_instance = instance.children[instance_name]
        apply_parameter_set(instance, child_instance, cfg_component, config_registry)

    # Third pass: set node groups.
    apply_node_groups(instance)

    # all children are initialized
    instance.is_initialized = True


def apply_node_groups(instance: "Instance") -> None:
    """Apply system node group/container configuration to matched node instances.

    Behavior:
      - Supports wildcard patterns (glob) in each node-group ``nodes`` entry.
      - For plain paths (no glob), treats them as path prefixes so all nodes under
        that path are registered.
      - Missing/invalid entries are warned and skipped (no hard failure).
    """
    node_groups = getattr(instance.configuration, "node_groups", None)
    if not node_groups:
        return

    all_node_instances = list(_iter_node_instances(instance))

    for group in node_groups:
        if not isinstance(group, dict):
            logger.warning("Skipping invalid node group entry (expected mapping): %r", group)
            continue

        group_name = group.get("name")
        group_type = group.get("type")
        node_patterns = group.get("nodes")

        if not group_name or not isinstance(group_name, str):
            logger.warning("Skipping node group with invalid name: %r", group)
            continue
        if not isinstance(node_patterns, list):
            logger.warning("Node group '%s' has invalid 'nodes' field (expected list), skipping", group_name)
            continue

        logger.info("Applying node group '%s' (type=%s)", group_name, group_type)

        matched_nodes = []
        matched_ids = set()

        for pattern in node_patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                logger.warning("Node group '%s' contains invalid node pattern: %r", group_name, pattern)
                continue

            for node_instance in all_node_instances:
                if node_instance.unique_id in matched_ids:
                    continue

                if _node_group_pattern_matches(pattern, node_instance.namespace_str):
                    matched_nodes.append(node_instance)
                    matched_ids.add(node_instance.unique_id)

        if not matched_nodes:
            logger.warning("Node group '%s' matched no nodes; continuing", group_name)
            continue

        for node_instance in matched_nodes:
            if not node_instance.launch_manager:
                logger.warning(
                    "Node group '%s' matched node '%s' without launch manager; skipping",
                    group_name,
                    node_instance.namespace_str,
                )
                continue

            previous_target = node_instance.launch_manager.launch_config.container_target
            if previous_target and previous_target != group_name:
                logger.warning(
                    "Node '%s' container target reassigned from '%s' to '%s'",
                    node_instance.namespace_str,
                    previous_target,
                    group_name,
                )

            node_instance.launch_manager.update(container_target=group_name)


def _iter_node_instances(instance: "Instance"):
    if instance.entity_type == "node":
        yield instance

    for child in instance.children.values():
        yield from _iter_node_instances(child)


def _normalize_node_group_path(raw_path: str) -> str:
    path = raw_path.strip()
    if not path.startswith("/"):
        path = f"/{path}"

    # collapse repeated separators
    while "//" in path:
        path = path.replace("//", "/")

    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return path


def _node_group_pattern_matches(pattern: str, node_namespace: str) -> bool:
    normalized_pattern = _normalize_node_group_path(pattern)
    normalized_node = _normalize_node_group_path(node_namespace)

    # glob-style pattern (supports broad wildcard matching)
    if any(ch in normalized_pattern for ch in ["*", "?", "["]):
        return fnmatch(normalized_node, normalized_pattern)

    # plain path: treat as prefix to include all nodes under the path
    if normalized_pattern == "/":
        return True
    return normalized_node == normalized_pattern or normalized_node.startswith(f"{normalized_pattern}/")


def set_module_instances(
    instance: "Instance",
    entity_id: str,
    entity_name: str,
    config_registry: "ConfigRegistry",
) -> None:
    """Set instances for module entity type."""
    logger.info(f"Setting module entity {entity_id} for instance {instance.namespace_str}")
    instance.configuration = config_registry.get_module(entity_name)
    instance.entity_type = "module"

    # check if the module is already set
    if entity_id in instance.parent_module_list:
        raise ValidationError(f"Config is already set: {entity_id}, avoid circular reference")
    instance.parent_module_list.append(entity_id)

    # set children
    create_module_children(instance, config_registry)

    # run the module configuration
    run_module_configuration(instance)

    # recursive call is finished
    instance.is_initialized = True


def set_node_instances(
    instance: "Instance",
    entity_id: str,
    entity_name: str,
    config_registry: "ConfigRegistry",
) -> None:
    """Set instances for node entity type."""
    logger.info(f"Setting node entity {entity_id} for instance {instance.namespace_str}")
    instance.configuration = config_registry.get_node(entity_name)
    instance.entity_type = "node"
    instance.launch_manager = LaunchManager.from_config(instance.configuration)

    # run the node configuration
    run_node_configuration(instance, config_registry)

    # recursive call is finished
    instance.is_initialized = True


def create_module_children(instance: "Instance", config_registry: "ConfigRegistry") -> None:
    """Create child instances for module entities."""
    cfg_node_list = instance.configuration.instances
    for idx, cfg_node in enumerate(cfg_node_list):
        # check if cfg_node has 'name' and 'entity'
        if "name" not in cfg_node or "entity" not in cfg_node:
            raise ValidationError(
                f"Module instance configuration must have 'name' and 'entity' fields, at {instance.configuration.file_path}"
            )

        child_name = cfg_node.get("name")
        child_instance = _create_child_instance(
            child_name,
            instance.compute_unit,
            instance.namespace + [child_name],
            instance,
            layer_delta=1,
        )
        child_instance.parent_module_list = instance.parent_module_list.copy()

        # recursive call of set_instances
        try:
            set_instances(
                child_instance,
                cfg_node.get("entity"),
                config_registry,
            )
        except Exception as e:
            # add the instance to the children dict for debugging
            instance.children[child_instance.name] = child_instance
            raise ValidationError(
                f"Error in setting child instance {child_instance.name} : {e}, at {instance.configuration.file_path}"
            )
        instance.children[child_instance.name] = child_instance


def run_module_configuration(instance: "Instance") -> None:
    if instance.entity_type != "module":
        raise ValidationError(
            f"run_module_configuration is only supported for module, at {instance.configuration.file_path}"
        )

    # set connections
    if len(instance.configuration.connections) == 0:
        cfg_src = source_from_config(instance.configuration, "/connections")
        logger.warning(f"Module '{instance.name}' has no connections configured{format_source(cfg_src)}")
        return

    # set links first to know topic type for external ports
    instance.link_manager.set_links()

    # log module configuration
    instance.link_manager.log_module_configuration()


def run_node_configuration(instance: "Instance", config_registry: "ConfigRegistry") -> None:
    if instance.entity_type != "node":
        raise ValidationError(
            f"run_node_configuration is only supported for node, at {instance.configuration.file_path}"
        )

    # set ports
    instance.link_manager.initialize_node_ports()

    # Initialize node parameters
    instance.parameter_manager.initialize_node_parameters(config_registry)

    # initialize processes and events
    instance.event_manager.initialize_node_processes()


def _create_child_instance(
    name: str,
    compute_unit: str,
    namespace: list[str],
    parent_instance: "Instance",
    layer_delta: int = 0,
) -> "Instance":
    from .instances import Instance

    child_instance = Instance(name, compute_unit, namespace, parent_instance.layer + layer_delta)
    child_instance.parent = parent_instance
    # parameter resolver propagation
    if parent_instance.parameter_resolver:
        child_instance.set_parameter_resolver(parent_instance.parameter_resolver)

    return child_instance
