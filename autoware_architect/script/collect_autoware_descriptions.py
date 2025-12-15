import os
import argparse
import glob
import yaml
import xml.etree.ElementTree as ET
from pathlib import Path

def get_package_name(path):
    xml_path = os.path.join(path, 'package.xml')
    if not os.path.exists(xml_path):
        return None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        name = root.find('name').text
        return name
    except:
        return None

def find_packages(root_dir):
    packages = {}
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if 'package.xml' in filenames:
            name = get_package_name(dirpath)
            if name:
                packages[os.path.abspath(dirpath)] = name
            # Optimization: don't traverse inside packages unless they are metapackages?
            # ROS 2 packages usually don't nest.
            # But let's be safe and traverse.
    return packages

def check_file_for_description(filepath):
    try:
        with open(filepath, 'r') as f:
            content = yaml.safe_load(f)
            if content and isinstance(content, dict) and 'autoware_description_format' in content:
                return True
    except:
        pass
    return False

def infer_type(filename):
    if filename.endswith('.node.yaml'):
        return 'node'
    elif filename.endswith('.module.yaml'):
        return 'module'
    elif filename.endswith('system.yaml'):
        return 'system'
    elif filename.endswith('parameter_set.yaml'):
        return 'parameter_set'
    return 'unknown'

def find_source_root(start_path):
    """
    Find the workspace source root directory by traversing up from start_path.
    Heuristics:
    1. Look for 'src' directory in the path components.
    2. Look for sibling 'build'/'install' directories.
    """
    path = Path(start_path).resolve()
    
    # 1. Look for 'src' in the path components
    # We want the directory containing 'src' (workspace root) or the 'src' directory itself?
    # The previous logic used 'src' as the root to scan.
    # Usually we want to scan the 'src' directory.
    if 'src' in path.parts:
        # Return the path ending with 'src'
        # Example: /home/user/ws/src/pkg -> /home/user/ws/src
        idx = path.parts.index('src')
        return str(Path(*path.parts[:idx+1]))

    # 2. Fallback: traverse up and look for sibling 'build'/'install' directories
    # If we find them, return the 'src' directory inside that root if it exists.
    # Or just return the root.
    curr = path
    while len(curr.parts) > 1: # Don't go to root /
        if (curr / "build").exists() and (curr / "install").exists():
             if (curr / "src").exists():
                 return str(curr / "src")
             return str(curr)
        curr = curr.parent

    # 3. Last resort: return start_path
    return str(path)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('start_path', help='Path to start searching for workspace root (usually current package dir)')
    parser.add_argument('output_dir', help='Directory to save manifests')
    parser.add_argument('install_prefix', help='CMAKE_INSTALL_PREFIX')
    args = parser.parse_args()

    # Find workspace root
    workspace_root = find_source_root(args.start_path)
    output_dir = os.path.abspath(args.output_dir)

    print(f"Scanning workspace: {workspace_root}")
    pkg_paths = find_packages(workspace_root)
    print(f"Found {len(pkg_paths)} packages")

    pkg_files = {}

    # Glob all yaml files
    # Using recursive glob
    yaml_files = glob.glob(os.path.join(workspace_root, '**', '*.yaml'), recursive=True)
    
    for yf in yaml_files:
        yf_abs = os.path.abspath(yf)
        
        # Check content first (it's a hard requirement)
        if not check_file_for_description(yf_abs):
            continue
            
        # Find which package it belongs to
        parent = os.path.dirname(yf_abs)
        found_pkg = None
        
        # Traverse up
        curr = parent
        while curr.startswith(workspace_root):
            if curr in pkg_paths:
                found_pkg = pkg_paths[curr]
                break
            if curr == workspace_root:
                break
            curr = os.path.dirname(curr)
        
        if not found_pkg:
            continue
            
        if found_pkg not in pkg_files:
            pkg_files[found_pkg] = []
        pkg_files[found_pkg].append(yf_abs)

    print(f"Found system descriptions in {len(pkg_files)} packages")

    # Write manifests
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    for pkg, files in pkg_files.items():
        manifest_path = os.path.join(output_dir, f"{pkg}.yaml")
        # Assuming shared install: install_prefix/share/pkg_name
        pkg_install_path = os.path.join(args.install_prefix, 'share', pkg)
        
        data = {
            'package_path': pkg_install_path,
            'system_config_files': []
        }
        
        for f in files:
            t = infer_type(os.path.basename(f))
            if t == 'unknown':
                continue
            data['system_config_files'].append({
                'path': f,
                'type': t
            })
            
        with open(manifest_path, 'w') as f:
            yaml.dump(data, f)
            print(f"Generated {manifest_path}")

if __name__ == '__main__':
    main()
