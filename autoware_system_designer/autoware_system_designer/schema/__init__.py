"""Schema definitions and validation.

This package intentionally avoids depending on implementation modules (builder/runtime)
so that validation remains format- and implementation-independent.
"""

from .yaml_schema import (
    SchemaIssue,
    get_entity_schema,
    validate_against_schema,
)
