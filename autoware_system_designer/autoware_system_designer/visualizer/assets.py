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
from dataclasses import dataclass
from pathlib import Path

from autoware_system_designer.common.source_location import SourceLocation, format_source

logger = logging.getLogger(__name__)

ASSET_ROOT = Path(__file__).resolve().parent

# Directory beside each page that holds the bundle, keeping the source layout below it.
BUNDLE_DIR = "assets"


@dataclass(frozen=True)
class DiagramType:
    # Window variable the generated data file populates.
    data_global: str
    # Module script the overview page loads when the diagram is first shown.
    script: str


# Declares the diagram set for the data files, the page config, and index discovery alike.
DIAGRAM_TYPES = {
    "node_diagram": DiagramType("systemDesignData", "js/diagrams/node_diagram.js"),
    "sequence_diagram": DiagramType("sequenceDiagramData", "js/diagrams/sequence_diagram.js"),
    "logic_diagram": DiagramType("logicDiagramData", "js/diagrams/logic_diagram.js"),
}

DEFAULT_DIAGRAM_TYPE = "node_diagram"

OVERVIEW_TEMPLATE = "overview.html.jinja2"

# Page core every page loads first: the SD namespace, theme and editor links.
CORE_SCRIPTS = (
    "js/core/sd.js",
    "js/core/editor_link.js",
)

# Scripts the overview page loads in its head, in load order; diagram modules load on demand.
OVERVIEW_SCRIPTS = CORE_SCRIPTS + (
    "js/diagrams/diagram_base.js",
    "js/diagrams/elk_canvas.js",
    "js/diagrams/event_graph.js",
    "js/diagrams/timing_model.js",
    "js/diagrams/latency_source.js",
    "js/inspector/info_panel.js",
)

# Script that drives the overview page once its body is parsed.
OVERVIEW_ENTRY = "js/pages/overview_page.js"

# Stylesheets in cascade order: tokens first, then the areas reading them.
OVERVIEW_STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/workbench.css",
    "css/inspector.css",
    "css/node_diagram.css",
    "css/logic_diagram.css",
    "css/sequence_diagram.css",
)

# Content pages (systems index, launch commands).
PAGE_STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/pages.css",
)

PAGE_SCRIPTS = ("js/core/sd.js",)

# Every bundled file an export carries under BUNDLE_DIR.
EXPORT_BUNDLE = tuple(
    dict.fromkeys(
        OVERVIEW_SCRIPTS
        + (OVERVIEW_ENTRY,)
        + tuple(diagram.script for diagram in DIAGRAM_TYPES.values())
        + OVERVIEW_STYLESHEETS
        + PAGE_STYLESHEETS
    )
)

# What the install-root systems index carries under BUNDLE_DIR.
INDEX_BUNDLE = tuple(dict.fromkeys(PAGE_STYLESHEETS + PAGE_SCRIPTS))


def bundle_path(relative_path: str) -> str:
    """URL of a bundled file relative to a page that sits beside BUNDLE_DIR."""
    return f"{BUNDLE_DIR}/{relative_path}"


def copy_bundle(files: tuple[str, ...], page_dir: str) -> bool:
    """Copy bundled files under page_dir/BUNDLE_DIR; True when every file was found."""
    copied = [copy_asset(path, os.path.join(page_dir, BUNDLE_DIR, path)) for path in files]
    return all(copied)


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
