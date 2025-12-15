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

import os
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

def generate_build_scripts(
    deploy_instances: Dict[str, Any],
    output_root_dir: str,
    deployment_name: str,
    config_yaml_dir: str,
    file_package_map: Dict[str, str]
):
    """Generate shell scripts to build necessary packages for each ECU."""
    logger.info("Generating build scripts...")
    
    # Get the package containing the system/deployment definition
    # This is needed for launch files
    system_pkg = None
    # Try to find package in the collected map
    if str(config_yaml_dir) in file_package_map:
        system_pkg = file_package_map[str(config_yaml_dir)]
    
    for mode_key, deploy_instance in deploy_instances.items():
        # Collection structure: ecu_name -> set of package names
        packages_by_ecu: Dict[str, set] = {}
        
        # Helper function to recursively collect packages
        def collect_packages(instance):
            # If it's a node, find its package
            if instance.entity_type == "node" and instance.configuration:
                pkg = instance.configuration.package
                if pkg and instance.compute_unit:
                    if instance.compute_unit not in packages_by_ecu:
                        packages_by_ecu[instance.compute_unit] = set()
                    packages_by_ecu[instance.compute_unit].add(pkg)
            
            # Recurse for children
            if hasattr(instance, "children"):
                for child in instance.children.values():
                    collect_packages(child)

        # Start collection from the root instance
        collect_packages(deploy_instance)
        
        # Generate scripts
        scripts_dir = os.path.join(output_root_dir, "exports", deployment_name, "build_scripts", mode_key)
        if not os.path.exists(scripts_dir):
            os.makedirs(scripts_dir)
            
        for ecu, pkgs in packages_by_ecu.items():
            if not pkgs:
                continue
            
            # Always include the system package (for launch files)
            if system_pkg:
                pkgs.add(system_pkg)
            
            sorted_pkgs = sorted(list(pkgs))
            script_path = os.path.join(scripts_dir, f"build_{ecu}.sh")
            
            with open(script_path, 'w') as f:
                f.write("#!/bin/bash\n")
                f.write("# Auto-generated build script for ECU: " + ecu + "\n")
                f.write("# Mode: " + mode_key + "\n\n")
                f.write("colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release --packages-up-to \\\n")
                f.write("  " + " \\\n  ".join(sorted_pkgs) + "\n")
            
            os.chmod(script_path, 0o755)
            logger.info(f"Generated build script: {script_path}")

