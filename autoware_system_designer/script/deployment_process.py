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

import argparse
import logging
from pathlib import Path

import yaml

from autoware_system_designer.common.deployment_config import DeploymentConfig
from autoware_system_designer.common.exceptions import SystemDesignerError, render_error
from autoware_system_designer.deploy import (
    build_deployment,
    export_parameter_set_templates,
    generate_build_scripts,
    generate_launchers,
    export_reference_latency,
    generate_system_monitor_config,
)

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


def build(
    deployment_file: str,
    manifest_dir: str,
    output_root_dir: str,
    workspace_yaml: str | None = None,
    package_name: str | None = None,
):
    # Inputs:
    #   deployment_file: YAML deployment configuration
    #   manifest_dir: directory containing per-package manifest YAML files (each lists deploy_config_files)
    #   output_root_dir: root directory for generated exports
    #   package_name: package that owns the deployment; its entities are searched first

    # configure the autoware system design format files
    # Start from env defaults so callers (e.g. CMake) can control terminal verbosity.
    deploy_config = DeploymentConfig.from_env()

    deploy_config.deployment_file = deployment_file
    deploy_config.manifest_dir = manifest_dir
    deploy_config.output_root_dir = output_root_dir
    deploy_config.workspace_config = _load_workspace_config(workspace_yaml)
    deploy_config.deployment_package_name = package_name or None

    logger = deploy_config.set_logging()

    artifacts = None
    try:
        # build stage: parse, build every mode, export system structure + manifest
        logger.info("Autoware System Designer: Building deployment...")
        artifacts = build_deployment(deploy_config)

        # generators: consumers of the build artifacts
        logger.info("Autoware System Designer: Exporting parameter set template...")
        export_parameter_set_templates(artifacts)

        logger.info("Autoware System Designer: Exporting reference latency...")
        export_reference_latency(artifacts)

        logger.info("Autoware System Designer: Generating launch files...")
        generate_launchers(artifacts)

        logger.info("Autoware System Designer: Generating system monitor configuration...")
        generate_system_monitor_config(artifacts)

        logger.info("Autoware System Designer: Generating build scripts...")
        generate_build_scripts(artifacts)

        logger.info("Autoware System Designer: Done!")
    except SystemDesignerError as exc:
        # The process boundary reports once: hints attach to the error and the
        # whole block (message, context frames, hints) renders in one log entry.
        for hint in _registry_hints(artifacts, exc):
            exc.add_hint(hint)
        _logger.error(render_error(exc))
        raise
    except Exception as exc:
        # An unexpected failure keeps its traceback; the hints still apply, and
        # they matter most where the error itself names no design entity.
        for hint in _registry_hints(artifacts, exc):
            _logger.error(hint)
        raise


def _find_registry(artifacts, exc):
    """Registry attached to the exception, or the build artifacts' when the build completed."""
    registry = getattr(exc, "config_registry", None)
    if registry is not None:
        return registry
    return getattr(artifacts, "config_registry", None) if artifacts else None


def _registry_hints(artifacts, exc) -> list:
    """Duplicate-name and minor-version hints recorded by the registry."""
    registry = _find_registry(artifacts, exc)
    if registry is None:
        return []
    from autoware_system_designer.builder.config.config_registry import (
        format_duplicate_report,
        format_mismatch_hint,
    )

    hints = []
    duplicates = registry.used_duplicates()
    if duplicates:
        hints.append(
            f"Note: {len(duplicates)} duplicated entity name(s) are used by this deployment. "
            f"This may have contributed to the error:\n" + format_duplicate_report(duplicates)
        )
    files = getattr(registry, "minor_version_mismatch_files", [])
    if files:
        hints.append(format_mismatch_hint(files))
    return hints


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an autoware system designer deployment.")
    parser.add_argument("deployment_file", help="Deployment table, system file, or bare system name")
    parser.add_argument("manifest_dir", help="Directory of per-package design manifests")
    parser.add_argument("output_root_dir", help="Root directory for generated exports")
    parser.add_argument("workspace_yaml", nargs="?", default=None, help="Optional workspace.yaml path")
    parser.add_argument("--package", default=None, help="Package that owns the deployment")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        build(args.deployment_file, args.manifest_dir, args.output_root_dir, args.workspace_yaml, args.package)
    except SystemDesignerError:
        # Already rendered by the boundary handler; a traceback would repeat it.
        raise SystemExit(1)
