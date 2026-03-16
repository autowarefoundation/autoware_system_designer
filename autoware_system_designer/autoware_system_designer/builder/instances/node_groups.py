import logging
from fnmatch import fnmatch
from typing import TYPE_CHECKING

if TYPE_CHECKING:
	from .instances import Instance

logger = logging.getLogger(__name__)


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

				if _node_group_pattern_matches(pattern, node_instance.node_path):
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
	return normalized_node_path == normalized_pattern or normalized_node_path.startswith(
		f"{normalized_pattern}/"
	)
