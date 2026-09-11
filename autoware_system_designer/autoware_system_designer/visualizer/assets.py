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


"""Static web bundle shipped with the package: what it contains and how it is copied out."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from autoware_system_designer.common.source_location import SourceLocation, format_source

logger = logging.getLogger(__name__)

ASSET_ROOT = Path(__file__).resolve().parent

# Diagram type -> the window variable its generated data file populates.
# Declares the diagram set for the data files, the page config, and index discovery alike.
DIAGRAM_TYPES = {
    "node_diagram": "systemDesignData",
    "sequence_diagram": "sequenceDiagramData",
    "logic_diagram": "logicDiagramData",
}

DEFAULT_DIAGRAM_TYPE = "node_diagram"

OVERVIEW_PAGE = "deployment_overview.html"

# Scripts the overview page loads from its own directory, in load order.
OVERVIEW_SCRIPTS = (
    "js/diagram_base.js",
    "js/elk_canvas.js",
    "js/editor_link.js",
    "js/info_panel.js",
    "js/overview_page.js",
    "js/node_diagram.js",
    "js/sequence_diagram.js",
    "js/logic_diagram.js",
    "js/theme.js",
)

# Stylesheet every page links as css/styles.css.
STYLESHEET = "css/styles.css"

# Assets the standalone systems index needs beside itself: source -> path under the install root.
INDEX_ASSETS = {
    STYLESHEET: "css/styles.css",
    "js/theme.js": "theme.js",
}


def resolve(relative_path: str) -> Path | None:
    """Absolute path of a bundled asset, or None when it is missing."""
    candidate = ASSET_ROOT / relative_path
    if candidate.exists():
        return candidate
    logger.error(f"Static asset not found: {relative_path}{format_source(SourceLocation(file_path=candidate))}")
    return None


def copy_asset(relative_path: str, destination: str) -> bool:
    """Copy a bundled asset to an exact destination path, creating parent directories."""
    source = resolve(relative_path)
    if source is None:
        return False
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    shutil.copy2(source, destination)
    logger.info(f"Copied static asset: {os.path.basename(destination)}")
    return True


def copy_into(relative_path: str, destination_dir: str) -> bool:
    """Copy a bundled asset into a directory under its own basename."""
    return copy_asset(relative_path, os.path.join(destination_dir, os.path.basename(relative_path)))
