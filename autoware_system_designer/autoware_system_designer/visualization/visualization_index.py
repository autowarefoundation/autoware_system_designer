import fcntl
import logging
import os
import shutil
from pathlib import Path

from ..file_io.source_location import SourceLocation, format_source
from ..file_io.template_renderer import TemplateRenderer

logger = logging.getLogger(__name__)


def get_install_root(path: Path) -> Path:
    """Find the nearest enclosing 'install' directory for a path."""
    path = path.resolve()
    parts = path.parts

    if "install" in parts:
        try:
            # Use the last occurrence when multiple 'install' segments exist.
            idx = len(parts) - 1 - parts[::-1].index("install")
            return Path(*parts[: idx + 1])
        except ValueError:
            pass

    return None


def update_index(output_root_dir: str):
    """Update systems.html in install root with file locking."""
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

    try:
        with open(lock_file, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                _generate_index_file(install_root, index_file)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
    except Exception as e:
        src = SourceLocation(file_path=Path(output_root_dir))
        logger.error(f"Failed to update visualization index: {e}{format_source(src)}")


def _copy_shared_assets_to_install_root(install_root: Path) -> None:
    """Copy css/styles.css and js/theme.js to install root for systems.html."""
    pkg_dir = Path(__file__).resolve().parent
    css_src = pkg_dir / "css" / "styles.css"
    theme_src = pkg_dir / "js" / "theme.js"
    css_dest_dir = install_root / "css"
    css_dest_dir.mkdir(parents=True, exist_ok=True)
    if css_src.exists():
        shutil.copy2(css_src, css_dest_dir / "styles.css")
    if theme_src.exists():
        shutil.copy2(theme_src, install_root / "theme.js")


def _generate_index_file(install_root: Path, output_file: Path):
    deployments = []

    # Ensure shared assets are available for systems.html.
    _copy_shared_assets_to_install_root(install_root)

    deployment_map = {}

    for visualization_dir in install_root.rglob("visualization"):
        try:
            if len(visualization_dir.parts) < 5:
                continue

            if visualization_dir.parts[-3] == "exports":
                deployment_dir_name = visualization_dir.parts[-2]
                package_name = visualization_dir.parts[-4]  # .../share/<pkg>/exports/...

                web_dir = visualization_dir / "web"
                data_dir = web_dir / "data"

                if not web_dir.exists() or not data_dir.exists():
                    continue

                deployment_key = f"{package_name}:{deployment_dir_name}"
                if deployment_key in deployment_map:
                    continue

                diagram_types = set()

                for data_file in data_dir.glob("*.js"):
                    if data_file.name.endswith(".js"):
                        parts = data_file.stem.split("_")
                        if len(parts) >= 2:
                            diagram_type = "_".join(parts[1:])
                            diagram_types.add(diagram_type)

                if not diagram_types:
                    continue

                rel_path = web_dir.relative_to(install_root)

                deployment_map[deployment_key] = {
                    "name": deployment_dir_name,
                    "package": package_name,
                    "path": rel_path,
                    "diagram_types": sorted(list(diagram_types)),
                }
        except (IndexError, ValueError):
            continue

    deployments.extend(deployment_map.values())

    deployments.sort(key=lambda x: (x["package"], x["name"]))

    view_deployments = []
    for dep in deployments:
        web_path = dep["path"]
        deployment_overview_path = web_path / f"{dep['name']}_overview.html"

        diagram_types = dep["diagram_types"]
        default_diagram = "node_diagram" if "node_diagram" in diagram_types else diagram_types[0]
        diagram_link = f"{deployment_overview_path}?diagram={default_diagram}"

        web_dir_abs = install_root / web_path
        launch_commands_filename = f"{dep['name']}_launch_commands.html"
        launch_commands_path = web_dir_abs / launch_commands_filename
        launch_commands_link = (
            (web_path / launch_commands_filename).as_posix() if launch_commands_path.exists() else None
        )

        view_deployments.append(
            {
                "name": dep["name"],
                "package": dep["package"],
                "diagram_link": diagram_link,
                "launch_commands_link": launch_commands_link,
            }
        )

    try:
        renderer = TemplateRenderer()
        renderer.render_template_to_file("systems_index.html.jinja2", str(output_file), deployments=view_deployments)
    except Exception as e:
        logger.error(f"Failed to render visualization index template: {e}")
