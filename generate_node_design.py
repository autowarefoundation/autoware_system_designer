#!/usr/bin/env python3
import os
import re
import sys
import argparse
import yaml
from pathlib import Path

def parse_package_xml(package_path):
    xml_path = os.path.join(package_path, "package.xml")
    if not os.path.exists(xml_path):
        return None
    with open(xml_path, 'r') as f:
        content = f.read()
    match = re.search(r'<name>(.*?)</name>', content)
    if match:
        return match.group(1)
    return None

def infer_type(value_str):
    value_str = value_str.strip()
    if value_str in ['true', 'false']:
        return 'bool'
    # Only treat as string if explicitly quoted
    if value_str.startswith('"') and value_str.endswith('"'):
        return 'string'
    if '.' in value_str:
        try:
            float(value_str)
            return 'double'
        except ValueError:
            pass
    if value_str.isdigit():
        return 'int'
    
    # If it's a C++ variable (no quotes, not a number), we can't infer the value
    return None 

def clean_cpp_type(cpp_type):
    return cpp_type.replace("::", "/")

class TypeResolver:
    def __init__(self):
        self.aliases = {}

    def parse_content(self, content):
        using_pattern = r'using\s+([a-zA-Z0-9_]+)\s*=\s*([a-zA-Z0-9_:]+);'
        for match in re.finditer(using_pattern, content):
            self.aliases[match.group(1)] = match.group(2)
        
        using_direct_pattern = r'using\s+([a-zA-Z0-9_:]+::([a-zA-Z0-9_]+));'
        for match in re.finditer(using_direct_pattern, content):
            self.aliases[match.group(2)] = match.group(1)

    def resolve(self, type_name):
        if "::" in type_name:
            parts = type_name.split("::")
            if parts[0] in self.aliases:
                resolved_base = self.aliases[parts[0]]
                return clean_cpp_type(f"{resolved_base}::{'::'.join(parts[1:])}")

        if type_name in self.aliases:
            return clean_cpp_type(self.aliases[type_name])
        
        return clean_cpp_type(type_name)

def get_logical_name(topic):
    if "FIXME" in topic:
        return topic
    parts = topic.strip('/').split('/')
    if not parts:
        return topic
    return parts[-1]

def make_unique_name(items, name, topic):
    existing_names = [item['name'] for item in items]
    if name not in existing_names:
        return name
    
    parts = topic.strip('/').split('/')
    if len(parts) >= 2:
        new_name = f"{parts[-2]}_{parts[-1]}"
        if new_name not in existing_names:
            return new_name
    
    idx = 1
    while f"{name}_{idx}" in existing_names:
        idx += 1
    return f"{name}_{idx}"

def extract_variable_value(content, var_name):
    pattern = r'(?:const\s+)?(?:std::)?string\s+' + re.escape(var_name) + r'\s*=\s*"([^"]+)"'
    match = re.search(pattern, content)
    if match:
        return match.group(1)
    return None

def resolve_topic_name(topic_arg, content):
    """Helper to handle literal strings vs variables."""
    topic_arg = topic_arg.strip()
    topic_name = "FIXME_VARIABLE_TOPIC"
    
    # 1. Direct String Literal
    if topic_arg.startswith('"') and topic_arg.endswith('"'):
        return topic_arg[1:-1]
    
    # 2. String Concatenation (simple case)
    if "+" in topic_arg:
        parts = topic_arg.split("+")
        first_part = parts[0].strip()
        if first_part.startswith('"') and first_part.endswith('"'):
            # "input/" + var -> "input/VAR"
            return f"{first_part[1:-1]}/VAR"
        else:
            val = extract_variable_value(content, first_part)
            if val:
                return f"{val}/VAR"

    # 3. Variable Lookup
    val = extract_variable_value(content, topic_arg)
    if val:
        return val
        
    return topic_name

def analyze_file(file_path, package_name, resolver):
    with open(file_path, 'r') as f:
        content = f.read()

    resolver.parse_content(content)

    comp_match = re.search(r'RCLCPP_COMPONENTS_REGISTER_NODE\s*\(\s*([a-zA-Z0-9_:]+)\s*\)', content)
    if not comp_match:
        return None

    full_class_name = comp_match.group(1)
    class_name = full_class_name.split("::")[-1]

    node_data = {
        "name": f"{class_name}.node",
        "type": "node",
        "launch": {
            "package": package_name,
            "plugin": full_class_name
        },
        "inputs": [],
        "outputs": [],
        "parameters": [],
        "processes": []
    }

    # Subscribers
    sub_pattern = r'create_subscription\s*<\s*([^>]+)\s*>\s*\(\s*([^,]+)'
    for match in re.finditer(sub_pattern, content, re.DOTALL):
        msg_type_raw = match.group(1).strip()
        msg_type = resolver.resolve(msg_type_raw)
        topic_arg = match.group(2).strip()
        
        topic_name = resolve_topic_name(topic_arg, content)
        
        logical_name = get_logical_name(topic_name)
        logical_name = make_unique_name(node_data["inputs"], logical_name, topic_name)
        
        item = {"name": logical_name, "message_type": msg_type}
        
        # Remapping Logic
        if "FIXME" in topic_name or "/VAR" in topic_name:
             item["remap_target"] = topic_name
        elif not (topic_name == f"~/input/{logical_name}" or topic_name == f"input/{logical_name}" or topic_name.endswith(f"input/{logical_name}")):
             item["remap_target"] = topic_name
        
        node_data["inputs"].append(item)

    # Publishers
    pub_pattern = r'create_publisher\s*<\s*([^>]+)\s*>\s*\(\s*([^,]+)'
    for match in re.finditer(pub_pattern, content, re.DOTALL):
        msg_type_raw = match.group(1).strip()
        msg_type = resolver.resolve(msg_type_raw)
        topic_arg = match.group(2).strip()
        
        topic_name = resolve_topic_name(topic_arg, content)
        
        logical_name = get_logical_name(topic_name)
        logical_name = make_unique_name(node_data["outputs"], logical_name, topic_name)
        
        item = {"name": logical_name, "message_type": msg_type}
        
        # Remapping Logic
        if "FIXME" in topic_name or "/VAR" in topic_name:
             item["remap_target"] = topic_name
        elif not (topic_name == f"~/output/{logical_name}" or topic_name.endswith(f"output/{logical_name}")):
             item["remap_target"] = topic_name
        
        node_data["outputs"].append(item)

    # Parameters
    # Improved Regex: Capture until ) or , but be careful about defaults
    param_pattern = r'declare_parameter\s*(?:<\s*([^>]+)\s*>)?\s*\(\s*"([^"]+)"\s*(?:,\s*([^,)]+))?'
    for match in re.finditer(param_pattern, content, re.DOTALL):
        p_type_explicit = match.group(1)
        p_name = match.group(2)
        p_val_raw = match.group(3)
        
        p_type = "string" # Default fallback
        p_val = None
        
        if p_val_raw:
            p_val_raw = p_val_raw.strip()
            inferred = infer_type(p_val_raw)
            if inferred:
                p_type = inferred
                if p_type == 'string':
                    p_val = p_val_raw[1:-1]
                elif p_type == 'double':
                    try: p_val = float(p_val_raw)
                    except: pass
                elif p_type == 'int':
                    try: p_val = int(p_val_raw)
                    except: pass
                elif p_type == 'bool':
                    p_val = (p_val_raw == 'true')
            else:
                # It's a C++ variable or function call, set default to None
                p_val = None
                p_type = "string" # Default to string for safety
        
        if p_type_explicit:
            p_type_explicit = p_type_explicit.strip()
            type_map = {'std::string': 'string', 'double': 'double', 'float': 'double', 'int': 'int', 'bool': 'bool'}
            p_type = type_map.get(p_type_explicit, p_type_explicit)

        item = {"name": p_name, "default": p_val, "type": p_type}
        node_data["parameters"].append(item)

    return node_data

def main():
    parser = argparse.ArgumentParser(description="Generate Autoware Node Design")
    parser.add_argument("--package-path", required=True, help="Path to ROS 2 package")
    parser.add_argument("--output-dir", help="Output directory")
    
    args = parser.parse_args()
    package_path = args.package_path
    package_name = parse_package_xml(package_path)
    if not package_name:
        package_name = "unknown_package"

    output_dir = args.output_dir if args.output_dir else os.path.join(package_path, "design")
    os.makedirs(output_dir, exist_ok=True)

    resolver = TypeResolver()
    # Scan headers first to build alias map
    for root, dirs, files in os.walk(package_path):
        for file in files:
            if file.endswith(".hpp") or file.endswith(".h"):
                try:
                    with open(os.path.join(root, file), 'r', errors='ignore') as f:
                        resolver.parse_content(f.read())
                except: pass

    processed_nodes = {}
    search_dirs = ["src", "include"]
    for sub_dir in search_dirs:
        dir_path = os.path.join(package_path, sub_dir)
        if not os.path.exists(dir_path): continue

        for root, dirs, files in os.walk(dir_path):
            for file in files:
                if file.endswith(".cpp"):
                    file_path = os.path.join(root, file)
                    try:
                        node_data = analyze_file(file_path, package_name, resolver)
                        if node_data:
                            plugin = node_data["launch"]["plugin"]
                            if plugin not in processed_nodes:
                                processed_nodes[plugin] = node_data
                    except Exception as e:
                        print(f"Error parsing {file}: {e}")

    for plugin, data in processed_nodes.items():
        out_filename = f"{data['name']}.yaml"
        out_path = os.path.join(output_dir, out_filename)
        with open(out_path, 'w') as f:
            yaml.dump(data, f, sort_keys=False)
        print(f"Generated: {out_path}")

    print(f"Processed {len(processed_nodes)} nodes.")

if __name__ == "__main__":
    main()