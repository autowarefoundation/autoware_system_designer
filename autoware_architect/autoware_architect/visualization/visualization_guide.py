from typing import List, Dict, Optional, Tuple

# Base color mapping for component types
# All color variants (matte, medium, bright, text) are calculated from these base colors
BASE_COLOR_MAP = {
    "sensing": "#cc6666",      # red
    "localization": "#cc8855", # orange
    "map": "#6699aa",          # cyan/teal
    "perception": "#ccaa55",   # yellow
    "planning": "#6b9b6b",     # green
    "control": "#6677bb",      # blue
    "system": "#9966bb",       # purple
}

DEFAULT_BASE_COLOR = "#888888"  # gray

def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color to RGB tuple.

    Args:
        hex_color: Hex color string (e.g., "#cc6666")

    Returns:
        Tuple of (r, g, b) values (0-255)
    """
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB values to hex color string.

    Args:
        r, g, b: RGB values (0-255)

    Returns:
        Hex color string (e.g., "#cc6666")
    """
    return f"#{r:02x}{g:02x}{b:02x}"

def calculate_color_variant(base_color: str, variant: str) -> str:
    """Calculate a color variant from a base color.

    Args:
        base_color: Base hex color string
        variant: Variant type - "matte", "medium", "bright", or "text"

    Returns:
        Calculated hex color string
    """
    r, g, b = hex_to_rgb(base_color)

    if variant == "matte":
        # Matte: use base color as-is
        return base_color
    elif variant == "medium":
        # Medium: blend 50% base + 50% white for lighter background
        return rgb_to_hex(
            int(r * 0.5 + 255 * 0.5),
            int(g * 0.5 + 255 * 0.5),
            int(b * 0.5 + 255 * 0.5)
        )
    elif variant == "bright":
        # Bright: blend 20% base + 80% white for pastel background
        return rgb_to_hex(
            int(r * 0.2 + 255 * 0.8),
            int(g * 0.2 + 255 * 0.8),
            int(b * 0.2 + 255 * 0.8)
        )
    elif variant == "text":
        # Text: darken by 30% for better contrast
        return rgb_to_hex(
            int(r * 0.3),
            int(g * 0.3),
            int(b * 0.3)
        )
    else:
        return base_color

def get_component_color(namespace: List[str], variant: str = "matte") -> str:
    """Get color for a component based on its top-level namespace.

    All color variants are calculated dynamically from the base color map.

    Args:
        namespace: List of namespace components
        variant: Color variant - "matte" (default), "medium", "bright", or "text"

    Returns:
        Calculated hex color string
    """
    # Get base color
    if not namespace or len(namespace) == 0:
        base_color = DEFAULT_BASE_COLOR
    else:
        # Get the top-level component (first in namespace)
        top_level = namespace[0].lower()
        base_color = BASE_COLOR_MAP.get(top_level, DEFAULT_BASE_COLOR)

    # Calculate and return the requested variant
    return calculate_color_variant(base_color, variant)

# Position map for visualization
# left to right, top to bottom
# each element is a tuple of (x, y)
POSITION_MAP = {
  "map": [0, 0],
  "sensing": {
    "lidar": [0, 1],
    "camera": [0, 2],
    "radar": [0, 3],
  },
  "localization": [1, 0],
  "perception":
  {
    "obstacle_segmentation": [2, 1],
    "occupancy_grid_map": [3, 1],
    "object_recognition": [4, 2],
    "traffic_light_recognition": [3, 1],
  },
  "planning": [5, 2],
  "control": [6, 2],
  "system": [7, 5]
}

def get_component_position(namespace: List[str]) -> Optional[List[int]]:
    """Get position [x, y] for a component based on its namespace.

    Traverses the POSITION_MAP using the namespace components.
    Returns the most specific position found.

    Args:
        namespace: List of namespace components

    Returns:
        List [x, y] or None if no position found
    """
    if not namespace:
        return None

    current_level = POSITION_MAP
    last_found_pos = None

    for part in namespace:
        key = part.lower()
        if isinstance(current_level, dict) and key in current_level:
            val = current_level[key]
            if isinstance(val, (list, tuple)) and len(val) == 2:
                last_found_pos = list(val)
                # If we hit a coordinate, we stop traversing because
                # in the current map structure, coordinates are leaf values.
                return last_found_pos
            elif isinstance(val, dict):
                current_level = val
            else:
                break
        else:
            break

    return last_found_pos
