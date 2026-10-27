from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import jsonschema
from jsonschema.exceptions import ValidationError

from autoware_system_designer.common.parameter_types import is_supported_parameter_type, normalize_type_name
from autoware_system_designer.schema.format_version import check_format_version
from autoware_system_designer.schema.json_schema_loader import load_schema

JsonPointer = str


@dataclass(frozen=True)
class SchemaIssue:
    message: str
    yaml_path: Optional[JsonPointer] = None


def _jp_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def validate_against_schema(
    data: Any,
    *,
    entity_type: str = None,
    format_version: str = None,
    json_schema_dict: dict = None,
) -> List[SchemaIssue]:
    """Validate data against schema.

    This function uses JSON Schema validation and semantic checks.

    Args:
        data: Data to validate
        entity_type: Entity type for JSON Schema loading
        format_version: Format version for JSON Schema loading
        json_schema_dict: JSON Schema dictionary to validate against

    Returns:
        List of SchemaIssue objects
    """
    issues: List[SchemaIssue] = []

    if not isinstance(data, dict):
        return [SchemaIssue(message="Root must be a mapping/object", yaml_path="")]

    # Use JSON Schema validation if provided
    if json_schema_dict is not None:
        try:
            jsonschema.validate(instance=data, schema=json_schema_dict)
        except ValidationError as e:
            # Convert JSON Schema validation errors to SchemaIssue format
            path = "/" + "/".join(str(p) for p in e.absolute_path) if e.absolute_path else ""
            issues.append(SchemaIssue(message=e.message, yaml_path=path))
        except Exception as e:
            issues.append(SchemaIssue(message=f"JSON Schema validation error: {str(e)}", yaml_path=""))
    elif entity_type is not None and format_version is not None:
        # Load JSON Schema and validate
        try:
            json_schema = load_schema(entity_type, format_version)
            try:
                jsonschema.validate(instance=data, schema=json_schema)
            except ValidationError as e:
                path = "/" + "/".join(str(p) for p in e.absolute_path) if e.absolute_path else ""
                issues.append(SchemaIssue(message=e.message, yaml_path=path))
            except Exception as e:
                issues.append(SchemaIssue(message=f"JSON Schema validation error: {str(e)}", yaml_path=""))
        except FileNotFoundError as e:
            issues.append(SchemaIssue(message=str(e), yaml_path=""))
        except Exception as e:
            issues.append(SchemaIssue(message=f"Failed to load JSON Schema: {str(e)}", yaml_path=""))
    else:
        issues.append(SchemaIssue(message="No schema provided for validation", yaml_path=""))

    return issues


# -------------------------
# Semantic checks
# -------------------------


def _node_semantics(config: Dict[str, Any]) -> Iterable[SchemaIssue]:
    launch = config.get("launch")
    if launch is None or not isinstance(launch, dict):
        launch = None

    issues: List[SchemaIssue] = []

    if launch is not None:
        has_plugin = "plugin" in launch
        has_executable = "executable" in launch
        has_ros2_launch_file = "ros2_launch_file" in launch

        if not (has_plugin or has_executable or has_ros2_launch_file):
            issues.append(
                SchemaIssue(
                    message="Launch config must have at least one of: 'plugin', 'executable', or 'ros2_launch_file'",
                    yaml_path="/launch",
                )
            )

    issues.extend(_parameter_type_semantics(config.get("param_values"), base_path="/param_values"))
    return issues


def _parameter_set_semantics(config: Dict[str, Any]) -> Iterable[SchemaIssue]:
    issues: List[SchemaIssue] = []
    parameters = config.get("parameters")
    if not isinstance(parameters, list):
        return issues

    for idx, node_entry in enumerate(parameters):
        if not isinstance(node_entry, dict):
            continue
        issues.extend(
            _parameter_type_semantics(
                node_entry.get("param_values"),
                base_path=f"/parameters/{idx}/param_values",
            )
        )
    return issues


def _parameter_type_semantics(parameters: Any, *, base_path: str) -> Iterable[SchemaIssue]:
    if not isinstance(parameters, list):
        return []

    issues: List[SchemaIssue] = []
    for idx, param in enumerate(parameters):
        if not isinstance(param, dict):
            continue
        raw_type = param.get("type")
        if raw_type is None:
            continue
        type_name = normalize_type_name(raw_type)
        if not is_supported_parameter_type(type_name):
            issues.append(
                SchemaIssue(
                    message=f"Unsupported parameter type '{raw_type}'",
                    yaml_path=f"{base_path}/{idx}/type",
                )
            )
    return issues


_LATEST = "latest"

# ``$(var NAME)`` substitution token, as used in a data path_pattern
VAR_TOKEN = re.compile(r"\$\(var\s+([A-Za-z0-9_]+)\s*\)")


def data_semantics(config: Dict[str, Any]) -> List[SchemaIssue]:
    """Cross-field checks for data entities (variant axes + path_pattern).

    Single source of the variant-axis rules for both the linter and
    :class:`~...building.resolution.data_variant_scanner.DataVariantScanner`.
    """
    issues: List[SchemaIssue] = []

    variants = config.get("variants")
    declared_axes: set = set()
    if isinstance(variants, list):
        for idx, axis in enumerate(variants):
            if not isinstance(axis, dict):
                continue
            name = axis.get("name")
            if name is None:
                continue
            sortable = bool(axis.get("sortable"))
            if name in declared_axes:
                issues.append(
                    SchemaIssue(
                        message=f"Duplicate variant axis name '{name}'",
                        yaml_path=f"/variants/{idx}/name",
                    )
                )
            declared_axes.add(name)

            # `latest` is only meaningful on a sortable axis.
            default = axis.get("default")
            if default == _LATEST and not sortable:
                issues.append(
                    SchemaIssue(
                        message=f"Variant axis '{name}' uses default 'latest' but is not 'sortable: true'",
                        yaml_path=f"/variants/{idx}/default",
                    )
                )
            values = axis.get("values")
            if isinstance(values, list) and _LATEST in values and not sortable:
                issues.append(
                    SchemaIssue(
                        message=f"Variant axis '{name}' lists value 'latest' but is not 'sortable: true'",
                        yaml_path=f"/variants/{idx}/values",
                    )
                )
            # `latest` selects among the declared values, so a sortable axis must enumerate them.
            if sortable and not (isinstance(values, list) and values):
                issues.append(
                    SchemaIssue(
                        message=f"Variant axis '{name}' is 'sortable: true' but declares no 'values'",
                        yaml_path=f"/variants/{idx}",
                    )
                )

    # path_pattern: one layout; every declared axis appears exactly once as a whole segment.
    if "path_patterns" in config:
        issues.append(
            SchemaIssue(
                message="'path_patterns' is no longer supported; declare a single 'path_pattern' "
                "(one data entity per artifactory layout)",
                yaml_path="/path_patterns",
            )
        )
    pattern = config.get("path_pattern")
    if isinstance(pattern, str):
        used: List[str] = []
        for seg in pattern.split("/"):
            m = VAR_TOKEN.fullmatch(seg.strip())
            if m:
                used.append(m.group(1))
        for token in VAR_TOKEN.findall(pattern):
            if token not in declared_axes:
                issues.append(
                    SchemaIssue(
                        message=(
                            f"path_pattern references unknown variable '{token}'; "
                            f"only declared variant axes {sorted(declared_axes)} may be used"
                        ),
                        yaml_path="/path_pattern",
                    )
                )
        for axis_name in sorted(set(used)):
            if used.count(axis_name) > 1:
                issues.append(
                    SchemaIssue(
                        message=f"path_pattern uses variant axis '{axis_name}' more than once",
                        yaml_path="/path_pattern",
                    )
                )
        addressed = set(used)
    else:
        addressed = set()
    for axis_name in sorted(declared_axes - addressed):
        issues.append(
            SchemaIssue(
                message=f"variant axis '{axis_name}' does not appear in path_pattern as a '$(var {axis_name})' segment",
                yaml_path="/path_pattern",
            )
        )

    return issues


def _format_version_semantics(config: Dict[str, Any]) -> Iterable[SchemaIssue]:
    """Check the ``autoware_system_design_format`` field for compatibility."""
    raw = config.get("autoware_system_design_format")
    result = check_format_version(raw)

    if raw is None:
        # Missing version → emit a warning-level issue.
        yield SchemaIssue(
            message=result.message,
            yaml_path="/autoware_system_design_format",
        )
    elif not result.compatible:
        # Major version mismatch → error (must stop).
        yield SchemaIssue(
            message=result.message,
            yaml_path="/autoware_system_design_format",
        )
    # minor_newer is intentionally not emitted here; SchemaIssues are
    # treated as hard errors by validate_all().  The minor-version
    # warning is handled at the config_registry and linter layers.


def _variant_forbidden_root_fields_semantics(
    *,
    forbidden_fields: Sequence[str],
    message_prefix: str,
) -> Callable[[Dict[str, Any]], Iterable[SchemaIssue]]:
    def _check(config: Dict[str, Any]) -> Iterable[SchemaIssue]:
        if "base" not in config:
            return []

        issues: List[SchemaIssue] = []
        if "override" in config and not isinstance(config.get("override"), dict):
            issues.append(SchemaIssue(message="'override' must be a dictionary", yaml_path="/override"))
        if "remove" in config and not isinstance(config.get("remove"), dict):
            issues.append(SchemaIssue(message="'remove' must be a dictionary", yaml_path="/remove"))

        for key in forbidden_fields:
            if key in config:
                issues.append(
                    SchemaIssue(
                        message=f"{message_prefix}: field '{key}' must be under 'override' in variant config",
                        yaml_path=f"/{_jp_escape(key)}",
                    )
                )
        return issues

    return _check


def get_semantic_checks(
    entity_type: str,
) -> Tuple[Callable[[Dict[str, Any]], Iterable[SchemaIssue]], ...]:
    """Get semantic check functions for an entity type.

    Semantic checks are cross-field validation rules that cannot be
    expressed in JSON Schema (e.g., "at least one of X, Y, or Z").

    Args:
        entity_type: Entity type (node, module, system, parameter_set)

    Returns:
        Tuple of semantic check functions
    """
    if entity_type == "node":
        return (
            _format_version_semantics,
            _node_semantics,
            _variant_forbidden_root_fields_semantics(
                forbidden_fields=(
                    "package",
                    "launch",
                    "inputs",
                    "outputs",
                    "param_files",
                    "param_values",
                    "processes",
                    "required_data",
                ),
                message_prefix="Variant rule",
            ),
        )
    elif entity_type == "module":
        return (
            _format_version_semantics,
            _variant_forbidden_root_fields_semantics(
                forbidden_fields=("instances", "inputs", "outputs", "connections"),
                message_prefix="Variant rule",
            ),
        )
    elif entity_type == "parameter_set":
        return (
            _format_version_semantics,
            _parameter_set_semantics,
        )
    elif entity_type == "system":
        return (
            _format_version_semantics,
            _variant_forbidden_root_fields_semantics(
                forbidden_fields=(
                    "modes",
                    "parameter_sets",
                    "components",
                    "connections",
                    "node_groups",
                    "arguments",
                    "variables",
                    "variable_files",
                    "data",
                ),
                message_prefix="Variant rule",
            ),
        )
    elif entity_type == "data":
        return (
            _format_version_semantics,
            data_semantics,
            _variant_forbidden_root_fields_semantics(
                forbidden_fields=(
                    "category",
                    "variants",
                    "path_pattern",
                    "manifest",
                    "scripts",
                ),
                message_prefix="Variant rule",
            ),
        )
    else:
        return (_format_version_semantics,)
