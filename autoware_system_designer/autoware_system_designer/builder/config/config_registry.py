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

import copy
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Type

from autoware_system_designer.builder.resolution.variant_resolver import (
    ModuleVariantResolver,
    NodeVariantResolver,
    SystemVariantResolver,
    VariantResolver,
)
from autoware_system_designer.common.exceptions import (
    FormatVersionError,
    ModuleConfigurationError,
    NodeConfigurationError,
    ParameterConfigurationError,
    ValidationError,
    annotate_error,
)
from autoware_system_designer.common.path_utils import canonical_path
from autoware_system_designer.common.source_location import SourceLocation, format_source, source_from_config
from autoware_system_designer.model.config import (
    Config,
    ConfigSubType,
    ConfigType,
    ModuleConfig,
    NodeConfig,
    ParameterSetConfig,
    SystemConfig,
)
from autoware_system_designer.parser.data_parser import ConfigParser
from autoware_system_designer.parser.data_validator import entity_name_decode, split_package_qualifier
from autoware_system_designer.schema.format_version import check_format_version

logger = logging.getLogger(__name__)


def format_mismatch_hint(mismatch_files: list) -> str:
    """Build a human-readable hint listing files with version mismatches.

    Each entry in *mismatch_files* is ``(file_path, file_version, supported_version)``.
    """
    lines = []
    for entry in mismatch_files:
        fpath, file_ver, supported_ver = entry
        lines.append(f"  {fpath}  (file: {file_ver}, supported: {supported_ver})")
    file_list = "\n".join(lines)
    return (
        f"Note: the following design files use a newer minor format version "
        f"than this tool supports.  This may have contributed to the error:\n"
        f"{file_list}"
    )


# Distance stand-in when no anchor is known, leaving load order to break the tie.
_UNRANKED = 1 << 20


def _path_distance(anchor: Path, target: Path) -> int:
    """Number of steps between two paths, walking up to their common ancestor and back down."""
    anchor_parts = anchor.parts
    target_parts = target.parts
    shared = 0
    for left, right in zip(anchor_parts, target_parts):
        if left != right:
            break
        shared += 1
    return (len(anchor_parts) - shared) + (len(target_parts) - shared)


@lru_cache(maxsize=None)
def _package_root(path: Path) -> Path:
    """Nearest ancestor holding a package.xml; the containing directory when none is found."""
    start = path.parent if path.is_file() else path
    for candidate in (start, *start.parents):
        if (candidate / "package.xml").is_file():
            return candidate
    return start


def _package_distance(anchor: Path, target: Path) -> int:
    """Tree distance between the packages owning two paths; layout inside a package does not count."""
    return _path_distance(_package_root(anchor), _package_root(target))


@dataclass(frozen=True)
class SelectionPolicy:
    """Ranking inputs for picking one config among several declaring the same name."""

    anchor_dir: Optional[Path] = None
    preferred_package: Optional[str] = None

    def rank(self, config: Config) -> Tuple[int, int]:
        """Preferred package first, then package proximity to the anchor; lower wins."""
        tier = 0 if self.preferred_package and config.package == self.preferred_package else 1
        if self.anchor_dir is None:
            return tier, _UNRANKED
        return tier, _package_distance(self.anchor_dir, Path(canonical_path(str(config.file_path))))


@dataclass
class EntityGroup:
    """Every config declaring one full_name, in load order. Selection is memoized on first use.

    A bare reference shares one ranked selection; a package-qualified reference picks within
    that package, memoized per package.
    """

    full_name: str
    name: str
    entity_type: str
    candidates: List[Config] = field(default_factory=list)
    _selected: Optional[Config] = None
    _pinned: Dict[str, Config] = field(default_factory=dict)

    @property
    def is_duplicated(self) -> bool:
        return len(self.candidates) > 1

    @property
    def used(self) -> bool:
        return self._selected is not None or bool(self._pinned)

    @property
    def selected(self) -> Optional[Config]:
        """Memoized selection; None until the group is resolved."""
        return self._selected

    def in_use(self) -> List[Config]:
        """Every memoized pick, bare selection first, without repeats."""
        picks: List[Config] = []
        for config in (self._selected, *self._pinned.values()):
            if config is not None and not any(config is pick for pick in picks):
                picks.append(config)
        return picks

    def _package_candidates(self, package: str) -> List[Config]:
        matches = [candidate for candidate in self.candidates if candidate.package == package]
        if not matches:
            declaring = sorted({candidate.package for candidate in self.candidates if candidate.package})
            raise ValidationError(
                f"Entity '{self.full_name}' is not declared in package '{package}'. "
                f"Packages declaring it: {declaring}"
            )
        return matches

    def choose(self, policy: SelectionPolicy, package: Optional[str] = None) -> Config:
        """Best candidate under *policy*, load order breaking ties; *package* restricts the
        candidates to that package. Leaves the group unused."""
        if package is not None:
            if package in self._pinned:
                return self._pinned[package]
            candidates = self._package_candidates(package)
        else:
            if self._selected is not None:
                return self._selected
            candidates = self.candidates
        best = min(range(len(candidates)), key=lambda i: (policy.rank(candidates[i]), i))
        return candidates[best]

    def resolve(self, policy: SelectionPolicy, package: Optional[str] = None) -> Tuple[Config, bool]:
        """Select and memoize; the flag marks the first resolution, which gates reporting."""
        if package is not None:
            if package in self._pinned:
                return self._pinned[package], False
            self._pinned[package] = self.choose(policy, package)
            return self._pinned[package], True
        if self._selected is not None:
            return self._selected, False
        self._selected = self.choose(policy)
        return self._selected, True

    def describe(self) -> str:
        """Candidate list with '->' on every pick in use."""
        picks = self.in_use()
        return "\n".join(
            f"  {'->' if any(candidate is pick for pick in picks) else '  '} {candidate.file_path}"
            + (f"  [{candidate.package}]" if candidate.package else "")
            for candidate in self.candidates
        )


def format_duplicate_report(groups: List[EntityGroup]) -> str:
    """Human-readable listing of duplicated names, '->' marking the definition in use."""
    blocks = [f"Duplicate entity '{group.full_name}':\n{group.describe()}" for group in groups]
    return "\n".join(blocks)


class ConfigRegistry:
    """Collection for managing multiple entity data structures with efficient lookup methods."""

    def __init__(
        self,
        config_yaml_file_paths: List[str],
        package_paths: Dict[str, str] = None,
        file_package_map: Dict[str, str] = None,
        package_source_paths: Dict[str, str] = None,
        workspace_config: List[Dict[str, Any]] = None,
        strict: bool = False,
        anchor_dir: Optional[str] = None,
    ):
        # Sole entity store: full_name → every config declaring that name. A name is duplicated
        # when its group holds more than one candidate; which one wins is decided lazily, on use.
        self.entities: Dict[str, EntityGroup] = {}
        self.package_paths = package_paths or {}
        self.file_package_map = file_package_map or {}
        self._package_source_paths: Dict[str, Optional[str]] = dict(package_source_paths or {})
        self._canonical_package_paths: Optional[Dict[str, str]] = None
        # Name of the package currently being built/exported (deployment package).
        self.deployment_package_name: Optional[str] = None

        # Workspace provider resolution map: provider -> "source" | "installed"
        self._provider_resolution_map: Dict[str, str] = {}
        if workspace_config:
            for entry in workspace_config:
                if isinstance(entry, dict) and "provider" in entry and "resolution" in entry:
                    self._provider_resolution_map[entry["provider"]] = entry["resolution"]

        # Track files whose minor format version is newer than the tool supports.
        # Populated during _load_entities; surfaced to the user when the build fails.
        self.minor_version_mismatch_files: List[str] = []

        self.strict = strict

        # Anchor duplicated candidates are ranked against; assignable until the first lookup.
        self.anchor_dir: Optional[str] = anchor_dir

        self.parser = ConfigParser()
        self._load_entities(config_yaml_file_paths)
        self._log_duplicate_scan()

    @property
    def selection_policy(self) -> SelectionPolicy:
        """Current ranking inputs for duplicated names."""
        return SelectionPolicy(
            anchor_dir=Path(canonical_path(self.anchor_dir)) if self.anchor_dir else None,
            preferred_package=self.deployment_package_name,
        )

    def _load_entities(self, config_yaml_file_paths: List[str]) -> None:
        """Load entities from configuration files."""
        from autoware_system_designer.parser.yaml_parser import yaml_parser as _yaml_parser

        for file_path in config_yaml_file_paths:
            logger.debug(f"Loading entity from: {file_path}")

            # ── format-version gate ──────────────────────────────────
            try:
                raw_ver = _yaml_parser.peek(file_path, "autoware_system_design_format")
                version_readable = True
            except Exception:
                version_readable = False  # let parse_entity_file report the real error

            if version_readable:
                ver_result = check_format_version(raw_ver)
                if not ver_result.compatible:
                    # Major version mismatch → stop immediately
                    src = SourceLocation(file_path=Path(file_path))
                    raise FormatVersionError(f"{ver_result.message}{format_source(src)}")
                if ver_result.minor_newer:
                    # Minor version newer than tool → warn and track
                    src = SourceLocation(file_path=Path(file_path))
                    logger.warning(f"{ver_result.message}{format_source(src)}")
                    self.minor_version_mismatch_files.append(
                        (file_path, str(ver_result.file_version), str(ver_result.supported_version))
                    )
            # ─────────────────────────────────────────────────────────

            try:
                entity_data = self.parser.parse_entity_file(file_path)

                # Set package name if available
                if entity_data.file_path and str(entity_data.file_path) in self.file_package_map:
                    entity_data.package = self.file_package_map[str(entity_data.file_path)]

                # For node entities, resolve the provider against the workspace config
                if isinstance(entity_data, NodeConfig) and entity_data.package_provider:
                    resolution = self._provider_resolution_map.get(entity_data.package_provider)
                    if resolution:
                        entity_data.package_resolution = resolution

                group = self.entities.get(entity_data.full_name)
                if group is None:
                    group = EntityGroup(
                        full_name=entity_data.full_name,
                        name=entity_data.name,
                        entity_type=entity_data.entity_type,
                    )
                    self.entities[entity_data.full_name] = group
                group.candidates.append(entity_data)

            except Exception as e:
                src = SourceLocation(file_path=Path(file_path))
                error = annotate_error(e, f"loading design file {file_path}", src)
                # Newer-minor files loaded so far may be related to the failure.
                if self.minor_version_mismatch_files:
                    error.add_hint(format_mismatch_hint(self.minor_version_mismatch_files))
                if error is e:
                    raise
                raise error from e

    def iter_used_configs(self) -> Iterator[Config]:
        """The memoized selection of every group this deployment resolved."""
        for group in self.entities.values():
            yield from group.in_use()

    def all_duplicates(self) -> List[EntityGroup]:
        """Every duplicated name in the scan, whether the deployment reaches it or not."""
        return [group for group in self.entities.values() if group.is_duplicated]

    def used_duplicates(self) -> List[EntityGroup]:
        """Duplicated names this deployment resolved by a bare reference; package-qualified picks are explicit."""
        return [group for group in self.entities.values() if group.is_duplicated and group.selected is not None]

    def _log_duplicate_scan(self) -> None:
        """Workspace-wide collision summary; the deployment-relevant subset is reported on use."""
        if not logger.isEnabledFor(logging.DEBUG):
            return
        duplicates = self.all_duplicates()
        if not duplicates:
            return
        logger.debug(
            f"{len(duplicates)} duplicated entity name(s) in the scan; candidates in load order:\n"
            + format_duplicate_report(duplicates)
        )

    def _report_duplicate(self, group: EntityGroup, chosen: Config) -> None:
        """Surface a duplicated name at the point the deployed system first uses it."""
        message = (
            f"Duplicate entity '{group.full_name}' is used by this deployment; "
            f"'->' marks the definition in use:\n{group.describe()}"
        )
        logger.warning(f"{message}{format_source(source_from_config(chosen, '/name'))}")

    def _find_group(self, name: str, config_type: str) -> Optional[EntityGroup]:
        """Group registered under name.config_type; the name may carry a package qualifier and the type suffix."""
        _, name = split_package_qualifier(name)
        group = self.entities.get(f"{name}.{config_type}")
        if group is None and "." in name:
            try:
                decoded_name, entity_type = entity_name_decode(name)
                if entity_type == config_type:
                    group = self.entities.get(f"{decoded_name}.{config_type}")
            except ValidationError:
                pass
        return group

    def system_group(self, name: str) -> Optional[EntityGroup]:
        """Candidate group for a system name; reading it leaves the group unused."""
        return self._find_group(name, ConfigType.SYSTEM)

    def _get_entity_with_base(
        self,
        name: str,
        config_type: str,
        error_cls: Type[Exception],
        resolver_cls: Optional[Type[VariantResolver]] = None,
        recursive_getter: Optional[Callable[[str], Config]] = None,
    ) -> Config:
        """
        Generic method to get an entity and resolve base/variant if applicable.
        """
        group = self._find_group(name, config_type)

        if group is None:
            available = [g.name for g in self.entities.values() if g.entity_type == config_type]
            raise error_cls(f"{config_type.capitalize()} '{name}' not found. Available {config_type}s: {available}")

        package, _ = split_package_qualifier(name)
        try:
            entity, first_use = group.resolve(self.selection_policy, package)
        except ValidationError as e:
            raise error_cls(str(e)) from e
        if first_use and group.is_duplicated and package is None:
            self._report_duplicate(group, entity)

        if entity.sub_type == ConfigSubType.VARIANT:
            if not resolver_cls or not recursive_getter:
                # Variant requested but no resolver provided, return as is (or could raise error)
                return entity

            # Get parent name
            base_target = entity.base
            if not base_target:
                # Should have been validated, but fallback
                return entity

            # Resolve parent (recursive)
            parent = recursive_getter(base_target)

            # Create a deep copy of the parent to serve as the base for this entity
            # This ensures we don't modify the parent object
            resolved_entity = copy.deepcopy(parent)

            # Update the identity of the resolved entity to match the current entity
            resolved_entity.name = entity.name
            resolved_entity.full_name = entity.full_name
            resolved_entity.file_path = entity.file_path
            resolved_entity.package = entity.package
            resolved_entity.sub_type = entity.sub_type
            resolved_entity.config = entity.config  # Keep original config with overrides

            # Apply overrides from this entity's config
            resolver = resolver_cls()
            resolver.resolve(resolved_entity, entity.config)

            return resolved_entity

        return entity

    # Enhanced methods for type-safe entity access
    def get_node(self, name: str) -> NodeConfig:
        """Get a node entity by name."""
        return self._get_entity_with_base(
            name, ConfigType.NODE, NodeConfigurationError, NodeVariantResolver, self.get_node
        )

    def get_module(self, name: str) -> ModuleConfig:
        """Get a module entity by name."""
        return self._get_entity_with_base(
            name,
            ConfigType.MODULE,
            ModuleConfigurationError,
            ModuleVariantResolver,
            self.get_module,
        )

    def get_parameter_set(self, name: str) -> ParameterSetConfig:
        """Get a parameter set entity by name."""
        return self._get_entity_with_base(name, ConfigType.PARAMETER_SET, ParameterConfigurationError)

    def get_system(self, name: str) -> SystemConfig:
        """Get an system entity by name. Resolves base/variant if applicable."""
        return self._get_entity_with_base(
            name,
            ConfigType.SYSTEM,
            ValidationError,  # System uses ValidationError in original code, keeping it
            SystemVariantResolver,
            self.get_system,
        )

    def get_entity_by_type(self, name: str, entity_type: str) -> Config:
        """Get an entity by name and type."""
        if entity_type == ConfigType.NODE:
            return self.get_node(name)
        elif entity_type == ConfigType.MODULE:
            return self.get_module(name)
        elif entity_type == ConfigType.PARAMETER_SET:
            return self.get_parameter_set(name)
        elif entity_type == ConfigType.SYSTEM:
            return self.get_system(name)
        else:
            raise ValidationError(f"Unknown entity type: {entity_type}")

    def get_package_path(self, package_name: str) -> Optional[str]:
        """Get package path by package name."""
        return self.package_paths.get(package_name)

    def get_package_source_path(self, package_name: str) -> Optional[str]:
        """Best-effort lookup of a package's *source* directory.

        This is intentionally independent from the install/share path stored in package_paths.
        It is used to avoid false negatives during build-time checks, where install/share may
        not yet contain installed resources (e.g., config/*.yaml).
        """
        if not package_name:
            return None
        if package_name in self._package_source_paths:
            return self._package_source_paths[package_name]

        # Find any design/config yaml that belongs to this package, then walk up to package.xml.
        for file_path, pkg in self.file_package_map.items():
            if pkg != package_name:
                continue
            try:
                current = Path(file_path).resolve().parent
                while True:
                    if (current / "package.xml").exists():
                        self._package_source_paths[package_name] = str(current)
                        return self._package_source_paths[package_name]
                    if current.parent == current:
                        break
                    current = current.parent
            except Exception:
                continue

        # Cache negative result to avoid repeated scans.
        self._package_source_paths[package_name] = None
        return None

    def source_counterpart(self, path: str) -> Optional[str]:
        """Source-tree path of a file under a mapped package directory; None when unmapped or unknown."""
        if self._canonical_package_paths is None:
            self._canonical_package_paths = {
                name: canonical_path(package_path) for name, package_path in self.package_paths.items() if package_path
            }
        target = canonical_path(path)
        owner, owner_dir = None, ""
        for package_name, package_path in self._canonical_package_paths.items():
            if len(package_path) > len(owner_dir) and target.startswith(package_path + os.sep):
                owner, owner_dir = package_name, package_path
        if owner is None:
            return None
        source_dir = self.get_package_source_path(owner)
        if not source_dir:
            return None
        return os.path.join(source_dir, os.path.relpath(target, owner_dir))

    def get_provider_resolution(self, provider: str) -> Optional[str]:
        """Get the resolution type for a given provider.

        Args:
            provider: The provider identifier (e.g., 'autoware', 'ros', 'dummy').

        Returns:
            'source' if the provider's packages are built from source in the workspace,
            'installed' if they are pre-built library packages,
            or None if the provider is not in the workspace config.
        """
        return self._provider_resolution_map.get(provider)
