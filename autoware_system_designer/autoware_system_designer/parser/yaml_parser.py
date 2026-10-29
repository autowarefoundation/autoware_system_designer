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

"""YAML configuration parser with caching support."""

import copy
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import yaml

from autoware_system_designer.common.deployment_config import deploy_config
from autoware_system_designer.common.exceptions import ValidationError

logger = logging.getLogger(__name__)


@dataclass
class _CacheEntry:
    """Parsed file content, valid while the file's stamp on disk is unchanged."""

    stamp: Tuple[int, int]
    data: Dict[str, Any]
    source_map: Optional[Dict[str, Dict[str, int]]] = None


class YamlParser:
    """YAML parser with caching and validation."""

    def __init__(self, cache_enabled: bool = None):
        """Initialize YAML parser.

        Args:
            cache_enabled: Whether to enable caching. If None, uses global config.
        """
        self.cache_enabled = cache_enabled if cache_enabled is not None else deploy_config.cache_enabled
        self._cache: Dict[Path, _CacheEntry] = {}

    @staticmethod
    def _stamp(path: Path) -> Tuple[int, int]:
        """Modification time and size of the file; a cache entry is valid while they match."""
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    def _cached(self, path: Path, stamp: Tuple[int, int]) -> Optional[_CacheEntry]:
        entry = self._cache.get(path) if self.cache_enabled else None
        return entry if entry is not None and entry.stamp == stamp else None

    @staticmethod
    def _json_pointer_escape(token: str) -> str:
        # JSON Pointer escaping: "~" -> "~0", "/" -> "~1"
        return token.replace("~", "~0").replace("/", "~1")

    @classmethod
    def _build_source_map_from_yaml(cls, content: str) -> Dict[str, Dict[str, int]]:
        """Build a mapping from YAML JSON-pointer-like paths to 1-based line/column.

        This uses PyYAML's node tree (yaml.compose) so we can track locations without
        changing the parsed data shapes returned by safe_load.
        """
        source_map: Dict[str, Dict[str, int]] = {}

        try:
            root = yaml.compose(content, Loader=yaml.SafeLoader)
        except Exception:
            # If compose fails, return empty source map. Parsing errors are handled elsewhere.
            return source_map

        if root is None:
            return source_map

        def _record(path: str, node) -> None:
            mark = getattr(node, "start_mark", None)
            if mark is None:
                return
            # PyYAML uses 0-based line/column
            source_map[path] = {"line": int(mark.line) + 1, "column": int(mark.column) + 1}

        def _walk(node, path: str) -> None:
            _record(path, node)

            if isinstance(node, yaml.nodes.MappingNode):
                for key_node, value_node in node.value:
                    key = getattr(key_node, "value", None)
                    if key is None:
                        continue
                    child_path = (
                        f"{path}/{cls._json_pointer_escape(str(key))}"
                        if path
                        else f"/{cls._json_pointer_escape(str(key))}"
                    )
                    _walk(value_node, child_path)
            elif isinstance(node, yaml.nodes.SequenceNode):
                for idx, item_node in enumerate(node.value):
                    child_path = f"{path}/{idx}" if path else f"/{idx}"
                    _walk(item_node, child_path)
            else:
                # ScalarNode - already recorded
                return

        _walk(root, "")
        return source_map

    def load_config_with_source(self, file_path: Union[str, Path]) -> Tuple[Dict[str, Any], Dict[str, Dict[str, int]]]:
        """Load YAML configuration file and return (data, source_map).

        source_map keys are JSON-pointer-like YAML paths (e.g. "/parameters/0/value").
        Values contain 1-based line/column.
        """
        path = Path(file_path)

        if not path.exists():
            raise ValidationError(f"Configuration file not found: {path}")

        if not path.is_file():
            raise ValidationError(f"Path is not a file: {path}")

        # Callers own the returned data; the cache keeps a private copy.
        stamp = self._stamp(path)
        entry = self._cached(path, stamp)
        if entry is not None and entry.source_map is not None:
            logger.debug(f"Loading configuration (with source) from cache: {path}")
            return copy.deepcopy(entry.data), entry.source_map

        try:
            logger.debug(f"Loading configuration file (with source): {path}")
            content = path.read_text(encoding="utf-8")
            config_data = yaml.safe_load(content)
            if config_data is None:
                config_data = {}

            source_map = self._build_source_map_from_yaml(content)

            if self.cache_enabled:
                self._cache[path] = _CacheEntry(stamp, copy.deepcopy(config_data), source_map)

            return config_data, source_map
        except yaml.YAMLError as exc:
            raise ValidationError(f"Failed to parse YAML file {path}: {exc}")
        except Exception as exc:
            raise ValidationError(f"Failed to read configuration file {path}: {exc}")

    def load_config_from_string_with_source(self, content: str) -> Tuple[Dict[str, Any], Dict[str, Dict[str, int]]]:
        """Load YAML configuration from string content and return (data, source_map)."""
        try:
            config_data = yaml.safe_load(content)
            if config_data is None:
                config_data = {}
            source_map = self._build_source_map_from_yaml(content)
            return config_data, source_map
        except yaml.YAMLError as exc:
            raise ValidationError(f"Failed to parse YAML content: {exc}")
        except Exception as exc:
            raise ValidationError(f"Failed to process configuration content: {exc}")

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

        # Callers own the returned data; the cache keeps a private copy.
        stamp = self._stamp(path)
        entry = self._cached(path, stamp)
        if entry is not None:
            logger.debug(f"Loading configuration from cache: {path}")
            return copy.deepcopy(entry.data)

        try:
            logger.debug(f"Loading configuration file: {path}")
            with open(path, "r", encoding="utf-8") as stream:
                config_data = yaml.safe_load(stream)

            if config_data is None:
                config_data = {}

            if self.cache_enabled:
                self._cache[path] = _CacheEntry(stamp, copy.deepcopy(config_data))

            return config_data

        except yaml.YAMLError as exc:
            raise ValidationError(f"Failed to parse YAML file {path}: {exc}")
        except Exception as exc:
            raise ValidationError(f"Failed to read configuration file {path}: {exc}")

    def load_config_from_string(self, content: str) -> Dict[str, Any]:
        """Load YAML configuration from string content.

        Args:
            content: YAML string content

        Returns:
            Parsed YAML content as dictionary

        Raises:
            ValidationError: If content cannot be parsed
        """
        try:
            config_data = yaml.safe_load(content)

            if config_data is None:
                config_data = {}

            return config_data

        except yaml.YAMLError as exc:
            raise ValidationError(f"Failed to parse YAML content: {exc}")
        except Exception as exc:
            raise ValidationError(f"Failed to process configuration content: {exc}")

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
            with open(list_path, "r", encoding="utf-8") as file:
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


# Global parser instance
yaml_parser = YamlParser()
