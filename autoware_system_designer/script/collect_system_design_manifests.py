import argparse
import glob
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

# Directory markers that exclude a subtree from the scan, matching colcon semantics.
IGNORE_MARKERS = ("COLCON_IGNORE", "AMENT_IGNORE")


def is_ignored_path(path, root_dir):
    """A path is ignored when any directory between root_dir and it holds an ignore marker."""
    current = os.path.realpath(os.path.dirname(path) if os.path.isfile(path) else path)
    root = os.path.realpath(root_dir)
    while current.startswith(root):
        if any(os.path.exists(os.path.join(current, marker)) for marker in IGNORE_MARKERS):
            return True
        if current == root:
            break
        current = os.path.dirname(current)
    return False


def get_package_name(path):
    xml_path = os.path.join(path, "package.xml")
    if not os.path.exists(xml_path):
        return None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        name = root.find("name").text
        return name
    except:
        return None


def find_packages(root_dir):
    packages = {}
    for dirpath, dirnames, filenames in os.walk(root_dir):
        if any(marker in filenames for marker in IGNORE_MARKERS):
            dirnames.clear()
            continue
        if "package.xml" in filenames:
            name = get_package_name(dirpath)
            if name:
                packages[os.path.realpath(dirpath)] = name
            # Optimization: don't traverse inside packages unless they are metapackages?
            # ROS 2 packages usually don't nest.
            # But let's be safe and traverse.
    return packages


def parse_design_file(filepath):
    try:
        with open(filepath, "r") as file:
            content = yaml.safe_load(file)
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError(f"Failed to parse YAML file: {filepath}") from exc
    except OSError as exc:
        raise ValueError(f"Failed to read YAML file: {filepath}") from exc

    if content and isinstance(content, dict) and "autoware_system_design_format" in content:
        return content
    return None


def infer_type(filename):
    if filename.endswith(".node.yaml"):
        return "node"
    elif filename.endswith(".module.yaml"):
        return "module"
    elif filename.endswith("system.yaml"):
        return "system"
    elif filename.endswith("parameter_set.yaml"):
        return "parameter_set"
    return "unknown"


def is_target_design_file(filename):
    return infer_type(filename) != "unknown"


def to_manifest_path(path, anchor):
    """Manifest entry for a path: anchor-relative when inside the anchor, absolute otherwise.

    Inverse of autoware_system_designer.common.path_utils.resolve_manifest_path; kept local so
    the collector runs stdlib-only at install time.
    """
    if not anchor:
        return path
    try:
        rel = os.path.relpath(path, anchor)
    except ValueError:
        return path
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return path
    return rel


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
    if "src" in path.parts:
        # Return the path ending with 'src'
        # Example: /home/user/ws/src/pkg -> /home/user/ws/src
        idx = path.parts.index("src")
        return str(Path(*path.parts[: idx + 1]))

    # 2. Fallback: traverse up and look for sibling 'build'/'install' directories
    # If we find them, return the 'src' directory inside that root if it exists.
    # Or just return the root.
    curr = path
    while len(curr.parts) > 1:  # Don't go to root /
        if (curr / "build").exists() and (curr / "install").exists():
            if (curr / "src").exists():
                return str(curr / "src")
            return str(curr)
        curr = curr.parent

    # 3. Last resort: return start_path
    return str(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "start_path",
        help="Path to start searching for workspace root (usually current package dir)",
    )
    parser.add_argument("output_dir", help="Directory to save manifests")
    parser.add_argument("install_prefix", help="CMAKE_INSTALL_PREFIX")
    parser.add_argument(
        "--package-map-mode",
        choices=["install", "source"],
        default="install",
        help=(
            "How to generate package_map paths. "
            "'install' uses install_prefix/share/<pkg> (default), "
            "'source' uses workspace source package directories (CI/no-build mode)."
        ),
    )
    parser.add_argument(
        "--anchor",
        default=None,
        help=(
            "Base directory that manifest paths are written relative to, recorded as "
            "'workspace_root' in _package_map.yaml. Absolute paths are written when omitted."
        ),
    )
    args = parser.parse_args()

    anchor = os.path.realpath(args.anchor) if args.anchor else None

    # Find workspace root
    workspace_root = find_source_root(args.start_path)
    output_dir = os.path.abspath(args.output_dir)

    print(f"Scanning workspace: {workspace_root}")
    pkg_paths = find_packages(workspace_root)
    print(f"Found {len(pkg_paths)} packages")

    # Identify the current package name to detect install layout (isolated vs merged)
    current_pkg_name = get_package_name(args.start_path)

    is_source_mode = args.package_map_mode == "source"
    is_isolated = False
    install_base = args.install_prefix
    clean_prefix = args.install_prefix.rstrip(os.path.sep)

    if is_source_mode:
        print("Detected source package_map mode (CI/no-build)")
    elif os.path.basename(clean_prefix) == current_pkg_name:
        is_isolated = True
        install_base = os.path.dirname(clean_prefix)
        print(f"Detected isolated install layout. Base: {install_base}")
    else:
        print(f"Detected merged install layout (or non-standard). Prefix: {install_base}")

    pkg_files = {}

    # Glob all yaml files
    # Using recursive glob
    yaml_files = glob.glob(os.path.join(workspace_root, "**", "*.yaml"), recursive=True)

    yaml_parse_errors = []

    for yaml_file in yaml_files:
        yaml_file_abs = os.path.realpath(yaml_file)

        if not is_target_design_file(os.path.basename(yaml_file_abs)):
            continue

        if is_ignored_path(yaml_file_abs, workspace_root):
            continue

        # Check content first (it's a hard requirement)
        try:
            content = parse_design_file(yaml_file_abs)
        except ValueError:
            yaml_parse_errors.append(yaml_file_abs)
            continue
        if not content:
            continue

        # Find which package it belongs to
        parent = os.path.dirname(yaml_file_abs)
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

        # Determine target package
        target_pkg = found_pkg

        # If it's a node design file, check if it specifies a package
        if "launch" in content and "package" in content["launch"]:
            target_pkg = content["launch"]["package"]

        if not target_pkg:
            continue

        if target_pkg not in pkg_files:
            pkg_files[target_pkg] = []
        pkg_files[target_pkg].append(yaml_file_abs)

    if yaml_parse_errors:
        unique_files = sorted(set(yaml_parse_errors))
        error_list = "\n".join(f"- {path}" for path in unique_files)
        raise RuntimeError(f"Corrupted YAML design files detected:\n{error_list}")

    print(f"Found system descriptions in {len(pkg_files)} packages")

    # Write manifests
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 1. Generate package_map for ALL workspace packages
    package_map = {}
    for pkg_src_path, pkg_name in pkg_paths.items():
        if is_source_mode:
            pkg_path = pkg_src_path
        elif is_isolated:
            # isolated: install_base/pkg_name/share/pkg_name
            pkg_path = os.path.join(install_base, pkg_name, "share", pkg_name)
        else:
            # merged: install_prefix/share/pkg_name
            pkg_path = os.path.join(args.install_prefix, "share", pkg_name)
        package_map[pkg_name] = to_manifest_path(pkg_path, anchor)

    package_map_path = os.path.join(output_dir, "_package_map.yaml")
    package_map_data = {"package_map": package_map}
    if anchor:
        package_map_data["workspace_root"] = anchor
    with open(package_map_path, "w") as manifest_file:
        yaml.dump(package_map_data, manifest_file)
        print(f"Generated {package_map_path} with {len(package_map)} packages")

    # 2. Generate individual manifests for packages with design files
    for pkg, files in pkg_files.items():
        manifest_path = os.path.join(output_dir, f"{pkg}.yaml")

        if is_isolated:
            pkg_install_path = os.path.join(install_base, pkg, "share", pkg)
        else:
            # Assuming shared install: install_prefix/share/pkg_name
            pkg_install_path = os.path.join(args.install_prefix, "share", pkg)

        data = {"package_name": pkg, "deploy_config_files": []}

        for design_file in files:
            design_type = infer_type(os.path.basename(design_file))
            if design_type == "unknown":
                continue
            data["deploy_config_files"].append({"path": to_manifest_path(design_file, anchor), "type": design_type})

        with open(manifest_path, "w") as manifest_file:
            yaml.dump(data, manifest_file)
            print(f"Generated {manifest_path}")


if __name__ == "__main__":
    main()
