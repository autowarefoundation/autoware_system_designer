import logging
from typing import Callable, Dict

from ...exceptions import ValidationError
from ..parameters.parameter_resolver import ParameterResolver
from .instance_tree import set_instances

logger = logging.getLogger(__name__)


def set_system(
    instance,
    system_config,
    config_registry,
    package_paths: Dict[str, str] = {},
    snapshot_callback: Callable[[str, Exception | None], None] | None = None,
) -> None:
    """Set system for a deployment instance."""
    def _snapshot(step: str, error: Exception | None = None) -> None:
        if snapshot_callback:
            snapshot_callback(step, error)

    current_step = "parse"
    try:
        instance.parameter_resolver = ParameterResolver(
            variables=[], package_paths=package_paths
        )
        logger.info(f"Setting system {system_config.full_name} for instance {instance.name}")
        instance.configuration = system_config
        instance.entity_type = "system"

        # Apply system variables and variable files to the parameter resolver if available
        if instance.parameter_resolver:
            if hasattr(system_config, "variables") and system_config.variables:
                instance.parameter_resolver.load_system_variables(system_config.variables)

            if hasattr(system_config, "variable_files") and system_config.variable_files:
                instance.parameter_resolver.load_system_variable_files(
                    system_config.variable_files
                )

        # 1. set component instances
        logger.info(f"Instance '{instance.name}': setting component instances")
        set_instances(instance, system_config.full_name, config_registry)
        _snapshot("1_parse")

        # Propagate parameter resolver to all instances in the tree (now that they exist)
        instance.set_parameter_resolver(instance.parameter_resolver)

        # 2. set connections
        current_step = "connections"
        logger.info(f"Instance '{instance.name}': setting connections")
        instance.link_manager.set_links()
        instance.check_ports()
        _snapshot("2_connections")

        # 3. build logical topology
        current_step = "events"
        logger.info(f"Instance '{instance.name}': building logical topology")
        instance.set_event_tree()
        _snapshot("3_events")

        # 4. validate node namespaces
        current_step = "validate"
        check_duplicate_node_namespaces(instance)

        # 5. finalize parameters (resolve substitutions)
        current_step = "finalize"
        finalize_parameters_recursive(instance)
    except Exception as e:
        _snapshot(current_step, e)
        raise


def check_duplicate_node_namespaces(instance) -> None:
    """Check for duplicate node namespaces in the entire system."""
    namespace_map = {}
    root_namespaces = {"", "/"}

    def _collect_namespaces(inst):
        if inst.entity_type == "node":
            if inst.namespace_str in root_namespaces:
                pass
            elif inst.namespace_str in namespace_map:
                raise ValidationError(
                    f"Duplicate node namespace found: '{inst.namespace_str}'. "
                    f"Conflict between instance '{inst.name}' and '{namespace_map[inst.namespace_str]}'"
                )
            namespace_map[inst.namespace_str] = inst.name

        for child in inst.children.values():
            _collect_namespaces(child)

    _collect_namespaces(instance)


def finalize_parameters_recursive(instance) -> None:
    """Recursively finalize all parameters in the instance tree."""
    # If this is a node, resolve all its parameters
    if instance.entity_type == "node" and hasattr(instance, "parameter_manager"):
        instance.parameter_manager.resolve_all_parameters()

    # Recursively process children
    for child in instance.children.values():
        finalize_parameters_recursive(child)
