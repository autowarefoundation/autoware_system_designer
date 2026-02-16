from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import jsonschema
from jsonschema.exceptions import ValidationError

from ..utils.parameter_types import normalize_type_name, is_supported_parameter_type
from ..utils.format_version import check_format_version
from .json_schema_loader import load_schema


JsonPointer = str


@dataclass(frozen=True)
class SchemaIssue:
    message: str
    yaml_path: Optional[JsonPointer] = None


@dataclass(frozen=True)
class TypeSpec:
    types: Tuple[type, ...]


@dataclass(frozen=True)
class UnionSpec:
    options: Tuple["SchemaSpec", ...]


@dataclass(frozen=True)
class ListSpec:
    item: "SchemaSpec"


@dataclass(frozen=True)
class ObjectSpec:
    fields: Dict[str, "FieldSpec"]
    allow_extra: bool = True


SchemaSpec = Union[TypeSpec, UnionSpec, ListSpec, ObjectSpec]


@dataclass(frozen=True)
class FieldSpec:
    spec: SchemaSpec
    required: bool = False


@dataclass(frozen=True)
class EntitySchema:
    entity_type: str

    # Fields required in every config of this type
    required_fields: Tuple[str, ...] = ()

    # Fields required only when "base" is not present
    required_fields_when_no_base: Tuple[str, ...] = ()

    # Root object schema
    root: ObjectSpec = field(default_factory=lambda: ObjectSpec(fields={}, allow_extra=True))

    # Extra semantic rules (cross-field constraints)
    semantic_checks: Tuple[Callable[[Dict[str, Any]], Iterable[SchemaIssue]], ...] = ()


def _jp_escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _join_path(base: Optional[JsonPointer], token: str) -> JsonPointer:
    if not base:
        return f"/{_jp_escape(token)}"
    return f"{base}/{_jp_escape(token)}"


def _type_name(types: Tuple[type, ...]) -> str:
    return " | ".join(t.__name__ for t in types)


def validate_against_schema(
    data: Any,
    *,
    schema: EntitySchema = None,
    entity_type: str = None,
    format_version: str = None,
    json_schema_dict: dict = None,
) -> List[SchemaIssue]:
    """Validate data against schema.
    
    This function now supports both the legacy EntitySchema approach and
    the new JSON Schema approach. If json_schema_dict is provided, it uses
    JSON Schema validation. Otherwise, it falls back to the legacy approach.
    
    Args:
        data: Data to validate
        schema: Legacy EntitySchema (deprecated, use json_schema_dict instead)
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
    elif schema is not None:
        # Legacy EntitySchema validation (deprecated)
        # Base required field checks
        for key in schema.required_fields:
            if key not in data:
                issues.append(SchemaIssue(message=f"Missing required field '{key}'", yaml_path=_join_path("", key)))

        if "base" not in data:
            for key in schema.required_fields_when_no_base:
                if key not in data:
                    issues.append(
                        SchemaIssue(
                            message=f"Missing required field '{key}' in base config (no 'base')",
                            yaml_path=_join_path("", key),
                        )
                    )

        issues.extend(_validate_spec(schema.root, data, path=""))

        # Semantic checks
        for check in schema.semantic_checks:
            try:
                issues.extend(list(check(data)))
            except Exception as exc:
                issues.append(SchemaIssue(message=f"Internal semantic check error: {exc}", yaml_path=""))
    else:
        issues.append(SchemaIssue(message="No schema provided for validation", yaml_path=""))

    return issues


def _validate_spec(spec: SchemaSpec, value: Any, *, path: JsonPointer) -> List[SchemaIssue]:
    if isinstance(spec, TypeSpec):
        if not isinstance(value, spec.types):
            return [SchemaIssue(message=f"Invalid type: expected {_type_name(spec.types)}", yaml_path=path)]
        return []

    if isinstance(spec, UnionSpec):
        # Accept if any option validates with no issues.
        option_issues: List[List[SchemaIssue]] = []
        for opt in spec.options:
            errs = _validate_spec(opt, value, path=path)
            if not errs:
                return []
            option_issues.append(errs)
        # If none match, surface a concise error.
        return [SchemaIssue(message="Value does not match any allowed schema", yaml_path=path)]

    if isinstance(spec, ListSpec):
        if not isinstance(value, list):
            return [SchemaIssue(message="Invalid type: expected list", yaml_path=path)]
        issues: List[SchemaIssue] = []
        for idx, item in enumerate(value):
            issues.extend(_validate_spec(spec.item, item, path=f"{path}/{idx}" if path else f"/{idx}"))
        return issues

    if isinstance(spec, ObjectSpec):
        if not isinstance(value, dict):
            return [SchemaIssue(message="Invalid type: expected object", yaml_path=path)]
        issues: List[SchemaIssue] = []
        for field_name, field_spec in spec.fields.items():
            if field_spec.required and field_name not in value:
                issues.append(
                    SchemaIssue(
                        message=f"Missing required field '{field_name}'",
                        yaml_path=_join_path(path, field_name),
                    )
                )
        for field_name, field_value in value.items():
            if field_name not in spec.fields:
                if spec.allow_extra:
                    continue
                issues.append(SchemaIssue(message=f"Unknown field '{field_name}'", yaml_path=_join_path(path, field_name)))
                continue
            issues.extend(_validate_spec(spec.fields[field_name].spec, field_value, path=_join_path(path, field_name)))
        return issues

    return [SchemaIssue(message="Internal error: unknown schema spec", yaml_path=path)]


# -------------------------
# Entity schema definitions
# -------------------------

_STR = TypeSpec((str,))
_BOOL = TypeSpec((bool,))
_NUM = TypeSpec((int, float))
_OBJ = TypeSpec((dict,))
_LIST = TypeSpec((list,))
_ANY = TypeSpec((object,))


def _list_of_objects(required_keys: Sequence[str] = ()) -> ListSpec:
    obj_fields = {k: FieldSpec(_ANY, required=True) for k in required_keys}
    return ListSpec(ObjectSpec(fields=obj_fields, allow_extra=True))


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

        if launch.get("use_container") is True and "container_name" not in launch:
            issues.append(
                SchemaIssue(
                    message="Launch config must have 'container_name' when 'use_container' is true",
                    yaml_path="/launch/use_container",
                )
            )

    issues.extend(_parameter_type_semantics(config.get("parameters"), base_path="/parameters"))
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
                node_entry.get("parameters"),
                base_path=f"/parameters/{idx}/parameters",
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


def get_entity_schema(entity_type: str) -> EntitySchema:
    # Common: allow top-level autoware_system_design_format but don't require it yet.
    common_root_fields: Dict[str, FieldSpec] = {
        "autoware_system_design_format": FieldSpec(_STR, required=False),
        "name": FieldSpec(_STR, required=True),
        "base": FieldSpec(_STR, required=False),
        "override": FieldSpec(_OBJ, required=False),
        "remove": FieldSpec(_OBJ, required=False),
    }

    if entity_type == "node":
        package_spec = ObjectSpec(
            fields={
                "name": FieldSpec(_STR, required=True),
                "provider": FieldSpec(_STR, required=True),
            },
            allow_extra=True,
        )
        root = ObjectSpec(
            fields={
                **common_root_fields,
                "package": FieldSpec(package_spec, required=False),
                "launch": FieldSpec(_OBJ, required=False),
                "inputs": FieldSpec(_list_of_objects(required_keys=("name", "message_type")), required=False),
                "outputs": FieldSpec(_list_of_objects(required_keys=("name", "message_type")), required=False),
                "parameter_files": FieldSpec(
                    UnionSpec((TypeSpec((dict,)), _list_of_objects(required_keys=("name",)))), required=False
                ),
                "parameters": FieldSpec(UnionSpec((TypeSpec((dict,)), _list_of_objects(required_keys=("name",)))), required=False),
                "processes": FieldSpec(_list_of_objects(required_keys=("name", "trigger_conditions", "outcomes")), required=False),
            },
            allow_extra=True,
        )
        return EntitySchema(
            entity_type=entity_type,
            required_fields=("name",),
            required_fields_when_no_base=(
                "package",
                "launch",
                "inputs",
                "outputs",
                "parameter_files",
                "parameters",
                "processes",
            ),
            root=root,
            semantic_checks=(
                _format_version_semantics,
                _node_semantics,
                _variant_forbidden_root_fields_semantics(
                    forbidden_fields=("package", "launch", "inputs", "outputs", "parameter_files", "parameters", "processes"),
                    message_prefix="Variant rule",
                ),
            ),
        )

    if entity_type == "module":
        ext_interfaces = ObjectSpec(
            fields={
                "input": FieldSpec(_list_of_objects(required_keys=("name",)), required=False),
                "output": FieldSpec(_list_of_objects(required_keys=("name",)), required=False),
            },
            allow_extra=True,
        )
        module_instance_spec = ListSpec(
            ObjectSpec(
                fields={
                    "name": FieldSpec(_STR, required=True),
                    "entity": FieldSpec(_STR, required=True),
                    "launch": FieldSpec(_OBJ, required=False),
                },
                allow_extra=True,
            )
        )
        root = ObjectSpec(
            fields={
                **common_root_fields,
                "instances": FieldSpec(module_instance_spec, required=False),
                "external_interfaces": FieldSpec(UnionSpec((ext_interfaces, TypeSpec((list,)))), required=False),
                "connections": FieldSpec(_list_of_objects(required_keys=("from", "to")), required=False),
            },
            allow_extra=True,
        )
        return EntitySchema(
            entity_type=entity_type,
            required_fields=("name",),
            required_fields_when_no_base=("instances", "external_interfaces", "connections"),
            root=root,
            semantic_checks=(
                _format_version_semantics,
                _variant_forbidden_root_fields_semantics(
                    forbidden_fields=("instances", "external_interfaces", "connections"),
                    message_prefix="Variant rule",
                ),
            ),
        )

    if entity_type == "parameter_set":
        parameter_files_entry = ObjectSpec(fields={}, allow_extra=True)
        parameter_set_item = ObjectSpec(
            fields={
                "node": FieldSpec(_STR, required=True),
                # Each entry is typically a one-key dict: {param_file_name: path}
                "parameter_files": FieldSpec(ListSpec(parameter_files_entry), required=False),
                "parameters": FieldSpec(_list_of_objects(required_keys=("name",)), required=False),
            },
            allow_extra=True,
        )
        root = ObjectSpec(
            fields={
                **common_root_fields,
                "parameters": FieldSpec(ListSpec(parameter_set_item), required=True),
                "local_variables": FieldSpec(ListSpec(ObjectSpec(fields={}, allow_extra=True)), required=False),
            },
            allow_extra=True,
        )
        return EntitySchema(
            entity_type=entity_type,
            required_fields=("name", "parameters"),
            root=root,
            semantic_checks=(
                _format_version_semantics,
                _parameter_set_semantics,
            ),
        )

    if entity_type == "system":
        root = ObjectSpec(
            fields={
                **common_root_fields,
                "arguments": FieldSpec(_list_of_objects(required_keys=("name",)), required=False),
                "variables": FieldSpec(_list_of_objects(required_keys=("name",)), required=False),
                "variable_files": FieldSpec(ListSpec(ObjectSpec(fields={}, allow_extra=True)), required=False),
                "modes": FieldSpec(_list_of_objects(required_keys=("name",)), required=False),
                "parameter_sets": FieldSpec(ListSpec(_STR), required=False),
                "components": FieldSpec(_list_of_objects(required_keys=("name", "entity")), required=False),
                "connections": FieldSpec(_list_of_objects(required_keys=("from", "to")), required=False),
            },
            allow_extra=True,
        )
        return EntitySchema(
            entity_type=entity_type,
            required_fields=("name",),
            required_fields_when_no_base=("components", "connections"),
            root=root,
            semantic_checks=(
                _format_version_semantics,
                _variant_forbidden_root_fields_semantics(
                    forbidden_fields=(
                        "modes",
                        "parameter_sets",
                        "components",
                        "connections",
                        "arguments",
                        "variables",
                        "variable_files",
                    ),
                    message_prefix="Variant rule",
                ),
            ),
        )

    raise ValueError(f"Unknown entity type: {entity_type}")


def get_semantic_checks(entity_type: str) -> Tuple[Callable[[Dict[str, Any]], Iterable[SchemaIssue]], ...]:
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
                forbidden_fields=("package", "launch", "inputs", "outputs", "parameter_files", "parameters", "processes"),
                message_prefix="Variant rule",
            ),
        )
    elif entity_type == "module":
        return (
            _format_version_semantics,
            _variant_forbidden_root_fields_semantics(
                forbidden_fields=("instances", "external_interfaces", "connections"),
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
                    "arguments",
                    "variables",
                    "variable_files",
                ),
                message_prefix="Variant rule",
            ),
        )
    else:
        return (_format_version_semantics,)
