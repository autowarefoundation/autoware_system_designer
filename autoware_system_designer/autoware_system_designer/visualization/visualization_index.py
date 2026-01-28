import fcntl
import os
from pathlib import Path
import logging
from ..utils.template_utils import TemplateRenderer
from ..utils.source_location import SourceLocation, format_source

logger = logging.getLogger(__name__)

def get_install_root(path: Path) -> Path:
    """
    Find the install root directory from a given path.
    Assumes the path is somewhere inside an 'install' directory.
    """
    path = path.resolve()
    parts = path.parts

    # Look for 'install' in the path from right to left
    # This handles cases where the workspace itself might be in a directory named 'install'
    # But typically we want the one that structures the package layout.
    # Standard layout: .../install/<pkg>/share/<pkg>/...
    # or .../install/share/<pkg>/...

    # We'll assume the 'install' directory we care about is the one closest to the workspace root
    # but strictly speaking, we just need *a* common root to place the index.

    # Simple heuristic: search for 'install'
    if 'install' in parts:
        # Find the index of 'install'
        # If there are multiple, we probably want the one that is part of the current build workspace
        # usually the last one?
        # /home/user/workspace/install/pkg/... -> index is len-3
        # If /home/user/install/workspace/install/pkg -> we want the last one.

        try:
            # finding the last occurrence of 'install'
            idx = len(parts) - 1 - parts[::-1].index('install')
            return Path(*parts[:idx+1])
        except ValueError:
            pass

    return None

def update_index(output_root_dir: str):
    """
    Update the systems.html index file in the install root.
    Uses file locking to safely handle concurrent builds.
    """
    output_path = Path(output_root_dir).resolve()
    install_root = get_install_root(output_path)

    if not install_root or not install_root.exists():
        src = SourceLocation(file_path=Path(output_root_dir))
        logger.warning(
            f"Could not determine install root from {output_root_dir}. Skipping index update.{format_source(src)}"
        )
        return

    index_file = install_root / "systems.html"
    lock_file = install_root / ".systems_index.lock"

    # Ensure we can write to lock file
    try:
        with open(lock_file, 'w') as lock:
            try:
                # Acquire exclusive lock
                fcntl.flock(lock, fcntl.LOCK_EX)

                # Now we have the lock, regenerate the index
                _generate_index_file(install_root, index_file)

            finally:
                # Release lock
                fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception as e:
        src = SourceLocation(file_path=Path(output_root_dir))
        logger.error(f"Failed to update visualization index: {e}{format_source(src)}")


def _generate_index_file(install_root: Path, output_file: Path):
    deployments = []

    # Walk through the install directory to find deployments
    # We scan specifically for our known structure to avoid false positives
    # Look for visualization directories and check what data files they contain
    deployment_map = {}

    for visualization_dir in install_root.rglob("visualization"):
        try:
            # Expected path structure:
            # .../exports/<system_name>/visualization/

            if len(visualization_dir.parts) < 5:
                continue

            if visualization_dir.parts[-3] == 'exports':
                deployment_dir_name = visualization_dir.parts[-2]
                package_name = visualization_dir.parts[-4]  # .../share/<pkg>/exports/...

                web_dir = visualization_dir / "web"
                data_dir = web_dir / "data"

                if not web_dir.exists() or not data_dir.exists():
                    continue

                # Skip if we've already processed this deployment
                deployment_key = f"{package_name}:{deployment_dir_name}"
                if deployment_key in deployment_map:
                    continue

                # Find all available diagram types based on data files
                diagram_types = set()

                # Discover diagram types dynamically by looking for data files
                # Pattern: <mode>_<diagram_type>.js
                for data_file in data_dir.glob("*.js"):
                    if data_file.name.endswith('.js'):
                        # Extract diagram type from filename (remove mode prefix and .js extension)
                        parts = data_file.stem.split('_')
                        if len(parts) >= 2:
                            diagram_type = '_'.join(parts[1:])  # Everything after the first underscore
                            diagram_types.add(diagram_type)

                # If no diagram types found, skip this deployment
                if not diagram_types:
                    continue

                # Create a reference path - use the web directory as base
                # The actual path will be constructed in the HTML generation based on availability
                rel_path = web_dir.relative_to(install_root)

                deployment_map[deployment_key] = {
                    'name': deployment_dir_name,
                    'package': package_name,
                    'path': rel_path,
                    'diagram_types': sorted(list(diagram_types))
                }
        except (IndexError, ValueError):
            continue

    # Convert map to list
    deployments.extend(deployment_map.values())

    # Sort by package then system name
    deployments.sort(key=lambda x: (x['package'], x['name']))

    # Prepare data for template
    view_deployments = []
    for dep in deployments:
        web_path = dep['path']
        deployment_overview_path = web_path / f"{dep['name']}_overview.html"

        main_link = f"{deployment_overview_path}?diagram={dep['diagram_types'][0]}"

        diagrams = []
        for diagram_type in dep['diagram_types']:
            diagram_label = diagram_type.replace('_', ' ').title()
            diagram_link = f"{deployment_overview_path}?diagram={diagram_type}"
            diagrams.append({
                'label': diagram_label,
                'link': diagram_link,
                'type': diagram_type
            })

        view_deployments.append({
            'name': dep['name'],
            'package': dep['package'],
            'main_link': main_link,
            'diagrams': diagrams
        })

    # Render template
    try:
        renderer = TemplateRenderer()
        renderer.render_template_to_file(
            "visualization/systems_index.html.jinja2",
            str(output_file),
            deployments=view_deployments
        )
    except Exception as e:
        logger.error(f"Failed to render visualization index template: {e}")
