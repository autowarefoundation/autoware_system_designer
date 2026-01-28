"""Template rendering utilities for consistent Jinja2 rendering across the project."""

from __future__ import annotations

import json
import os

from jinja2 import Environment, FileSystemLoader


def _get_template_directories() -> list[str]:
    """Resolve template search paths.

    Supports both source checkout and installed site-packages layouts.
    """

    # Base dir is .../autoware_system_designer/file_io
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Templates bundled in-package
    core_template_dir = os.path.abspath(os.path.join(base_dir, "../template"))
    ros2_launcher_template_dir = os.path.abspath(
        os.path.join(base_dir, "../ros2_launcher/templates")
    )

    template_dirs: list[str] = []

    if os.path.exists(core_template_dir):
        template_dirs.append(core_template_dir)

    if os.path.exists(ros2_launcher_template_dir):
        template_dirs.append(ros2_launcher_template_dir)

    if template_dirs:
        return template_dirs

    # Fallback: try ROS package share directory
    try:
        from ament_index_python.packages import get_package_share_directory

        share_dir = get_package_share_directory("autoware_system_designer")
        share_template_dir = os.path.join(share_dir, "template")
        share_ros2_launcher_template_dir = os.path.join(share_dir, "ros2_launcher", "templates")

        if os.path.exists(share_template_dir):
            template_dirs.append(share_template_dir)

        if os.path.exists(share_ros2_launcher_template_dir):
            template_dirs.append(share_ros2_launcher_template_dir)

        return template_dirs
    except Exception:
        return []


def custom_serializer(obj):
    """Custom JSON serializer for domain objects."""

    if hasattr(obj, "port_path") and hasattr(obj, "msg_type"):
        return {
            "unique_id": getattr(obj, "unique_id", None),
            "name": getattr(obj, "name", None),
            "msg_type": getattr(obj, "msg_type", None),
            "namespace": getattr(obj, "namespace", []),
            "topic": getattr(obj, "topic", []),
            "is_global": getattr(obj, "is_global", False),
            "port_path": getattr(obj, "port_path", None),
            "event": getattr(obj, "event", None),
        }

    if hasattr(obj, "from_port") and hasattr(obj, "to_port") and hasattr(obj, "connection_type"):
        return {
            "from_port": obj.from_port,
            "to_port": obj.to_port,
            "msg_type": getattr(obj, "msg_type", None),
            "connection_type": str(obj.connection_type) if obj.connection_type else None,
        }

    if hasattr(obj, "type_list") and hasattr(obj, "triggers"):
        return {
            "unique_id": getattr(obj, "unique_id", None),
            "name": getattr(obj, "name", None),
            "type": getattr(obj, "type", None),
            "frequency": getattr(obj, "frequency", None),
            "warn_rate": getattr(obj, "warn_rate", None),
            "error_rate": getattr(obj, "error_rate", None),
            "timeout": getattr(obj, "timeout", None),
            "trigger_ids": [t.unique_id for t in obj.triggers] if obj.triggers else [],
            "action_ids": [a.unique_id for a in obj.actions] if obj.actions else [],
        }

    return str(obj)


def tojson_filter(value):
    """Jinja2 filter to serialize objects to JSON."""

    return json.dumps(value, default=custom_serializer)


class TemplateRenderer:
    """Unified template rendering utility."""

    def __init__(self, template_dir: str | list[str] | None = None):
        if template_dir is None:
            template_dirs = _get_template_directories()
        elif isinstance(template_dir, str):
            template_dirs = [template_dir]
        else:
            template_dirs = list(template_dir)

        self.template_dirs = template_dirs
        self.env = Environment(
            loader=FileSystemLoader(self.template_dirs),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            newline_sequence="\n",
            autoescape=False,
        )
        self.env.filters["tojson"] = tojson_filter

    def render_template(self, template_name: str, **kwargs) -> str:
        template = self.env.get_template(template_name)
        return template.render(**kwargs)

    def render_template_to_file(self, template_name: str, output_path: str, **kwargs) -> None:
        content = self.render_template(template_name, **kwargs)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        if os.path.exists(output_path):
            os.remove(output_path)
        with open(output_path, "w") as f:
            f.write(content)
