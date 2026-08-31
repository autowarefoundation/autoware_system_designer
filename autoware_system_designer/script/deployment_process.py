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

import logging
import sys
from pathlib import Path

import yaml

from autoware_system_designer.common.deployment_config import DeploymentConfig
from autoware_system_designer.common.exceptions import SystemDesignerError, render_error
from autoware_system_designer.deploy import (
    build_deployment,
    export_parameter_set_templates,
    generate_build_scripts,
    generate_launchers,
    generate_system_monitor_config,
    generate_visualization,
)
from autoware_system_designer.visualizer.visualization_index import update_index

# Stable name whether imported or executed as a script.
_logger = logging.getLogger("autoware_system_designer.deployment_process")


# build the deployment
# search and connect the connections between the nodes
def _load_workspace_config(workspace_yaml: str | None):
    if not workspace_yaml:
        return None
    path = Path(workspace_yaml)
    if not path.exists() or not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        _logger.warning("Failed to read workspace.yaml '%s': %s", workspace_yaml, exc)
        return None
    workspace_list = data.get("workspace")
    if not isinstance(workspace_list, list):
        return None
    return workspace_list


def build(deployment_file: str, manifest_dir: str, output_root_dir: str, workspace_yaml: str | None = None):
    # Inputs:
    #   deployment_file: YAML deployment configuration
    #   manifest_dir: directory containing per-package manifest YAML files (each lists deploy_config_files)
    #   output_root_dir: root directory for generated exports

    # configure the autoware system design format files
    # Start from env defaults so callers (e.g. CMake) can control terminal verbosity.
    deploy_config = DeploymentConfig.from_env()

    deploy_config.deployment_file = deployment_file
    deploy_config.manifest_dir = manifest_dir
    deploy_config.output_root_dir = output_root_dir
    deploy_config.workspace_config = _load_workspace_config(workspace_yaml)

    logger = deploy_config.set_logging()

    artifacts = None
    try:
        # build stage: parse, build every mode, export system structure + manifest
        logger.info("Autoware System Designer: Building deployment...")
        artifacts = build_deployment(deploy_config)

        # generators: consumers of the build artifacts
        logger.info("Autoware System Designer: Exporting parameter set template...")
        export_parameter_set_templates(artifacts)

        logger.info("Autoware System Designer: Generating visualization...")
        generate_visualization(artifacts)

        logger.info("Autoware System Designer: Generating launch files...")
        generate_launchers(artifacts)

        logger.info("Autoware System Designer: Generating system monitor configuration...")
        generate_system_monitor_config(artifacts)

        logger.info("Autoware System Designer: Generating build scripts...")
        generate_build_scripts(artifacts)

        # update the visualization index
        logger.info("Autoware System Designer: Updating visualization index...")
        update_index(output_root_dir)

        logger.info("Autoware System Designer: Done!")
    except SystemDesignerError as exc:
        # The process boundary reports once: hints attach to the error and the
        # whole block (message, context frames, hints) renders in one log entry.
        _attach_registry_hints(artifacts, exc)
        _logger.error(render_error(exc))
        raise


def _find_registry(artifacts, exc):
    """Registry attached to the exception, or the build artifacts' when the build completed."""
    registry = getattr(exc, "config_registry", None)
    if registry is not None:
        return registry
    return getattr(artifacts, "config_registry", None) if artifacts else None


def _attach_registry_hints(artifacts, exc: SystemDesignerError) -> None:
    """Attach duplicate-name and minor-version hints recorded by the registry."""
    registry = _find_registry(artifacts, exc)
    if registry is None:
        return
    from autoware_system_designer.builder.config.config_registry import (
        format_duplicate_report,
        format_mismatch_hint,
    )

    duplicates = registry.used_duplicates()
    if duplicates:
        exc.add_hint(
            f"Note: {len(duplicates)} duplicated entity name(s) are used by this deployment. "
            f"This may have contributed to the error:\n" + format_duplicate_report(duplicates)
        )
    files = getattr(registry, "minor_version_mismatch_files", [])
    if files:
        exc.add_hint(format_mismatch_hint(files))


if __name__ == "__main__":
    # Usage: deployment_process.py <deployment_file> <manifest_dir> <output_root_dir> [workspace_yaml]
    if len(sys.argv) < 4:
        raise SystemExit(
            "Usage: deployment_process.py <deployment_file> <manifest_dir> <output_root_dir> [workspace_yaml]"
        )
    deployment_file = sys.argv[1]
    manifest_dir = sys.argv[2]
    output_root_dir = sys.argv[3]
    workspace_yaml = sys.argv[4] if len(sys.argv) > 4 else None

    try:
        build(deployment_file, manifest_dir, output_root_dir, workspace_yaml)
    except SystemDesignerError:
        # Already rendered by the boundary handler; a traceback would repeat it.
        raise SystemExit(1)
