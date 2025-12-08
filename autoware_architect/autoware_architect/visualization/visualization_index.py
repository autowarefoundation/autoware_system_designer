import fcntl
import os
from pathlib import Path
import logging

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
    Update the architecture_visualization.html index file in the install root.
    Uses file locking to safely handle concurrent builds.
    """
    output_path = Path(output_root_dir).resolve()
    install_root = get_install_root(output_path)

    if not install_root or not install_root.exists():
        logger.warning(f"Could not determine install root from {output_root_dir}. Skipping index update.")
        return

    index_file = install_root / "architecture_visualization.html"
    lock_file = install_root / ".architecture_visualization_index.lock"

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
        logger.error(f"Failed to update visualization index: {e}")


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

    # Generate HTML
    html_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Autoware Architecture Deployments</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f4f4f4;
            color: #333;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            color: #2c3e50;
            border-bottom: 2px solid #eee;
            padding-bottom: 10px;
        }
        .description {
            color: #666;
            margin-bottom: 30px;
        }
        .deployment-list {
            list-style-type: none;
            padding: 0;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        .deployment-item {
            background: #fff;
            border: 1px solid #e0e0e0;
            border-radius: 6px;
            transition: transform 0.2s, box-shadow 0.2s;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        .deployment-item:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.05);
            border-color: #b0b0b0;
        }
        .deployment-header {
            padding: 20px 20px 15px 20px;
        }
        .diagram-buttons {
            display: flex;
            gap: 10px;
            padding: 0 20px 20px 20px;
            border-top: 1px solid #f0f0f0;
            background: #fafafa;
        }
        .diagram-button {
            text-decoration: none;
            color: #0066cc;
            background: #f8f9fa;
            border: 1px solid #dee2e6;
            border-radius: 4px;
            padding: 8px 12px;
            font-size: 0.9em;
            font-weight: 500;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .diagram-button:hover {
            background: #e9ecef;
            border-color: #adb5bd;
            color: #0056b3;
        }
        .diagram-icon {
            display: flex;
            align-items: center;
        }
        .deployment-name {
            font-weight: bold;
            font-size: 1.1em;
            color: #0066cc;
            margin-bottom: 10px;
            word-break: break-word;
            text-decoration: none;
        }
        .deployment-name:hover {
            color: #0056b3;
        }
        .deployment-meta {
            color: #666;
            font-size: 0.9em;
            margin-bottom: 10px;
        }
        .deployment-package {
            display: inline-block;
            background: #f0f0f0;
            padding: 2px 6px;
            border-radius: 4px;
            font-family: monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>Autoware Architecture Deployments</h1>
        <p class="description">Index of available system architecture visualizations found in the <code>install/</code> directory.</p>
        <ul class="deployment-list">
"""

    for dep in deployments:
        web_path = dep['path']  # This is now the web directory path
        deployment_overview_path = web_path / f"{dep['name']}_overview.html"

        html_content += f"""
            <li class="deployment-item">
                <div class="deployment-header">"""
        main_link = f"{deployment_overview_path}?diagram={dep['diagram_types'][0]}"
        html_content += f"""
                    <a href="{main_link}" class="deployment-name">{dep['name']}</a>
                    <div class="deployment-meta">Package: <span class="deployment-package">{dep['package']}</span></div>
                </div>
                <div class="diagram-buttons">"""

        # Show diagram type buttons
        for diagram_type in dep['diagram_types']:
            diagram_label = diagram_type.replace('_', ' ').title()
            diagram_link = f"{deployment_overview_path}?diagram={diagram_type}"

            html_content += f"""
                    <a class="diagram-button" href="{diagram_link}" data-type="{diagram_type}">
                        <span class="diagram-icon">{diagram_label}</span>
                    </a>"""

        html_content += """
                </div>
            </li>"""

    html_content += """
        </ul>
    </div>
</body>
</html>
"""

    with open(output_file, 'w') as f:
        f.write(html_content)
