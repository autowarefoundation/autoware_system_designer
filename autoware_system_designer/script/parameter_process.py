#!/usr/bin/env python3

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml


class CustomDumper(yaml.SafeDumper):
    """Custom YAML dumper to handle list formatting and line breaks."""

    def write_line_break(self, data: Optional[str] = None) -> None:
        super().write_line_break(data)
        if len(self.indents) == 1:
            super().write_line_break()

    def represent_list(self, data: List[Any]) -> Any:
        # Use flow style for lists
        return self.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


CustomDumper.add_representer(list, CustomDumper.represent_list)


class SchemaToRosParamConverter:
    """Converts JSON Schema to ROS parameter YAML files."""

    def __init__(
        self,
        schema_path: Path,
        output_dir: Path,
        package_name: Optional[str] = None,
    ) -> None:
        self.schema_path = schema_path
        self.output_dir = output_dir
        self.package_name = package_name
        self.logger = logging.getLogger(__name__)

    def process(self) -> bool:
        """
        Process the schema file and generate the config file.

        Returns:
            bool: True if successful, False otherwise.
        """
        try:
            self.logger.info(f"Processing schema file: {self.schema_path}")

            with open(self.schema_path, "r") as f:
                schema_data = json.load(f)

            resolved_schema = self._resolve_refs(schema_data, schema_data)
            defaults = self._extract_defaults_from_resolved_schema(resolved_schema)
            self._save_yaml(defaults)

            return True

        except Exception as e:
            self.logger.error(f"Error processing {self.schema_path}: {e}")
            return False

    def _resolve_refs(
        self, schema_data: Union[Dict[str, Any], List[Any]], root_schema: Dict[str, Any]
    ) -> Union[Dict[str, Any], List[Any]]:
        """Resolve $ref references in the schema."""
        if isinstance(schema_data, dict):
            if "$ref" in schema_data:
                ref_path = schema_data["$ref"]
                # Handle references to external files
                if isinstance(ref_path, str) and not ref_path.startswith("#"):
                    file_path, _, def_path = ref_path.partition("#")
                    
                    # Resolve external file path relative to current schema location
                    external_file_path = self.schema_path.parent / file_path
                    if not external_file_path.exists():
                        self.logger.warning(f"Referenced file not found: {external_file_path}")
                        return schema_data
                        
                    try:
                        with open(external_file_path, "r") as f:
                            external_schema = json.load(f)
                            
                        # If there's a definition path (after #), resolve it
                        if def_path:
                            if def_path.startswith("/definitions/"):
                                def_name = def_path.replace("/definitions/", "")
                                if (
                                    "definitions" in external_schema
                                    and def_name in external_schema["definitions"]
                                ):
                                    resolved = self._resolve_refs(
                                        external_schema["definitions"][def_name], external_schema
                                    )
                                    # Merge any additional properties (excluding $ref)
                                    if isinstance(resolved, dict):
                                        resolved = resolved.copy()
                                        for key, value in schema_data.items():
                                            if key != "$ref":
                                                resolved[key] = value
                                        return resolved
                            else:
                                # Handle other paths if needed, or root reference
                                pass
                        else:
                            # Use the whole external file
                             return self._resolve_refs(external_schema, external_schema)

                    except Exception as e:
                        self.logger.error(f"Error loading referenced file {external_file_path}: {e}")
                        return schema_data

                # Handle internal references
                elif isinstance(ref_path, str) and ref_path.startswith("#/definitions/"):
                    def_name = ref_path.replace("#/definitions/", "")
                    if (
                        "definitions" in root_schema
                        and def_name in root_schema["definitions"]
                    ):
                        resolved = self._resolve_refs(
                            root_schema["definitions"][def_name], root_schema
                        )
                        # Merge any additional properties (excluding $ref)
                        if isinstance(resolved, dict):
                            resolved = resolved.copy()
                            for key, value in schema_data.items():
                                if key != "$ref":
                                    resolved[key] = value
                            return resolved
                return schema_data
            else:
                # Recursively resolve refs in nested objects
                resolved = {}
                for key, value in schema_data.items():
                    resolved[key] = self._resolve_refs(value, root_schema)
                return resolved
        elif isinstance(schema_data, list):
            return [self._resolve_refs(item, root_schema) for item in schema_data]
        else:
            return schema_data

    def _extract_defaults_from_resolved_schema(
        self, resolved_schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract default values from a resolved schema."""
        defaults: Dict[str, Any] = {"/**": {"ros__parameters": {}}}

        # Navigate to the ros__parameters section
        if "properties" in resolved_schema and "/**" in resolved_schema["properties"]:
            root_props = resolved_schema["properties"]["/**"]
            if (
                "properties" in root_props
                and "ros__parameters" in root_props["properties"]
            ):
                ros_params = root_props["properties"]["ros__parameters"]
                param_defaults = self._extract_defaults_from_properties(ros_params)
                defaults["/**"]["ros__parameters"] = param_defaults

        return defaults

    def _extract_defaults_from_properties(
        self, properties_schema: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract default values from properties schema."""
        defaults = {}

        if "properties" in properties_schema:
            for prop_name, prop_data in properties_schema["properties"].items():
                if "default" in prop_data:
                    default_value = prop_data["default"]
                    default_value = self._process_default_value_path(default_value)
                    defaults[prop_name] = default_value
                elif prop_data.get("type") == "object" and "properties" in prop_data:
                    nested_defaults = self._extract_defaults_from_properties(prop_data)
                    if nested_defaults:
                        defaults[prop_name] = nested_defaults
                elif prop_data.get("type") == "array" and "default" in prop_data:
                    defaults[prop_name] = prop_data["default"]

        return defaults

    def _process_default_value_path(self, value: Any) -> Any:
        """Process string default values that might need package path prefixing."""
        if (
            isinstance(value, str)
            and self.package_name
            and not value.startswith("/")
            and not value.startswith("$(")
            and (
                "/" in value
                or value.endswith((".yaml", ".json", ".pcd", ".onnx", ".xml"))
            )
        ):
            return f"$(find-pkg-share {self.package_name})/{value}"
        return value

    def _save_yaml(self, data: Dict[str, Any]) -> None:
        """Save the data to a YAML file."""
        schema_filename = self.schema_path.stem
        # Remove .schema from the filename if present (e.g. name.schema -> name)
        if schema_filename.endswith(".schema"):
            schema_filename = schema_filename[:-7]
            
        output_filename = f"{schema_filename}.param.yaml"
        output_path = self.output_dir / output_filename

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            yaml.dump(
                data,
                f,
                Dumper=CustomDumper,
                default_flow_style=False,
                sort_keys=False,
                indent=2,
            )
        self.logger.info(f"Generated config file: {output_path}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    
    parser = argparse.ArgumentParser(
        description="Generate config files from JSON schema files"
    )
    parser.add_argument("schema_dir", help="Directory containing schema files")
    parser.add_argument(
        "output_dir", help="Output directory for generated config files"
    )
    parser.add_argument(
        "--package-name", help="Package name to prefix relative paths with"
    )

    args = parser.parse_args()

    schema_dir = Path(args.schema_dir)
    output_dir = Path(args.output_dir)

    if not schema_dir.exists():
        logging.error(f"Schema directory does not exist: {schema_dir}")
        sys.exit(1)

    schema_files = list(schema_dir.glob("*.schema.json"))

    if not schema_files:
        logging.error(f"No schema files found in: {schema_dir}")
        sys.exit(1)

    logging.info(f"Found {len(schema_files)} schema file(s)")
    if args.package_name:
        logging.info(f"Using package name: {args.package_name}")

    success_count = 0
    for schema_file in schema_files:
        converter = SchemaToRosParamConverter(
            schema_file, output_dir, args.package_name
        )
        if converter.process():
            success_count += 1

    logging.info(
        f"Successfully processed {success_count}/{len(schema_files)} schema files"
    )

    if success_count != len(schema_files):
        sys.exit(1)


if __name__ == "__main__":
    main()
