# Copyright 2026 TIER IV, inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Per-component presentation hints (color, canvas position) attached to the exported tree."""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# Base color per top-level component; every variant is derived from these.
BASE_COLOR_MAP = {
    "sensing": "#cc6666",  # red
    "localization": "#cc8855",  # orange
    "map": "#6699aa",  # cyan/teal
    "perception": "#ccaa55",  # yellow
    "planning": "#6b9b6b",  # green
    "control": "#6677bb",  # blue
    "system": "#9966bb",  # purple
    "gray": "#888888",  # gray
}

# Variant -> (base weight, white weight). Weights below 1 without white darken the base.
COLOR_VARIANTS = {
    "medium": (0.5, 0.5),
    "bright": (0.2, 0.8),
    "fade": (0.7, 0.0),
    "darkish": (0.39, 0.0),
    "dark": (0.26, 0.0),
    "darkest": (0.1, 0.0),
}


def hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """Convert hex color to RGB tuple.

    Args:
        hex_color: Hex color string (e.g., "#cc6666")

    Returns:
        Tuple of (r, g, b) values (0-255)
    """
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB values to hex color string.

    Args:
        r, g, b: RGB values (0-255)

    Returns:
        Hex color string (e.g., "#cc6666")
    """
    return f"#{r:02x}{g:02x}{b:02x}"


def calculate_color_variant(base_color: str, variant: str) -> str:
    """Blend a base color toward white or black per COLOR_VARIANTS; unknown variants pass through.

    Args:
        base_color: Base hex color string
        variant: Key of COLOR_VARIANTS, or "base" for the color as-is

    Returns:
        Calculated hex color string
    """
    weights = COLOR_VARIANTS.get(variant)
    if weights is None:
        return base_color
    base_weight, white_weight = weights
    return rgb_to_hex(*(int(channel * base_weight + 255 * white_weight) for channel in hex_to_rgb(base_color)))


def get_component_color(namespace: List[str], variant: str = "base") -> str:
    """Get color for a component based on its top-level namespace.

    Args:
        namespace: List of namespace components
        variant: Key of COLOR_VARIANTS, or "base" for the unmodified base color

    Returns:
        Calculated hex color string
    """
    top_level = namespace[0].lower() if namespace else None
    base_color = BASE_COLOR_MAP.get(top_level, BASE_COLOR_MAP["gray"])
    return calculate_color_variant(base_color, variant)


# Canvas grid slot [x, y] per component, left to right and top to bottom.
# Nested maps refine a component; a coordinate is a leaf.
POSITION_MAP = {
    "map": [0, 0],
    "sensing": {
        "lidar": [0, 1],
        "camera": [0, 2],
        "radar": [0, 3],
    },
    "localization": [1, 0],
    "perception": {
        "obstacle_segmentation": [2, 1],
        "occupancy_grid_map": [3, 1],
        "object_recognition": [4, 2],
        "traffic_light_recognition": [3, 1],
    },
    "planning": [5, 2],
    "control": [6, 2],
    "system": [7, 5],
}


def get_component_position(namespace: List[str]) -> Optional[List[int]]:
    """Get position [x, y] for a component based on its namespace.

    Traverses the POSITION_MAP using the namespace components and returns the
    most specific coordinate reached.

    Args:
        namespace: List of namespace components

    Returns:
        List [x, y] or None if no position found
    """
    level = POSITION_MAP

    for part in namespace:
        value = level.get(part.lower()) if isinstance(level, dict) else None
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return list(value)
        if not isinstance(value, dict):
            return None
        level = value

    return None


def build_vis_guide(namespace: List[str]) -> Dict[str, object]:
    """Build the color/position guide for a component namespace path."""
    return {
        "color": get_component_color(namespace, variant="base"),
        "medium_color": get_component_color(namespace, variant="medium"),
        "background_color": get_component_color(namespace, variant="bright"),
        "text_color": get_component_color(namespace, variant="darkest"),
        "dark_color": get_component_color(namespace, variant="fade"),
        "dark_medium_color": get_component_color(namespace, variant="darkish"),
        "dark_background_color": get_component_color(namespace, variant="dark"),
        "dark_text_color": get_component_color(namespace, variant="bright"),
        "position": get_component_position(namespace),
    }


def inject_vis_guides(instance_data: Dict) -> None:
    """Attach vis_guide to every instance of an exported structure tree, in place."""
    path = instance_data.get("path", "/")
    namespace = [part for part in path.strip("/").split("/") if part]
    instance_data["vis_guide"] = build_vis_guide(namespace)
    for child in instance_data.get("children", []):
        inject_vis_guides(child)
