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

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from autoware_system_designer.common.source_location import SourceLocation
from autoware_system_designer.model.domain import ParameterType


def parameter_type_to_str(value) -> str:
    """Convert ParameterType enum (or any value with .name) to string for export."""
    if value is None:
        return ""
    if hasattr(value, "name"):
        return value.name
    return str(value)


_SCIENTIFIC_NOTATION_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?[eE][+-]?\d+$")

_TYPE_ALIASES: Dict[str, str] = {
    "str": "string",
    "boolean": "bool",
    "float": "double",
    "float32": "double",
    "float64": "double",
    "integer": "int",
    "int8": "int",
    "int16": "int",
    "int32": "int",
    "int64": "int",
    "uint8": "int",
    "uint16": "int",
    "uint32": "int",
    "uint64": "int",
    "short": "int",
    "long": "int",
    "directory": "string",
}


def _infer_array_type(value: Any) -> str:
    """Infer the most specific ROS 2 array type from a Python list value."""
    if not isinstance(value, list) or not value:
        return "string_array"
    if all(isinstance(e, bool) for e in value):
        return "bool_array"
    if all(isinstance(e, int) for e in value):
        return "int_array"
    if all(isinstance(e, float) for e in value):
        return "double_array"
    return "string_array"


def canonical_parameter_type(data_type: Optional[str], value: Any) -> str:
    """Canonical ROS 2 parameter type name; inferred from the value when absent."""
    normalized = (data_type or "").strip().lower()
    normalized = _TYPE_ALIASES.get(normalized, normalized)

    # "array" without a subtype qualifier: infer the element type from value.
    if normalized == "array":
        return _infer_array_type(value)

    if not normalized:
        if isinstance(value, bool):
            return "bool"
        if isinstance(value, int):
            return "int"
        if isinstance(value, float):
            return "double"
        if isinstance(value, list):
            return _infer_array_type(value)

    return normalized or "string"


def _float_to_decimal_str(value: float) -> str:
    """Convert a float to a decimal string without scientific notation."""
    s = repr(value)
    if "e" in s or "E" in s:
        return f"{value:.15f}".rstrip("0").rstrip(".")
    return s


def normalize_parameter_value(value: Any) -> Any:
    """Render floats and scientific-notation strings as plain decimal strings.

    Handles two cases:
    - float: Python's YAML parser converts unquoted '1e-3' to float 0.001,
      which json.dumps may re-serialize as '1e-05' for very small values.
    - str: quoted '1e-3' remains a string and must be expanded to '0.001'.
    """
    if isinstance(value, float):
        return _float_to_decimal_str(value)
    if isinstance(value, str) and _SCIENTIFIC_NOTATION_PATTERN.match(value.strip()):
        return _float_to_decimal_str(float(value))
    return value


@dataclass(eq=False)
class Parameter:
    """A single parameter; values and type names are stored in canonical export form."""

    name: str
    value: Any
    data_type: str = field(default="string", metadata={"alias": "type"})
    # path to the schema file if available
    schema_path: Optional[str] = field(default=None, metadata={"exclude": True})
    # whether to allow substitutions in ROS launch
    allow_substs: bool = field(default=True, metadata={"exclude": True})
    # Parameter type with priority
    parameter_type: ParameterType = ParameterType.DEFAULT
    source: Optional[SourceLocation] = None

    def __post_init__(self):
        self.assign(self.value, self.data_type)

    def assign(self, value: Any, data_type: Optional[str]) -> None:
        """Store a value with its canonical type name and normalized rendering."""
        self.data_type = canonical_parameter_type(data_type, value)
        self.value = normalize_parameter_value(value)


class ParameterList:
    """Manages a list of parameters with priority-based resolution.
    Higher priority parameters override lower priority ones.
    """

    def __init__(self):
        self.list: List[Parameter] = []

    def get_parameter(self, parameter_name):
        """Get the highest priority parameter value by name.
        Higher priority parameters override lower priority ones.
        """
        highest_priority_param = None
        for parameter in self.list:
            if parameter.name == parameter_name:
                if (
                    highest_priority_param is None
                    or parameter.parameter_type.value > highest_priority_param.parameter_type.value
                ):
                    highest_priority_param = parameter
        return highest_priority_param.value if highest_priority_param else None

    def set_parameter(
        self,
        parameter_name,
        parameter_value,
        data_type: str = "string",
        schema_path: Optional[str] = None,
        allow_substs: bool = True,
        parameter_type: ParameterType = ParameterType.DEFAULT,
        source: Optional[SourceLocation] = None,
    ):
        """Set a parameter value.

        Higher priority parameters override lower priority ones.
        Lower priority parameters cannot override higher priority ones.

        Args:
            parameter_name: Name of the parameter
            parameter_value: Value of the parameter
            data_type: Data type of the value
            schema_path: Optional schema path
            allow_substs: Whether to allow substitutions
            parameter_type: Type of parameter with priority
        """
        # Find existing parameter
        for parameter in self.list:
            if parameter.name == parameter_name:
                # Only update if the new parameter has equal or higher priority
                if parameter_type.value >= parameter.parameter_type.value:
                    parameter.assign(parameter_value, data_type)
                    parameter.schema_path = schema_path
                    parameter.allow_substs = allow_substs
                    parameter.parameter_type = parameter_type
                    if source is not None:
                        parameter.source = source
                # If lower priority, don't update (higher priority takes precedence)
                return

        # Not found, add new parameter
        self.list.append(
            Parameter(
                parameter_name,
                parameter_value,
                data_type,
                schema_path,
                allow_substs,
                parameter_type,
                source,
            )
        )


@dataclass(eq=False)
class ParameterFile:
    """Represents a parameter file reference."""

    name: str
    path: str
    # path to the schema file if available
    schema_path: Optional[str] = field(default=None, metadata={"exclude": True})
    # whether to allow substitutions in ROS launch
    allow_substs: bool = True
    # True for override parameter files, False for default
    is_override: bool = False
    parameter_type: ParameterType = ParameterType.DEFAULT_FILE
    source: Optional[SourceLocation] = None


class ParameterFileList:
    """Manages a list of parameter files.
    Parameter files are accumulated in the order they are added.
    Override parameter files take precedence over default parameter files.
    """

    def __init__(self):
        self.list: List[ParameterFile] = []

    def get_parameter_file(self, parameter_name):
        """Get the last (most recent/override) parameter file path by name."""
        for param_file in reversed(self.list):
            if param_file.name == parameter_name:
                return param_file.path
        # not found, return None
        return None

    def add_parameter_file(
        self,
        parameter_name,
        parameter_path,
        schema_path: Optional[str] = None,
        allow_substs: bool = True,
        is_override: bool = False,
        parameter_type: ParameterType = ParameterType.DEFAULT_FILE,
        source: Optional[SourceLocation] = None,
    ):
        """Add a parameter file.

        Parameter files are accumulated in the order they are added.
        Override parameter files take precedence over default parameter files.

        Args:
            parameter_name: Name of the parameter file
            parameter_path: Path to the parameter file
            schema_path: Optional schema path
            allow_substs: Whether to allow substitutions
            is_override: True for override parameter files, False for default
            parameter_type: Type of parameter file
        """
        new_param_file = ParameterFile(
            parameter_name,
            parameter_path,
            schema_path,
            allow_substs,
            is_override,
            parameter_type,
            source,
        )
        self.list.append(new_param_file)
