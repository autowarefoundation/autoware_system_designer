from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Optional


STRING_TYPES = {"string", "str"}
BOOL_TYPES = {"bool", "boolean"}
INTEGER_TYPES = {
    "int",
    "integer",
    "int8",
    "int16",
    "int32",
    "int64",
    "uint8",
    "uint16",
    "uint32",
    "uint64",
    "short",
    "long",
}
FLOAT_TYPES = {"float", "double", "float32", "float64"}
ARRAY_TYPES = {"array", "string_array", "bool_array", "int_array", "double_array"}
PATH_TYPES = {"directory"}

ALLOWED_PARAMETER_TYPES = (
    STRING_TYPES | BOOL_TYPES | INTEGER_TYPES | FLOAT_TYPES | ARRAY_TYPES | PATH_TYPES
)
NUMERIC_TYPES = INTEGER_TYPES | FLOAT_TYPES


def normalize_type_name(type_name: Any) -> Optional[str]:
    if type_name is None:
        return None
    if isinstance(type_name, str):
        return type_name.strip().lower()
    return str(type_name).strip().lower()


def is_supported_parameter_type(type_name: Optional[str]) -> bool:
    if not type_name:
        return False
    return type_name in ALLOWED_PARAMETER_TYPES


def is_numeric_type(type_name: Optional[str]) -> bool:
    return bool(type_name) and type_name in NUMERIC_TYPES


def is_integer_type(type_name: Optional[str]) -> bool:
    return bool(type_name) and type_name in INTEGER_TYPES


def coerce_numeric_value(value: Any, type_name: Optional[str]) -> Any:
    """Coerce numeric strings to numbers for numeric parameter types.

    Raises ValueError if the value cannot be coerced for integer types.
    """
    if value is None or not is_numeric_type(type_name):
        return value

    if isinstance(value, bool):
        raise ValueError(f"Invalid numeric value '{value}' for type '{type_name}'")

    if isinstance(value, (int, float)):
        if is_integer_type(type_name):
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"Non-integral value '{value}' for type '{type_name}'")
            return int(value)
        return float(value)

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"Empty numeric value for type '{type_name}'")
        try:
            dec = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid numeric value '{value}' for type '{type_name}'") from exc
        if is_integer_type(type_name):
            if dec != dec.to_integral_value():
                raise ValueError(f"Non-integral value '{value}' for type '{type_name}'")
            return int(dec)
        return float(dec)

    raise ValueError(f"Invalid numeric value '{value}' for type '{type_name}'")
