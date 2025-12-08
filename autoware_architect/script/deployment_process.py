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
from autoware_architect.deployment import Deployment
from autoware_architect.config import SystemConfig
from autoware_architect.visualization.visualization_index import update_index

# build the deployment
# search and connect the connections between the nodes
def build(deployment_file: str, manifest_dir: str, output_root_dir: str, domains: list[str]):
    # Inputs:
    #   deployment_file: YAML deployment configuration
    #   manifest_dir: directory containing per-package manifest YAML files (each lists system_config_files)
    #   output_root_dir: root directory for generated exports

    # configure the architecture
    system_config = SystemConfig()
    system_config.debug_mode = True
    system_config.log_level = "INFO"

    system_config.deployment_file = deployment_file
    system_config.manifest_dir = manifest_dir
    system_config.output_root_dir = output_root_dir
    system_config.domains = domains

    logger = system_config.set_logging()

    # load and build the deployment
    logger.info("autoware architect: Building deployment...")
    deployment = Deployment(system_config)

    # parameter set template export
    logger.info("autoware architect: Exporting parameter set template...")
    deployment.generate_parameter_set_template()

    # generate the system visualization
    logger.info("autoware architect: Generating visualization...")
    deployment.visualize()

    # generate the launch files
    logger.info("autoware architect: Generating launch files...")
    deployment.generate_launcher()

    # generate the system monitor configuration
    logger.info("autoware architect: Generating system monitor configuration...")
    deployment.generate_system_monitor()

    # update the visualization index
    logger.info("autoware architect: Updating visualization index...")
    update_index(output_root_dir)

    logger.info("autoware architect: Done!")


if __name__ == "__main__":
    # Usage: deployment_process.py <deployment_file> <manifest_dir> <output_root_dir> [domain1 [domain2 ...]]
    # If no domains provided, pass empty list; 'shared' will be added later by effective_domains().
    if len(sys.argv) < 4:
        raise SystemExit("Usage: deployment_process.py <deployment_file> <manifest_dir> <output_root_dir> [domain1 [domain2 ...]]")
    deployment_file = sys.argv[1]
    manifest_dir = sys.argv[2]
    output_root_dir = sys.argv[3]
    domain_args = sys.argv[4:]

    if len(domain_args) == 1 and (';' in domain_args[0]):
        domains = [d.strip() for d in domain_args[0].split(';') if d.strip()]
    else:
        domains = [d.strip() for d in domain_args if d.strip()]

    build(deployment_file, manifest_dir, output_root_dir, domains)
