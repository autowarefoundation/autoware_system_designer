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

"""Configuration management for the autoware system."""

import os
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..utils.logging_utils import configure_split_stream_logging


@dataclass
class DeploymentConfig:
    """Configuration class for the autoware system deployment."""
    layer_limit: int = 50
    log_level: str = "INFO"
    print_level: str = "ERROR"
    cache_enabled: bool = False
    max_cache_size: int = 128

    # paths
    deployment_file: str = ""
    manifest_dir: str = ""
    output_root_dir: str = "build"
    workspace_config: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def from_env(cls) -> 'DeploymentConfig':
        """Create configuration from environment variables."""
        return cls(
            layer_limit=int(os.getenv('autoware_system_designer_LAYER_LIMIT', '50')),
            log_level=os.getenv('autoware_system_designer_LOG_LEVEL', 'INFO'),
            print_level=os.getenv('autoware_system_designer_PRINT_LEVEL', 'ERROR'),
            cache_enabled=os.getenv('autoware_system_designer_CACHE_ENABLED', 'true').lower() == 'true',
            max_cache_size=int(os.getenv('autoware_system_designer_MAX_CACHE_SIZE', '128'))
        )

    def set_logging(self) -> logging.Logger:
        """Setup logging based on configuration."""
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        stderr_level = getattr(logging, self.print_level.upper(), logging.ERROR)

        formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
        configure_split_stream_logging(level=level, stderr_level=stderr_level, formatter=formatter)

        return logging.getLogger('autoware_system_designer')


# Global configuration instance
deploy_config = DeploymentConfig.from_env()
