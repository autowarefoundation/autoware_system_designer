"""Launch file flattening via the vendored launch_unifier.

Callable from generate_system_config.py so the full pipeline can be driven
by a single entry point without needing a separate script invocation.
"""

from __future__ import annotations

import sys
from pathlib import Path

_patches_applied = False


def _ensure_patches() -> None:
    """Apply monkey-patches exactly once before any launch entity is created."""
    global _patches_applied
    if _patches_applied:
        return
    # Make the vendored launch_unifier importable.
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from launch_unifier.patches import apply_patches

    apply_patches()
    _patches_applied = True


def resolve_launch_path(
    package: str | None,
    file_name: str | None,
    launch_path: str | None,
) -> Path:
    """Return the absolute path to a launch file.

    Provide either *launch_path* (absolute/relative path) or both *package*
    and *file_name* (resolved via ament_index).
    """
    if launch_path:
        p = Path(launch_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"Launch file not found: {p}")
        return p
    if package and file_name:
        from ros2launch.api.api import get_share_file_path_from_package

        return Path(get_share_file_path_from_package(package_name=package, file_name=file_name))
    raise ValueError("Provide either --launch-path or (--launch-package + --launch-file).")


def unify_launch(
    launch_file: Path,
    launch_arguments: list[tuple[str, str]],
    output_dir: Path,
    debug: bool = False,
) -> Path:
    """Flatten a ROS 2 launch file and write generated.launch.xml.

    Patches are applied on first call (idempotent for subsequent calls).
    All launch imports are deferred so patches take effect before any
    launch entity class is instantiated.

    Args:
        launch_file: Absolute path to the top-level launch file.
        launch_arguments: Resolved key/value pairs forwarded to the launch file.
        output_dir: Directory that receives generated.launch.xml + entity_tree.pu.
        debug: Enable launch debug logging.

    Returns:
        Path to the written generated.launch.xml.
    """
    _ensure_patches()

    # Late imports required: patches must be applied before any launch_ros class loads.
    import launch  # noqa: PLC0415
    from launch_unifier.filter import filter_entity_tree
    from launch_unifier.launch_maker import generate_launch_file
    from launch_unifier.parser import create_entity_tree
    from launch_unifier.plantuml import generate_plantuml
    from launch_unifier.serialization import make_entity_tree_serializable

    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True, parents=True)

    root_entity = launch.actions.IncludeLaunchDescription(
        launch.launch_description_sources.AnyLaunchDescriptionSource(str(launch_file)),
        launch_arguments=launch_arguments,
    )
    launch_service = launch.LaunchService(
        argv=[f"{k}:={v}" for k, v in launch_arguments],
        noninteractive=True,
        debug=debug,
    )

    raw_tree = create_entity_tree(root_entity, launch_service)
    filtered_tree = filter_entity_tree(raw_tree.copy())
    serializable_tree = make_entity_tree_serializable(filtered_tree, launch_service.context, output_dir=output_dir)

    xml_path = output_dir / "generated.launch.xml"
    xml_path.write_text(generate_launch_file(serializable_tree))
    (output_dir / "entity_tree.pu").write_text(generate_plantuml(serializable_tree))

    return xml_path
