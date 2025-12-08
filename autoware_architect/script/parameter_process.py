#!/usr/bin/env python3

import json
import yaml
import os
import sys
import argparse
from pathlib import Path


def extract_default_values(schema_data, path=""):
    """
    Recursively extract default values from JSON schema.
    
    Args:
        schema_data: JSON schema object or property
        path: Current path in the schema (for debugging)
    
    Returns:
        Dictionary with default values
    """
    defaults = {}
    
    if isinstance(schema_data, dict):
        # Handle definitions references
        if "$ref" in schema_data:
            ref_path = schema_data["$ref"]
            if ref_path.startswith("#/definitions/"):
                # For now, we'll skip ref resolution as it requires more complex handling
                return defaults
        
        # Handle object properties
        if "properties" in schema_data:
            for prop_name, prop_data in schema_data["properties"].items():
                if "default" in prop_data:
                    defaults[prop_name] = prop_data["default"]
                elif prop_data.get("type") == "object":
                    nested_defaults = extract_default_values(prop_data, f"{path}.{prop_name}")
                    if nested_defaults:
                        defaults[prop_name] = nested_defaults
                elif prop_data.get("type") == "array" and "default" in prop_data:
                    defaults[prop_name] = prop_data["default"]
        
        # Handle definitions
        if "definitions" in schema_data:
            for def_name, def_data in schema_data["definitions"].items():
                def_defaults = extract_default_values(def_data, f"{path}.definitions.{def_name}")
                if def_defaults:
                    defaults.update(def_defaults)
    
    return defaults


def resolve_refs(schema_data, root_schema):
    """
    Resolve $ref references in the schema.
    
    Args:
        schema_data: Current schema object
        root_schema: Root schema object containing definitions
    
    Returns:
        Resolved schema object
    """
    if isinstance(schema_data, dict):
        if "$ref" in schema_data:
            ref_path = schema_data["$ref"]
            if ref_path.startswith("#/definitions/"):
                def_name = ref_path.replace("#/definitions/", "")
                if "definitions" in root_schema and def_name in root_schema["definitions"]:
                    resolved = resolve_refs(root_schema["definitions"][def_name], root_schema)
                    # Merge any additional properties (excluding $ref)
                    for key, value in schema_data.items():
                        if key != "$ref":
                            resolved[key] = value
                    return resolved
            return schema_data
        else:
            # Recursively resolve refs in nested objects
            resolved = {}
            for key, value in schema_data.items():
                resolved[key] = resolve_refs(value, root_schema)
            return resolved
    elif isinstance(schema_data, list):
        return [resolve_refs(item, root_schema) for item in schema_data]
    else:
        return schema_data


def extract_defaults_from_resolved_schema(resolved_schema, package_name=None):
    """
    Extract default values from a resolved schema.
    
    Args:
        resolved_schema: Resolved JSON schema
        package_name: Package name to prefix relative paths with
    
    Returns:
        Dictionary with default values in ROS parameter format
    """
    defaults = {"/**": {"ros__parameters": {}}}
    
    # Navigate to the ros__parameters section
    if "properties" in resolved_schema and "/**" in resolved_schema["properties"]:
        root_props = resolved_schema["properties"]["/**"]
        if "properties" in root_props and "ros__parameters" in root_props["properties"]:
            ros_params = root_props["properties"]["ros__parameters"]
            
            # Extract defaults from ros__parameters
            param_defaults = extract_defaults_from_properties(ros_params, package_name)
            defaults["/**"]["ros__parameters"] = param_defaults
    
    return defaults


def extract_defaults_from_properties(properties_schema, package_name=None):
    """
    Extract default values from properties schema.
    
    Args:
        properties_schema: Properties section of JSON schema
        package_name: Package name to prefix relative paths with
    
    Returns:
        Dictionary with default values
    """
    defaults = {}
    
    if "properties" in properties_schema:
        for prop_name, prop_data in properties_schema["properties"].items():
            if "default" in prop_data:
                default_value = prop_data["default"]
                # Check if default is a string that looks like a relative path
                if (isinstance(default_value, str) and 
                    package_name and 
                    not default_value.startswith('/') and 
                    not default_value.startswith('$(') and
                    ('/' in default_value or default_value.endswith(('.yaml', '.json', '.pcd', '.onnx', '.xml')))):
                    default_value = f"$(find-pkg-share {package_name})/{default_value}"
                defaults[prop_name] = default_value
            elif prop_data.get("type") == "object" and "properties" in prop_data:
                nested_defaults = extract_defaults_from_properties(prop_data, package_name)
                if nested_defaults:
                    defaults[prop_name] = nested_defaults
            elif prop_data.get("type") == "array" and "default" in prop_data:
                defaults[prop_name] = prop_data["default"]
    
    return defaults


def process_schema_file(schema_file_path, output_dir, package_name=None):
    """
    Process a single schema file and generate corresponding config file.
    
    Args:
        schema_file_path: Path to the JSON schema file
        output_dir: Output directory for generated config files
        package_name: Package name to prefix relative paths with
    
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        print(f"Processing schema file: {schema_file_path}")
        
        with open(schema_file_path, 'r') as f:
            schema_data = json.load(f)
        
        # Resolve all $ref references
        resolved_schema = resolve_refs(schema_data, schema_data)
        
        # Extract default values
        defaults = extract_defaults_from_resolved_schema(resolved_schema, package_name)
        
        # Generate output filename
        schema_filename = Path(schema_file_path).stem
        # Remove .schema from the filename if present
        if schema_filename.endswith('.schema'):
            schema_filename = schema_filename[:-7]  # Remove .schema
        output_filename = f"{schema_filename}.param.yaml"
        output_path = Path(output_dir) / output_filename
        
        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write YAML config file with custom formatting
        with open(output_path, 'w') as f:
            # Use custom YAML dumper to format arrays in flow style
            class CustomDumper(yaml.SafeDumper):
                def write_line_break(self, data=None):
                    super().write_line_break(data)
                    if len(self.indents) == 1:
                        super().write_line_break()
                        
                def represent_list(self, data):
                    # Use flow style for lists
                    return self.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=True)
            
            CustomDumper.add_representer(list, CustomDumper.represent_list)
            
            yaml.dump(defaults, f, Dumper=CustomDumper, default_flow_style=False, sort_keys=False, indent=2)
        
        print(f"Generated config file: {output_path}")
        return True
        
    except Exception as e:
        print(f"Error processing {schema_file_path}: {str(e)}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Generate config files from JSON schema files')
    parser.add_argument('schema_dir', help='Directory containing schema files')
    parser.add_argument('output_dir', help='Output directory for generated config files')
    parser.add_argument('--package-name', help='Package name to prefix relative paths with')
    
    args = parser.parse_args()
    
    schema_dir = Path(args.schema_dir)
    output_dir = Path(args.output_dir)
    package_name = args.package_name
    
    if not schema_dir.exists():
        print(f"Error: Schema directory does not exist: {schema_dir}")
        sys.exit(1)
    
    # Find all JSON schema files
    schema_files = list(schema_dir.glob("*.schema.json"))
    
    if not schema_files:
        print(f"No schema files found in: {schema_dir}")
        sys.exit(1)
    
    print(f"Found {len(schema_files)} schema file(s)")
    if package_name:
        print(f"Using package name: {package_name}")
    
    success_count = 0
    for schema_file in schema_files:
        if process_schema_file(schema_file, output_dir, package_name):
            success_count += 1
    
    print(f"Successfully processed {success_count}/{len(schema_files)} schema files")
    
    if success_count != len(schema_files):
        sys.exit(1)


if __name__ == "__main__":
    main()