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

"""Building module - Config to Instance data flow."""

# Note: building module has circular dependencies with exporting module.
# Import submodules directly when needed to avoid circular imports at module load time.
# Example: from autoware_system_designer.building.config import ConfigRegistry
# Example: from autoware_system_designer.building.deployment_instance import DeploymentInstance

__all__ = []
