# Copyright 2025 TIER IV, inc.
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

import sys
import logging
from autoware_system_designer.deployment import Deployment
from autoware_system_designer.deployment_config import DeploymentConfig
from autoware_system_designer.visualization.visualization_index import update_index

_logger = logging.getLogger(__name__)


# build the deployment
# search and connect the connections between the nodes
def build(deployment_file: str, manifest_dir: str, output_root_dir: str):
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

    logger = deploy_config.set_logging()

    deployment = None
    try:
        # load and build the deployment
        logger.info("Autoware System Designer: Building deployment...")
        deployment = Deployment(deploy_config)

        # parameter set template export
        logger.info("Autoware System Designer: Exporting parameter set template...")
        deployment.generate_parameter_set_template()

        # generate the system visualization
        logger.info("Autoware System Designer: Generating visualization...")
        deployment.visualize()

        # generate the launch files
        logger.info("Autoware System Designer: Generating launch files...")
        deployment.generate_launcher()

        # generate the system monitor configuration
        logger.info("Autoware System Designer: Generating system monitor configuration...")
        deployment.generate_system_monitor()

        # generate build scripts
        logger.info("Autoware System Designer: Generating build scripts...")
        deployment.generate_build_scripts()

        # update the visualization index
        logger.info("Autoware System Designer: Updating visualization index...")
        update_index(output_root_dir)

        logger.info("Autoware System Designer: Done!")
    except Exception:
        # Surface minor-version mismatch warnings when the build fails.
        # The Deployment / ConfigRegistry layers may have already appended
        # the hint to the exception message, but if the failure happened
        # before a Deployment was fully constructed we still have the
        # registry to check.
        _emit_minor_version_hint(deployment)
        raise


def _emit_minor_version_hint(deployment):
    """Log minor-version mismatch files if any were recorded."""
    if deployment is None:
        return
    registry = getattr(deployment, "config_registry", None)
    if registry is None:
        return
    files = getattr(registry, "minor_version_mismatch_files", [])
    if not files:
        return
    from autoware_system_designer.builder.config.config_registry import _format_mismatch_hint
    _logger.warning(_format_mismatch_hint(files))


if __name__ == "__main__":
    # Usage: deployment_process.py <deployment_file> <manifest_dir> <output_root_dir>
    if len(sys.argv) < 4:
        raise SystemExit("Usage: deployment_process.py <deployment_file> <manifest_dir> <output_root_dir>")
    deployment_file = sys.argv[1]
    manifest_dir = sys.argv[2]
    output_root_dir = sys.argv[3]

    build(deployment_file, manifest_dir, output_root_dir)
