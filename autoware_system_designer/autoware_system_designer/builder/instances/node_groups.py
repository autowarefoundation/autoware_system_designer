import logging
from fnmatch import fnmatch
from typing import TYPE_CHECKING, Any

from ...exceptions import ValidationError
from ..config.launch_manager import LaunchManager
from ..runtime.execution import LaunchConfig, LaunchState

if TYPE_CHECKING:
    from .instances import Instance

logger = logging.getLogger(__name__)


# Extensible registry: add a new node-group container type here.
NODE_GROUP_CONTAINER_SPECS = {
    "ros2_component_container_mt": {
        "package_name": "rclcpp_components",
        "package_provider": "ros2",
        "launch": {
            "executable": "component_container_mt",
            "type": "node_container",
        },
    },
    "ros2_component_container": {
        "package_name": "rclcpp_components",
        "package_provider": "ros2",
        "launch": {
            "executable": "component_container",
            "type": "node_container",
        },
    },
}


def apply_node_groups(instance: "Instance") -> None:
    """Apply system node group/container configuration to matched node instances.

    Behavior:
      - Supports wildcard patterns (glob) in each node-group ``nodes`` entry.
      - For plain paths (no glob), treats them as path prefixes so all nodes under
            that path are registered.
      - Strictly validates node-group name uniqueness and supported type.
      - Creates container nodes only when at least one node is matched.
    """
    node_groups = getattr(instance.configuration, "node_groups", None)
    if not node_groups:
        return
    if not isinstance(node_groups, list):
        raise ValidationError("'node_groups' must be a list")

    validated_groups = _validate_node_groups(node_groups)
    all_node_instances = list(_iter_node_instances(instance))

    for group in validated_groups:
        group_name = group.get("name")
        node_patterns = group.get("nodes")
        group_type = group.get("type")

        logger.info("Applying node group '%s' (type=%s)", group_name, group_type)

        matched_nodes = []
        matched_ids = set()

        for pattern in node_patterns:
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValidationError(f"Node group '{group_name}' contains invalid node pattern: {pattern!r}")

            for node_instance in all_node_instances:
                if node_instance.unique_id in matched_ids:
                    continue

                if _node_group_pattern_matches(pattern, node_instance.node_path):
                    matched_nodes.append(node_instance)
                    matched_ids.add(node_instance.unique_id)

        if not matched_nodes:
            logger.info("Node group '%s' matched no nodes; skipping container creation", group_name)
            continue

        group_compute_unit = _resolve_group_compute_unit(
            group_name=group_name,
            matched_nodes=matched_nodes,
        )

        _create_group_container_node(
            instance=instance,
            group_name=group_name,
            group_type=group_type,
            compute_unit=group_compute_unit,
        )

        for node_instance in matched_nodes:
            previous_target = node_instance.launch_manager.launch_config.container_target
            if previous_target and previous_target != group_name:
                logger.warning(
                    "Node '%s' container target reassigned from '%s' to '%s'",
                    node_instance.namespace_str,
                    previous_target,
                    group_name,
                )

            node_instance.launch_manager.update(container_target=group_name)


def _resolve_group_compute_unit(
    *,
    group_name: str,
    matched_nodes: list["Instance"],
) -> str:
    compute_units = {node_instance.compute_unit for node_instance in matched_nodes}
    if len(compute_units) > 1:
        sorted_compute_units = sorted(compute_units)
        raise ValidationError(
            f"Node group '{group_name}' matches nodes across multiple compute units: {sorted_compute_units}"
        )

    return next(iter(compute_units))


def _validate_node_groups(node_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    validated_groups: list[dict[str, Any]] = []
    seen_group_names: set[str] = set()

    for idx, group in enumerate(node_groups):
        if not isinstance(group, dict):
            raise ValidationError(f"Node group entry at index {idx} must be a mapping, got: {type(group).__name__}")

        group_name = group.get("name")
        group_type = group.get("type")
        node_patterns = group.get("nodes")

        if not group_name or not isinstance(group_name, str):
            raise ValidationError(f"Node group at index {idx} has invalid 'name': {group_name!r}")
        if group_name in seen_group_names:
            raise ValidationError(f"Duplicate node group name found: '{group_name}'")
        seen_group_names.add(group_name)

        if not isinstance(group_type, str) or not group_type:
            raise ValidationError(f"Node group '{group_name}' has invalid 'type': {group_type!r}")
        if group_type not in NODE_GROUP_CONTAINER_SPECS:
            supported_types = ", ".join(sorted(NODE_GROUP_CONTAINER_SPECS.keys()))
            raise ValidationError(
                f"Node group '{group_name}' uses unsupported type '{group_type}'. Supported types: {supported_types}"
            )

        if not isinstance(node_patterns, list):
            raise ValidationError(f"Node group '{group_name}' has invalid 'nodes' field (expected list)")

        validated_groups.append(group)

    return validated_groups


def _create_group_container_node(
    *,
    instance: "Instance",
    group_name: str,
    group_type: str,
    compute_unit: str,
) -> "Instance":
    if group_name in instance.children:
        raise ValidationError(f"Node group name '{group_name}' conflicts with existing system component name")

    container_instance = _create_container_node_instance(
        parent_instance=instance,
        group_name=group_name,
        group_type=group_type,
        compute_unit=compute_unit,
    )
    instance.children[group_name] = container_instance

    logger.info(
        "Created synthetic container node for group '%s' (type=%s, compute_unit=%s)",
        group_name,
        group_type,
        compute_unit,
    )

    return container_instance


def _create_container_node_instance(
    parent_instance: "Instance", group_name: str, group_type: str, compute_unit: str
) -> "Instance":
    from .instances import Instance

    container_spec = NODE_GROUP_CONTAINER_SPECS[group_type]
    launch_spec = container_spec["launch"]

    container_instance = Instance(
        name=group_name,
        compute_unit=compute_unit,
        namespace=[],
        layer=parent_instance.layer,
    )
    container_instance.parent = parent_instance
    container_instance.entity_type = "node"
    container_instance.launch_manager = LaunchManager(
        launch_config=LaunchConfig(
            package_name=container_spec["package_name"],
            node_output=launch_spec.get("node_output", "both"),
            plugin=launch_spec.get("plugin", ""),
            executable=launch_spec.get("executable", ""),
            launch_state=LaunchState.NODE_CONTAINER,
            launch_type=launch_spec.get("type", "node_container"),
        )
    )

    container_instance.is_initialized = True

    return container_instance


def _iter_node_instances(instance: "Instance"):
    if instance.entity_type == "node":
        yield instance

    for child in instance.children.values():
        yield from _iter_node_instances(child)


def _normalize_node_group_path(raw_path: str) -> str:
    path = raw_path.strip()
    if not path.startswith("/"):
        path = f"/{path}"

    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    return path


def _node_group_pattern_matches(pattern: str, node_path: str) -> bool:
    normalized_pattern = _normalize_node_group_path(pattern)
    normalized_node_path = _normalize_node_group_path(node_path)

    # glob-style pattern (supports broad wildcard matching)
    if any(ch in normalized_pattern for ch in ["*", "?", "["]):
        return fnmatch(normalized_node_path, normalized_pattern)

    # plain path: treat as prefix to include all nodes under the path
    if normalized_pattern == "/":
        return True
    return normalized_node_path == normalized_pattern or normalized_node_path.startswith(f"{normalized_pattern}/")
