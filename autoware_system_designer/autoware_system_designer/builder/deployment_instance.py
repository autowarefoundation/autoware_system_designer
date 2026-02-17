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

from typing import Callable, Dict

from ..models.config import SystemConfig
from .instances.instances import Instance
from .instances.instance_pipeline import (
    set_system as set_system_impl,
    check_duplicate_node_namespaces as check_duplicate_node_namespaces_impl,
)


class DeploymentInstance(Instance):
    """Top-level deployment instance representing a complete system deployment.

    This instance manages the entire system hierarchy, including setting up the system
    configuration, building the instance tree, establishing connections, and resolving parameters.
    """

    def __init__(self, name: str):
        super().__init__(name)

    def set_system(
        self,
        system_config: SystemConfig,
        config_registry,
        package_paths: Dict[str, str] = {},
        snapshot_callback: Callable[[str, Exception | None], None] | None = None,
    ):
        set_system_impl(
            self,
            system_config,
            config_registry,
            package_paths=package_paths,
            snapshot_callback=snapshot_callback,
        )

    def check_duplicate_node_namespaces(self):
        """Check for duplicate node namespaces in the entire system."""
        check_duplicate_node_namespaces_impl(self)
