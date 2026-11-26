import argparse
import glob
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

# Directory markers that exclude a subtree from the scan, matching colcon semantics.
IGNORE_MARKERS = ("COLCON_IGNORE", "AMENT_IGNORE")

# libyaml-backed loader when PyYAML is built with it.
_SafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


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


AMENT_PACKAGES_INDEX = os.path.join("share", "ament_index", "resource_index", "packages")


def install_prefixes(install_base, is_isolated):
    """Install prefixes of the workspace: one per package when isolated, the install base when merged."""
    if not is_isolated:
        return [install_base]
    try:
        entries = sorted(os.listdir(install_base))
    except OSError:
        return []
    return [os.path.join(install_base, entry) for entry in entries]


def indexed_packages(prefixes):
    """Share directories of packages registered in the ament resource index; earlier prefixes win."""
    packages = {}
    for prefix in prefixes:
        index_dir = os.path.join(prefix, AMENT_PACKAGES_INDEX)
        try:
            names = sorted(os.listdir(index_dir))
        except OSError:
            continue
        for name in names:
            packages.setdefault(name, os.path.join(prefix, "share", name))
    return packages


def parse_design_file(filepath):
    try:
        with open(filepath, "r") as file:
            content = yaml.load(file, Loader=_SafeLoader)
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


def layer_root(start_path, workspace_root):
    """Top-level workspace directory holding start_path; None outside the workspace or at its root."""
    if not workspace_root:
        return None
    rel = os.path.relpath(os.path.realpath(start_path), os.path.realpath(workspace_root))
    if rel in (os.curdir, os.pardir) or rel.startswith(os.pardir + os.sep):
        return None
    return os.path.join(os.path.realpath(workspace_root), rel.split(os.sep)[0])


def holds(root, path):
    """True when path is root or inside it, as written or with symbolic links resolved.

    A package linked into src resolves to a target outside the workspace, so the path as written counts too.
    """
    for resolve in (os.path.abspath, os.path.realpath):
        rel = os.path.relpath(resolve(path), resolve(root))
        if rel != os.pardir and not rel.startswith(os.pardir + os.sep):
            return True
    return False


def scan_roots(start_path, *more_roots):
    """Directories to scan, the deploying package first; a root nested in an earlier one is dropped."""
    roots = []
    for root in (start_path, *more_roots):
        if not root:
            continue
        root = os.path.realpath(root)
        if any(root == prev or root.startswith(prev + os.sep) for prev in roots):
            continue
        roots.append(root)
    return roots


def owning_package(path, scan_root, pkg_paths):
    """Name of the innermost package containing path, searched no higher than scan_root."""
    curr = os.path.dirname(path)
    while curr == scan_root or curr.startswith(scan_root + os.sep):
        if curr in pkg_paths:
            return pkg_paths[curr]
        if curr == scan_root:
            break
        curr = os.path.dirname(curr)
    return None


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
    parser.add_argument(
        "--workspace-root",
        default=None,
        help=(
            "Colcon workspace root. The scan covers the deploying package, the top-level workspace "
            "directory holding it, and <workspace-root>/src. Derived from start_path when omitted "
            "or when it does not hold start_path."
        ),
    )
    parser.add_argument(
        "--include-installed",
        action="store_true",
        help=(
            "Also map installed packages outside the scan roots (workspace install, then "
            "AMENT_PREFIX_PATH). Ignored in source mode."
        ),
    )
    args = parser.parse_args()

    anchor = os.path.realpath(args.anchor) if args.anchor else None

    # colcon run from another directory puts the build tree outside the sources, so the given
    # root does not hold the package. The root is then derived from the package path.
    if args.workspace_root and holds(args.workspace_root, args.start_path):
        workspace_root = os.path.realpath(args.workspace_root)
        source_root = os.path.join(workspace_root, "src")
    else:
        if args.workspace_root:
            print(f"Workspace root {args.workspace_root} does not hold {args.start_path}, deriving it from the package")
        source_root = find_source_root(args.start_path)
        workspace_root = os.path.dirname(source_root) if os.path.basename(source_root) == "src" else None
    # Scope of the build: the deploying package, the workspace layer holding it, and the source root.
    roots = scan_roots(args.start_path, layer_root(args.start_path, workspace_root), source_root)
    output_dir = os.path.abspath(args.output_dir)

    pkg_paths = {}
    for root in roots:
        print(f"Scanning: {root}")
        for pkg_dir, pkg_name in find_packages(root).items():
            pkg_paths.setdefault(pkg_dir, pkg_name)
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

    yaml_parse_errors = []
    seen_files = set()

    for root in roots:
        for yaml_file in sorted(glob.glob(os.path.join(root, "**", "*.yaml"), recursive=True)):
            yaml_file_abs = os.path.realpath(yaml_file)
            if yaml_file_abs in seen_files:
                continue
            seen_files.add(yaml_file_abs)

            if not is_target_design_file(os.path.basename(yaml_file_abs)):
                continue

            if is_ignored_path(yaml_file_abs, root):
                continue

            # Check content first (it's a hard requirement)
            try:
                content = parse_design_file(yaml_file_abs)
            except ValueError:
                yaml_parse_errors.append(yaml_file_abs)
                continue
            if not content:
                continue

            target_pkg = owning_package(yaml_file_abs, root, pkg_paths)

            # If it's a node design file, check if it specifies a package
            if "launch" in content and "package" in content["launch"]:
                target_pkg = content["launch"]["package"]

            if not target_pkg:
                continue

            pkg_files.setdefault(target_pkg, []).append(yaml_file_abs)

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
    source_map = {}
    for pkg_src_path, pkg_name in pkg_paths.items():
        source_map.setdefault(pkg_name, to_manifest_path(pkg_src_path, anchor))
        if is_source_mode:
            pkg_path = pkg_src_path
        elif is_isolated:
            # isolated: install_base/pkg_name/share/pkg_name
            pkg_path = os.path.join(install_base, pkg_name, "share", pkg_name)
        else:
            # merged: install_prefix/share/pkg_name
            pkg_path = os.path.join(args.install_prefix, "share", pkg_name)
        package_map[pkg_name] = to_manifest_path(pkg_path, anchor)

    # Opt-in: installed packages outside the scan roots resolve through the ament index,
    # workspace install first, then AMENT_PREFIX_PATH in overlay order.
    if args.include_installed and not is_source_mode:
        prefixes = install_prefixes(install_base, is_isolated)
        prefixes += [p for p in os.environ.get("AMENT_PREFIX_PATH", "").split(os.pathsep) if p]
        for pkg_name, pkg_path in indexed_packages(prefixes).items():
            package_map.setdefault(pkg_name, to_manifest_path(pkg_path, anchor))

    package_map_path = os.path.join(output_dir, "_package_map.yaml")
    # source_map: source directory of each scanned package, read when its install share lacks a file.
    package_map_data = {"package_map": package_map, "source_map": source_map}
    if anchor:
        package_map_data["workspace_root"] = anchor
    with open(package_map_path, "w") as manifest_file:
        yaml.dump(package_map_data, manifest_file)
        print(f"Generated {package_map_path} with {len(package_map)} packages")

    # 2. Generate individual manifests for packages with design files
    for pkg, files in pkg_files.items():
        manifest_path = os.path.join(output_dir, f"{pkg}.yaml")

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
