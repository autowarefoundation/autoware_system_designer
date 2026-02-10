import copy
import logging
import os
from typing import Any, Dict, List, Optional, Protocol, Tuple

from ..builder.deployment_instance import DeploymentInstance
from ..builder.resolution.variant_resolver import SystemVariantResolver
from ..exceptions import DeploymentError
from ..file_io.source_location import format_source, source_from_config
from ..file_io.system_structure_json import save_system_structure, save_system_structure_snapshot
from ..models.config import SystemConfig

logger = logging.getLogger(__name__)


class _DeploymentLike(Protocol):
	name: str
	mode_keys: List[str]
	system_structure_snapshots: Dict[str, Dict[str, Any]]
	config_registry: Any
	system_structure_dir: str

	def visualize(self) -> None: ...


def apply_mode_configuration(base_system_config: SystemConfig, mode_name: str) -> SystemConfig:
	"""Create a copy of base system and apply mode-specific overrides/removals."""

	modified_config = copy.deepcopy(base_system_config)

	# Filter out components with explicit 'mode' fields from base (deprecated old format)
	# These components should be defined in mode-specific sections instead.
	if modified_config.components:
		filtered_components = []
		for comp in modified_config.components:
			if "mode" in comp:
				logger.debug(
					"Filtering out component '%s' with deprecated 'mode' field from base",
					comp.get("name"),
				)
			else:
				filtered_components.append(comp)
		modified_config.components = filtered_components

	# If mode is "default" or no mode configs exist, return the filtered base.
	if mode_name == "default" or not base_system_config.mode_configs:
		return modified_config

	mode_config = base_system_config.mode_configs.get(mode_name)
	if not mode_config:
		src = source_from_config(base_system_config, "/modes")
		logger.warning(
			"Mode '%s' not found in mode_configs, using base configuration%s",
			mode_name,
			format_source(src),
		)
		return modified_config

	logger.info("Applying mode configuration for mode '%s'", mode_name)

	resolver = SystemVariantResolver()
	resolver.resolve(
		modified_config,
		{
			"override": mode_config.get("override", {}),
			"remove": mode_config.get("remove", {}),
		},
	)
	return modified_config


def _determine_modes(system_config: SystemConfig) -> Tuple[List[str], str]:
	modes_config = system_config.modes or []
	if not modes_config:
		logger.info("Building deployment with single 'default' mode")
		return ["default"], "default"

	mode_names: List[str] = []
	for mode in modes_config:
		name = mode.get("name") if isinstance(mode, dict) else None
		if isinstance(name, str) and name:
			mode_names.append(name)

	# Preserve legacy behavior as much as possible; fallback to default if malformed.
	if not mode_names:
		logger.warning("No valid mode names found; falling back to 'default'")
		return ["default"], "default"

	default_mode = next(
		(
			m.get("name")
			for m in modes_config
			if isinstance(m, dict) and m.get("default") and isinstance(m.get("name"), str)
		),
		mode_names[0],
	)
	if not isinstance(default_mode, str) or not default_mode:
		default_mode = mode_names[0]

	logger.info(
		"Building deployment for %d modes: %s, default: %s",
		len(mode_names),
		mode_names,
		default_mode,
	)
	return mode_names, default_mode


def _create_snapshot_callback(
	system_structure_dir: str,
	system_name: str,
	mode_key: str,
	deploy_instance: DeploymentInstance,
	snapshot_store: Dict[str, Any],
):
	def snapshot_callback(step: str, error: Exception | None = None) -> None:
		snapshot_path = os.path.join(system_structure_dir, f"{mode_key}_{step}.json")
		payload = save_system_structure_snapshot(
			snapshot_path, deploy_instance, system_name, mode_key, step, error
		)
		snapshot_store[step] = payload

	return snapshot_callback


def _build_mode_instance(
	deployment: _DeploymentLike,
	mode_name: str,
	mode_system_config: SystemConfig,
	package_paths: Dict[str, str],
	default_mode: str,
) -> Tuple[str, Dict[str, Any]]:
	mode_suffix = f"_{mode_name}" if mode_name else ""
	instance_name = f"{deployment.name}{mode_suffix}"
	deploy_instance = DeploymentInstance(instance_name)

	snapshot_store: Dict[str, Any] = {}
	mode_key = mode_name if mode_name else default_mode

	snapshot_callback = _create_snapshot_callback(
		deployment.system_structure_dir,
		deployment.name,
		mode_key,
		deploy_instance,
		snapshot_store,
	)

	deploy_instance.set_system(
		mode_system_config,
		deployment.config_registry,
		package_paths=package_paths,
		snapshot_callback=snapshot_callback,
	)

	structure_payload = deploy_instance.collect_system_structure(deployment.name, mode_key)
	structure_path = os.path.join(deployment.system_structure_dir, f"{mode_key}.json")
	save_system_structure(structure_path, structure_payload)

	return mode_key, snapshot_store


def build_deployment_modes(
	deployment: _DeploymentLike,
	system_config: SystemConfig,
	package_paths: Dict[str, str],
) -> None:
	"""Build deployment instances for all modes on the given deployment object."""

	mode_names, default_mode = _determine_modes(system_config)

	deployment.mode_keys = []
	for mode_name in mode_names:
		mode_key = mode_name if mode_name else default_mode
		snapshot_store: Dict[str, Any] = {}
		try:
			mode_system_config = apply_mode_configuration(system_config, mode_name)
			mode_key, snapshot_store = _build_mode_instance(
				deployment, mode_name, mode_system_config, package_paths, default_mode
			)
			deployment.mode_keys.append(mode_key)
			deployment.system_structure_snapshots[mode_key] = snapshot_store
			logger.info("Successfully built deployment instance for mode: %s", mode_key)
		except Exception as exc:
			deployment.system_structure_snapshots[mode_key] = snapshot_store

			# Try to visualize the system to show error status.
			deployment.visualize()

			details: List[str] = []
			if mode_key == default_mode:
				details.append("default")
			system_path = getattr(system_config, "file_path", None)
			if system_path:
				details.append(f"system= {system_path} ")
			details_str = f" ({', '.join(details)})" if details else ""

			hint = (
				"Hint: top-level 'connections' apply to all modes; "
				"use '<Mode>.override.connections' or '<Mode>.remove.connections' for mode-specific wiring."
			)

			mismatch_files: Optional[List[str]] = getattr(
				deployment.config_registry, "minor_version_mismatch_files", []
			)
			if mismatch_files:
				from ..builder.config.config_registry import _format_mismatch_hint

				hint += "\n" + _format_mismatch_hint(mismatch_files)

			raise DeploymentError(
				f"Error while building deploy for mode '{mode_key}'{details_str}: {exc}\n{hint}"
			) from exc
