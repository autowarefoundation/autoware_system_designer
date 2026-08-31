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

"""Deployment pipeline orchestrator.

``DeploymentBuilder.build`` parses the workspace, builds every mode, exports
the system-structure JSON, and returns :class:`BuildArtifacts` — the complete
input of every generator. Generators are module functions over artifacts and
the exported JSON; the artifacts manifest (``deployment.json``) makes a
finished export self-describing, so artifacts can be reloaded without a build.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from autoware_system_designer.builder.config.config_registry import ConfigRegistry, format_duplicate_report
from autoware_system_designer.builder.deployment_instance import DeploymentInstance
from autoware_system_designer.builder.export.instance_to_json import collect_system_structure
from autoware_system_designer.builder.export.json_io import (
    iter_mode_data,
    save_system_structure,
    save_system_structure_snapshot,
)
from autoware_system_designer.builder.modes import apply_mode_configuration, select_modes
from autoware_system_designer.common.deployment_config import DeploymentConfig
from autoware_system_designer.common.exceptions import DeploymentError, ValidationError, annotate_error
from autoware_system_designer.common.export_layout import ExportLayout
from autoware_system_designer.common.path_utils import (
    PACKAGE_MAP_FILENAME,
    WORKSPACE_ROOT_ENV,
    WORKSPACE_ROOT_KEY,
    canonical_path,
    contract_workspace_paths,
    derive_workspace_root,
    expand_workspace_paths,
    has_workspace_token,
    resolve_manifest_path,
)
from autoware_system_designer.common.source_location import SourceLocation, format_source
from autoware_system_designer.common.template_renderer import TemplateRenderer
from autoware_system_designer.generator import build_script_generator
from autoware_system_designer.generator.deploy_launchers import generate_deploy_launchers
from autoware_system_designer.generator.parameter_template_generator import ParameterTemplateGenerator
from autoware_system_designer.generator.ros2_launcher.generate_module_launcher import generate_module_launch_file
from autoware_system_designer.model import serde
from autoware_system_designer.model.config import NodeConfig, SystemConfig
from autoware_system_designer.model.export_schema import SCHEMA_VERSION
from autoware_system_designer.parser.deployment_parser import peek_target_system_name, resolve_input_target
from autoware_system_designer.parser.yaml_parser import yaml_parser
from autoware_system_designer.visualizer.launch_commands_page import generate_launch_commands_page
from autoware_system_designer.visualizer.visualize_deployment import visualize_deployment

logger = logging.getLogger(__name__)

# Artifacts manifest inside the system_structure directory.
ARTIFACTS_FILENAME = "deployment.json"


@dataclass
class BuildArtifacts:
    """Everything a generator needs about one built deployment."""

    layout: ExportLayout
    system_file: str
    deployment_package_path: str
    deployment_package_name: Optional[str]
    argument_names: List[str]
    deploy_variants: List[Dict[str, Any]]
    default_mode: str = "default"
    mode_keys: List[str] = field(default_factory=list)
    file_package_map: Dict[str, str] = field(default_factory=dict)
    package_resolution_by_name: Dict[str, Optional[str]] = field(default_factory=dict)
    packages_without_provider: List[str] = field(default_factory=list)
    # Base for tokenized paths in the export files; kept out of the manifest and
    # re-derived on load so exports stay machine-independent.
    workspace_root: Optional[str] = field(default=None, metadata={"exclude": True})
    # Build-time diagnostics; absent when artifacts are loaded from a manifest.
    config_registry: Any = field(default=None, metadata={"exclude": True})
    system_structure_snapshots: Dict[str, Dict[str, Any]] = field(default_factory=dict, metadata={"exclude": True})

    @property
    def name(self) -> str:
        return self.layout.system_name


def save_artifacts_manifest(artifacts: BuildArtifacts) -> str:
    """Write the artifacts manifest that makes the export self-describing."""
    path = os.path.join(artifacts.layout.system_structure_dir, ARTIFACTS_FILENAME)
    payload = {"schema_version": SCHEMA_VERSION, **serde.dump(artifacts)}
    payload = contract_workspace_paths(payload, artifacts.workspace_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=True)
    return path


def load_build_artifacts(output_root_dir: str, system_name: str) -> BuildArtifacts:
    """Reconstruct artifacts from an exported manifest (no registry, no snapshots)."""
    layout = ExportLayout(output_root_dir, system_name)
    path = os.path.join(layout.system_structure_dir, ARTIFACTS_FILENAME)
    with open(path) as f:
        payload = json.load(f)
    workspace_root = derive_workspace_root(output_root_dir, payload.get("deployment_package_path", ""))
    if workspace_root is None and has_workspace_token(payload):
        raise DeploymentError(
            f"Cannot locate the workspace root for tokenized paths in {path}; "
            f"set {WORKSPACE_ROOT_ENV} to the workspace root directory."
        )
    payload = expand_workspace_paths(payload, workspace_root)
    return BuildArtifacts(
        layout=layout,
        workspace_root=workspace_root,
        system_file=payload["system_file"],
        deployment_package_path=payload["deployment_package_path"],
        deployment_package_name=payload.get("deployment_package_name"),
        argument_names=payload.get("argument_names", []),
        deploy_variants=payload.get("deploy_variants", []),
        default_mode=payload.get("default_mode", "default"),
        mode_keys=payload.get("mode_keys", []),
        file_package_map=payload.get("file_package_map", {}),
        package_resolution_by_name=payload.get("package_resolution_by_name", {}),
        packages_without_provider=payload.get("packages_without_provider", []),
    )


class DeploymentBuilder:
    """Parse the workspace, build every mode, and export the system structure."""

    def __init__(self, deploy_config: DeploymentConfig):
        self.deploy_config = deploy_config
        self.config_registry: Optional[ConfigRegistry] = None
        self._package_paths: Dict[str, str] = {}
        self._workspace_root: Optional[str] = None

    def build(self) -> BuildArtifacts:
        try:
            system_config, deploy_variants, _table_path = self._resolve_target(self.deploy_config)
            artifacts = self._init_artifacts(system_config, deploy_variants)
            self._build_modes(system_config, artifacts)
            self._check_used_duplicates()
            self._collect_package_resolution(artifacts)
            save_artifacts_manifest(artifacts)
            return artifacts
        except Exception as exc:
            # Failure hints read the registry off the exception when the build aborts.
            exc.config_registry = self.config_registry
            raise

    def _resolve_target(self, deploy_config: DeploymentConfig):
        """Load YAML manifests and resolve the target system config."""
        system_yaml_list, package_paths, file_package_map = self._get_system_list(deploy_config)
        self._package_paths = package_paths
        config_registry = ConfigRegistry(
            system_yaml_list,
            package_paths,
            file_package_map,
            workspace_config=deploy_config.workspace_config,
            strict=deploy_config.strict,
            anchor_dir=deploy_config.anchor_dir or self._anchor_from_input(deploy_config.deployment_file),
        )
        self.config_registry = config_registry
        config_registry.deployment_package_name = file_package_map.get(canonical_path(deploy_config.deployment_file))

        logger.info("deployment init Deployment file: %s", deploy_config.deployment_file)

        # Resolve input target (could be deployment file or system-only file)
        input_path = deploy_config.deployment_file

        # Ranking inputs must be final before the first memoizing lookup.
        self._finalize_selection_policy(input_path, config_registry, file_package_map)

        system_config, deploy_variants, deployment_table_path = resolve_input_target(input_path, config_registry)
        if not system_config:
            raise ValidationError(f"System not found from input: {input_path}")

        logger.info(f"Resolved system file path from registry: {system_config.file_path}")
        return system_config, deploy_variants, deployment_table_path

    @staticmethod
    def _finalize_selection_policy(
        input_path: str,
        config_registry: ConfigRegistry,
        file_package_map: Dict[str, str],
    ) -> None:
        """Pin the anchor and preferred package to the target system before the first memoizing lookup."""
        if config_registry.anchor_dir is not None and config_registry.deployment_package_name is not None:
            return
        try:
            system_name = peek_target_system_name(input_path)
        except ValidationError:
            # resolve_input_target raises the canonical error for a broken target.
            return
        group = config_registry.system_group(system_name)
        if group is None:
            # resolve_input_target raises the canonical not-found error.
            return
        picked = group.choose(config_registry.selection_policy)
        picked_path = canonical_path(str(picked.file_path))
        if config_registry.deployment_package_name is None:
            config_registry.deployment_package_name = file_package_map.get(picked_path) or picked.package
        if config_registry.anchor_dir is None:
            if group.is_duplicated:
                logger.warning(
                    f"Deployment target '{system_name}' is a bare entity name with duplicated definitions; "
                    f"the anchor comes from the load-order pick:\n{group.describe()}"
                )
            config_registry.anchor_dir = str(Path(picked_path).parent)

    def _init_artifacts(self, system_config: SystemConfig, deploy_variants: List[Dict[str, Any]]) -> BuildArtifacts:
        layout = ExportLayout(self.deploy_config.output_root_dir, system_config.name)
        return BuildArtifacts(
            layout=layout,
            workspace_root=self._workspace_root,
            system_file=str(system_config.file_path),
            deployment_package_path=str(Path(self.deploy_config.output_root_dir).resolve()),
            deployment_package_name=getattr(self.config_registry, "deployment_package_name", None),
            argument_names=self._collect_system_argument_names(system_config),
            deploy_variants=deploy_variants,
            file_package_map=self.config_registry.file_package_map,
            config_registry=self.config_registry,
        )

    @staticmethod
    def _collect_system_argument_names(system_config: SystemConfig) -> List[str]:
        result: List[str] = []
        seen = set()
        for argument in system_config.arguments or []:
            if not isinstance(argument, dict):
                continue
            name = argument.get("name")
            if not isinstance(name, str) or not name:
                continue
            if name not in seen:
                seen.add(name)
                result.append(name)
        return result

    @staticmethod
    def _anchor_from_input(input_path: str) -> Optional[str]:
        """Directory of the deployment target; None when the target is a bare entity name."""
        if not input_path:
            return None
        path = Path(input_path)
        return str(Path(canonical_path(input_path)).parent) if path.is_file() else None

    @staticmethod
    def _read_workspace_root(manifest_dir: str) -> Optional[str]:
        """Workspace root recorded alongside the package map; None when unrecorded or gone."""
        package_map_file = os.path.join(manifest_dir, PACKAGE_MAP_FILENAME)
        if not os.path.isfile(package_map_file):
            return None
        try:
            recorded = yaml_parser.load_config(package_map_file).get(WORKSPACE_ROOT_KEY)
        except Exception as e:
            logger.warning(f"Failed to read manifest anchor from {package_map_file}: {e}")
            return None
        if not recorded:
            return None
        if not os.path.isdir(recorded):
            logger.warning(
                f"Recorded workspace root '{recorded}' does not exist; "
                f"resolving relative manifest paths against {manifest_dir}"
            )
            return None
        # Canonical form so the export base matches the root re-derived on load.
        return canonical_path(recorded)

    def _get_system_list(self, deploy_config: DeploymentConfig) -> Tuple[List[str], Dict[str, str], Dict[str, str]]:
        system_list: list[str] = []
        package_paths: Dict[str, str] = {}
        file_package_map: Dict[str, str] = {}
        manifest_dir = deploy_config.manifest_dir
        if not os.path.isdir(manifest_dir):
            raise ValidationError(f"System design manifest directory not found or not a directory: {manifest_dir}")

        self._workspace_root = self._read_workspace_root(manifest_dir)
        anchor = self._workspace_root or manifest_dir

        for entry in sorted(os.listdir(manifest_dir)):
            if not entry.endswith(".yaml"):
                continue
            manifest_file = os.path.join(manifest_dir, entry)
            try:
                manifest_yaml = yaml_parser.load_config(manifest_file)

                # Load package map if available
                if "package_map" in manifest_yaml:
                    package_paths.update(
                        {
                            name: resolve_manifest_path(path, anchor)
                            for name, path in manifest_yaml["package_map"].items()
                        }
                    )

                files = manifest_yaml.get("deploy_config_files")
                # Allow the field to be empty or null without raising an error
                if files in (None, []):
                    logger.debug(f"Manifest '{entry}' has empty deploy_config_files; skipping.")
                    continue
                if not isinstance(files, list):
                    manifest_src = SourceLocation(file_path=Path(manifest_file))
                    logger.warning(
                        f"Manifest '{entry}' has unexpected type for deploy_config_files: {type(files)}; skipping.{format_source(manifest_src)}"
                    )
                    continue
                for f in files:
                    file_path = f.get("path") if isinstance(f, dict) else None
                    if file_path:
                        file_path = resolve_manifest_path(file_path, anchor)
                    if file_path and file_path not in system_list:
                        system_list.append(file_path)

                    if file_path and "package_name" in manifest_yaml:
                        file_package_map[file_path] = manifest_yaml["package_name"]

            except Exception as e:
                manifest_src = SourceLocation(file_path=Path(manifest_file))
                logger.warning(f"Failed to load manifest {manifest_file}: {e}{format_source(manifest_src)}")
        if not system_list:
            raise ValidationError("No system design configuration files collected.")
        return system_list, package_paths, file_package_map

    def _create_snapshot_callback(
        self,
        mode_key: str,
        deploy_instance: DeploymentInstance,
        artifacts: BuildArtifacts,
        snapshot_store: Dict[str, Any],
    ):
        """Create callback for saving intermediate snapshots during instance population."""

        def snapshot_callback(step: str, error: Exception | None = None) -> None:
            snapshot_path = os.path.join(artifacts.layout.system_structure_dir, f"{mode_key}_{step}.json")
            payload = save_system_structure_snapshot(
                snapshot_path,
                deploy_instance,
                artifacts.name,
                mode_key,
                step,
                error,
                workspace_root=artifacts.workspace_root,
            )
            snapshot_store[step] = payload

        return snapshot_callback

    def _build_mode_instance(
        self,
        mode_name: str,
        mode_system_config: SystemConfig,
        artifacts: BuildArtifacts,
    ) -> Tuple[str, DeploymentInstance, Dict[str, Any]]:
        """Populate one mode's DeploymentInstance from its SystemConfig."""
        mode_suffix = f"_{mode_name}" if mode_name else ""
        instance_name = f"{artifacts.name}{mode_suffix}"
        deploy_instance = DeploymentInstance(instance_name)

        snapshot_store: Dict[str, Any] = {}
        mode_key = mode_name if mode_name else artifacts.default_mode

        snapshot_callback = self._create_snapshot_callback(mode_key, deploy_instance, artifacts, snapshot_store)

        deploy_instance.set_system(
            mode_system_config,
            self.config_registry,
            package_paths=self._package_paths,
            snapshot_callback=snapshot_callback,
        )

        return mode_key, deploy_instance, snapshot_store

    def _export_mode_structure(self, mode_key: str, deploy_instance: DeploymentInstance, artifacts: BuildArtifacts):
        """Serialize one mode's DeploymentInstance to the system-structure JSON."""
        structure_payload = collect_system_structure(deploy_instance, artifacts.name, mode_key)
        structure_path = os.path.join(artifacts.layout.system_structure_dir, f"{mode_key}.json")
        save_system_structure(structure_path, structure_payload, workspace_root=artifacts.workspace_root)

    def _build_modes(self, system_config: SystemConfig, artifacts: BuildArtifacts) -> None:
        mode_names, default_mode = select_modes(system_config)
        artifacts.default_mode = default_mode
        if system_config.modes:
            logger.info(f"Building deployment for {len(mode_names)} modes: {mode_names}, default: {default_mode}")
        else:
            logger.info("Building deployment with single 'default' mode")

        for mode_name in mode_names:
            mode_key = mode_name if mode_name else default_mode
            snapshot_store: Dict[str, Any] = {}
            try:
                mode_system_config = apply_mode_configuration(system_config, mode_name)
                mode_key, deploy_instance, snapshot_store = self._build_mode_instance(
                    mode_name, mode_system_config, artifacts
                )
                self._export_mode_structure(mode_key, deploy_instance, artifacts)

                artifacts.mode_keys.append(mode_key)
                logger.info(f"Successfully built deployment instance for mode: {mode_key}")
                artifacts.system_structure_snapshots[mode_key] = snapshot_store

            except Exception as e:
                artifacts.system_structure_snapshots[mode_key] = snapshot_store
                # try to visualize the system to show error status
                generate_visualization(artifacts)
                default_note = " (default)" if mode_key == default_mode else ""
                system_path = getattr(system_config, "file_path", None)
                source = SourceLocation(file_path=Path(system_path)) if system_path else None
                error = annotate_error(
                    e, f"building deployment for mode '{mode_key}'{default_note}", source, wrap=DeploymentError
                )
                if error is e:
                    raise
                raise error from e

    def _check_used_duplicates(self) -> None:
        """Strict gate on duplicated names, raised once every mode has been built and reported."""
        duplicates = self.config_registry.used_duplicates()
        if not duplicates or not self.config_registry.strict:
            return
        raise ValidationError(
            f"{len(duplicates)} duplicated entity name(s) are used by this deployment; "
            f"'->' marks the definition in use:\n" + format_duplicate_report(duplicates)
        )

    def _collect_package_resolution(self, artifacts: BuildArtifacts) -> None:
        """Record per-package build resolution for the build-script generator."""
        package_resolution_by_name: Dict[str, str | None] = {}
        packages_without_provider: set[str] = set()
        for entity in self.config_registry.iter_used_configs():
            if not isinstance(entity, NodeConfig):
                continue
            pkg_name = entity.package_name
            if not pkg_name:
                continue
            if not entity.package_provider:
                packages_without_provider.add(pkg_name)
                continue
            resolution = entity.package_resolution
            if resolution is None:
                package_resolution_by_name.setdefault(pkg_name, None)
                continue
            existing = package_resolution_by_name.get(pkg_name)
            if existing != "source":
                package_resolution_by_name[pkg_name] = resolution
        artifacts.package_resolution_by_name = package_resolution_by_name
        artifacts.packages_without_provider = sorted(packages_without_provider)


def build_deployment(deploy_config: DeploymentConfig) -> BuildArtifacts:
    """Run the build stage and return the artifacts every generator consumes."""
    return DeploymentBuilder(deploy_config).build()


# ---------------------------------------------------------------------------
# Generators: consumers of BuildArtifacts and the exported JSON only.
# ---------------------------------------------------------------------------


def _iter_mode_data(artifacts: BuildArtifacts):
    return iter_mode_data(
        artifacts.mode_keys, artifacts.layout.system_structure_dir, workspace_root=artifacts.workspace_root
    )


def _collect_deploy_variable_names(artifacts: BuildArtifacts) -> List[str]:
    variable_names: List[str] = []
    seen = set()

    # 1) System arguments are treated as required launch arguments.
    for name in artifacts.argument_names:
        if name not in seen:
            seen.add(name)
            variable_names.append(name)

    # 2) Deploy-list variables are also forwarded.
    for deploy_item in artifacts.deploy_variants:
        for argument in deploy_item.get("arguments", deploy_item.get("variables", [])):
            if not isinstance(argument, dict):
                continue
            name = argument.get("name")
            if not isinstance(name, str) or not name or name in seen:
                continue
            seen.add(name)
            variable_names.append(name)
    return variable_names


def generate_visualization(artifacts: BuildArtifacts) -> None:
    """Generate the visualization pages from the exported JSON."""
    deploy_data = {mode_key: data for mode_key, data in _iter_mode_data(artifacts)}
    visualize_deployment(deploy_data, artifacts.name, artifacts.layout.visualization_dir, artifacts.system_file)


def generate_system_monitor_config(artifacts: BuildArtifacts) -> None:
    """Generate system monitor configuration from the exported JSON."""
    template_dir = os.path.join(os.path.dirname(__file__), "template")
    topics_template_path = os.path.join(template_dir, "sys_monitor_topics.yaml.jinja2")
    template_name = os.path.basename(topics_template_path)

    renderer = TemplateRenderer()
    for mode_key, data in _iter_mode_data(artifacts):
        mode_monitor_dir = os.path.join(artifacts.layout.system_monitor_dir, mode_key, "component_state_monitor")
        output_path = os.path.join(mode_monitor_dir, "topics.yaml")
        renderer.render_template_to_file(template_name, output_path, **data)

        logger.info(f"Generated system monitor for mode: {mode_key}")


def generate_build_scripts(artifacts: BuildArtifacts) -> None:
    """Generate shell build scripts from the exported JSON and package resolutions."""
    deploy_data = {mode_key: data for mode_key, data in _iter_mode_data(artifacts)}

    build_script_generator.generate_build_scripts(
        deploy_data,
        artifacts.layout.output_root_dir,
        artifacts.name,
        artifacts.system_file,
        artifacts.file_package_map,
        package_resolution_by_name=artifacts.package_resolution_by_name,
        packages_without_provider=set(artifacts.packages_without_provider),
    )


def generate_launchers(artifacts: BuildArtifacts) -> None:
    """Generate ROS 2 launch files from the exported JSON."""
    deploy_variable_names = _collect_deploy_variable_names(artifacts)
    for mode_key, data in _iter_mode_data(artifacts):
        mode_launcher_dir = os.path.join(artifacts.layout.launcher_dir, mode_key)

        generate_module_launch_file(
            data,
            mode_launcher_dir,
            forward_args=deploy_variable_names,
        )

        logger.info(f"Generated launcher for mode: {mode_key}")

    if artifacts.deploy_variants:
        generate_deploy_launchers(
            mode_keys=artifacts.mode_keys,
            system_structure_dir=artifacts.layout.system_structure_dir,
            launcher_dir=artifacts.layout.launcher_dir,
            deployment_package_path=artifacts.deployment_package_path,
            system_name=artifacts.name,
            deploy_variants=artifacts.deploy_variants,
            workspace_root=artifacts.workspace_root,
        )

    web_dir = os.path.join(artifacts.layout.visualization_dir, "web")
    if os.path.isdir(web_dir):
        generate_launch_commands_page(
            system_name=artifacts.name,
            package_name=artifacts.deployment_package_name,
            launcher_dir=artifacts.layout.launcher_dir,
            mode_keys=artifacts.mode_keys,
            web_dir=web_dir,
            deploy_variants=artifacts.deploy_variants,
        )


def export_parameter_set_templates(artifacts: BuildArtifacts) -> Dict[str, List[str]]:
    """Generate parameter set templates from the exported JSON."""
    if not artifacts.mode_keys:
        raise DeploymentError("Deployment instances are not initialized")

    output_paths = {}
    for mode_key, data in _iter_mode_data(artifacts):
        mode_parameter_dir = os.path.join(artifacts.layout.parameter_set_dir, mode_key)
        os.makedirs(mode_parameter_dir, exist_ok=True)

        renderer = TemplateRenderer()

        template_name = f"{artifacts.name}_{mode_key}" if mode_key != "default" else artifacts.name
        output_path_list = ParameterTemplateGenerator.generate_parameter_set_template_from_data(
            data, template_name, renderer, mode_parameter_dir
        )

        output_paths[mode_key] = output_path_list
        logger.info(f"Generated {len(output_path_list)} parameter set templates for mode: {mode_key}")

    return output_paths
