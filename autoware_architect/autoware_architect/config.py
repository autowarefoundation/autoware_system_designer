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


@dataclass
class SystemConfig:
    """Configuration class for the autoware system deployment.
    domains:
      - `domains` is the list of active domains provided externally.
      - 'shared' is automatically appended if missing.
    """
    debug_mode: bool = False
    layer_limit: int = 50
    log_level: str = "INFO"
    cache_enabled: bool = False
    max_cache_size: int = 128

    # paths
    deployment_file: str = ""
    manifest_dir: str = ""
    output_root_dir: str = "build"
    domains: list[str] | None = None

    @classmethod
    def from_env(cls) -> 'SystemConfig':
        """Create configuration from environment variables."""
        return cls(
            debug_mode=os.getenv('AUTOWARE_ARCHITECT_DEBUG', 'false').lower() == 'true',
            layer_limit=int(os.getenv('AUTOWARE_ARCHITECT_LAYER_LIMIT', '50')),
            log_level=os.getenv('AUTOWARE_ARCHITECT_LOG_LEVEL', 'INFO'),
            cache_enabled=os.getenv('AUTOWARE_ARCHITECT_CACHE_ENABLED', 'true').lower() == 'true',
            max_cache_size=int(os.getenv('AUTOWARE_ARCHITECT_MAX_CACHE_SIZE', '128'))
        )

    def set_logging(self) -> logging.Logger:
        """Setup logging based on configuration."""
        logger = logging.getLogger('autoware_architect')
        # Clear existing handlers
        logger.handlers.clear()
        # Create handler
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        # Set level
        level = getattr(logging, self.log_level.upper(), logging.INFO)
        if self.debug_mode:
            level = logging.DEBUG
        logger.setLevel(level)
        return logger

    def effective_domains(self) -> list[str]:
        """Return active domains (deduped, ordered) including 'shared'."""
        raw = [d.strip() for d in (self.domains or []) if d and d.strip()]
        if 'shared' not in raw:
            raw.append('shared')
        ordered: list[str] = []
        seen = set()
        for d in raw:
            if d not in seen:
                ordered.append(d)
                seen.add(d)
        return ordered


# Global configuration instance
config = SystemConfig.from_env()
