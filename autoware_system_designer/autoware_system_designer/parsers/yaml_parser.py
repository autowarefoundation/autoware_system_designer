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

"""YAML configuration parser with caching support."""

import yaml
import logging
from pathlib import Path
from typing import Dict, Any, Union
from functools import lru_cache

from ..config import config
from ..exceptions import ValidationError

logger = logging.getLogger(__name__)


class YamlParser:
    """YAML parser with caching and validation."""
    
    def __init__(self, cache_enabled: bool = None):
        """Initialize YAML parser.
        
        Args:
            cache_enabled: Whether to enable caching. If None, uses global config.
        """
        self.cache_enabled = cache_enabled if cache_enabled is not None else config.cache_enabled
        self._cache: Dict[Path, Dict[str, Any]] = {}
    
    def load_config(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """Load YAML configuration file.
        
        Args:
            file_path: Path to YAML file
            
        Returns:
            Parsed YAML content as dictionary
            
        Raises:
            ValidationError: If file cannot be read or parsed
        """
        path = Path(file_path)
        
        if not path.exists():
            raise ValidationError(f"Configuration file not found: {path}")
        
        if not path.is_file():
            raise ValidationError(f"Path is not a file: {path}")
        
        # Check cache first
        if self.cache_enabled and path in self._cache:
            logger.debug(f"Loading configuration from cache: {path}")
            return self._cache[path]
        
        try:
            logger.debug(f"Loading configuration file: {path}")
            with open(path, 'r', encoding='utf-8') as stream:
                config_data = yaml.safe_load(stream)
                
            if config_data is None:
                config_data = {}
                
            # Cache the result
            if self.cache_enabled and config_data is not None:
                self._cache[path] = config_data
                
            return config_data
            
        except yaml.YAMLError as exc:
            raise ValidationError(f"Failed to parse YAML file {path}: {exc}")
        except Exception as exc:
            raise ValidationError(f"Failed to read configuration file {path}: {exc}")
    
    def load_config_list(self, file_list_path: Union[str, Path]) -> Dict[str, Any]:
        """Load configuration files from a list file.
        
        Args:
            file_list_path: Path to text file containing list of YAML file paths
            
        Returns:
            Dictionary mapping file paths to their configurations
            
        Raises:
            ValidationError: If list file cannot be read
        """
        list_path = Path(file_list_path)
        
        if not list_path.exists():
            raise ValidationError(f"File list not found: {list_path}")
        
        try:
            with open(list_path, 'r', encoding='utf-8') as file:
                file_paths = [line.strip() for line in file.readlines() if line.strip()]
            
            configs = {}
            for file_path in file_paths:
                path = Path(file_path)
                if not path.is_absolute():
                    # Make path relative to the list file's directory
                    path = list_path.parent / path
                
                configs[str(path)] = self.load_config(path)
            
            return configs
            
        except Exception as exc:
            raise ValidationError(f"Failed to read file list {list_path}: {exc}")
    
    def clear_cache(self):
        """Clear the configuration cache."""
        self._cache.clear()
        logger.debug("Configuration cache cleared")
    
    @lru_cache(maxsize=None)
    def get_cached_config(self, file_path: str) -> Dict[str, Any]:
        """Get cached configuration using LRU cache.
        
        Args:
            file_path: Path to configuration file
            
        Returns:
            Parsed configuration
        """
        return self.load_config(file_path)


# Global parser instance
yaml_parser = YamlParser()
