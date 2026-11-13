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

"""Web bundle shipped with the studio: its files and the load order of each page."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"

# Directory of a site that holds the bundle, keeping the web/ layout below it.
BUNDLE_DIR = "assets"
# Bundle subdirectories a site carries.
BUNDLE_SUBDIRS = ("css", "js", "img")

# Tab icon of every page.
FAVICON = "img/favicon.svg"

# Window variable a mode's data script fills: the exported system structure, keyed by mode.
DATA_GLOBAL = "systemStructureData"

# Diagrams of the Explore activity, in menu order: the module script loaded when the diagram is
# first shown, whether the module locates entities (``focusEntity``), which Explore search needs,
# and whether it draws the design hierarchy cut at a chosen depth (the ``depth`` option).
DIAGRAMS = {
    "node_diagram": {
        "label": "Node Diagram",
        "script": "js/diagrams/node_diagram.js",
        "searchable": True,
        "layered": True,
    },
    "logic_diagram": {
        "label": "Logic Diagram",
        "script": "js/diagrams/logic_diagram.js",
        "searchable": True,
        "layered": False,
    },
    "sequence_diagram": {
        "label": "Sequence Diagram",
        "script": "js/diagrams/sequence_diagram.js",
        "searchable": False,
        "layered": False,
    },
}

DEFAULT_DIAGRAM_TYPE = "node_diagram"

# Page core every page loads first: the SD namespace, theme and editor links.
CORE_SCRIPTS = (
    "js/core/sd.js",
    "js/core/editor_link.js",
)

# Workbench shell and the widgets its activities build on, in load order.
SHELL_SCRIPTS = (
    "js/shell/layout.js",
    "js/shell/statusbar.js",
    "js/shell/shell.js",
    "js/widgets/controls.js",
    "js/widgets/terminal.js",
    "js/widgets/main_view.js",
    "js/widgets/popup.js",
    "js/widgets/namespace_tree.js",
    "js/widgets/run_state.js",
)

# Rail activities: each one's scripts (its parts, then the entry that registers it) and stylesheet.
# The shell orders the rail by each activity's declared order.
ACTIVITIES = {
    "explore": {
        "scripts": (
            "js/activities/explore/diagram_host.js",
            "js/activities/explore/entity_search.js",
            "js/activities/explore/depth_selector.js",
            "js/activities/explore/live_overlay.js",
            "js/activities/explore/index.js",
        ),
        "styles": ("css/activities/explore.css",),
    },
    "launch": {
        "scripts": (
            "js/activities/launch/sessions.js",
            "js/activities/launch/units.js",
            "js/activities/launch/member_tree.js",
            "js/activities/launch/commands_table.js",
            "js/activities/launch/state_monitor.js",
            "js/diagrams/treemap_layout.js",
            "js/diagrams/treemap.js",
            "js/activities/launch/state_map.js",
            "js/activities/launch/member_actions.js",
            "js/activities/launch/topic_model.js",
            "js/activities/launch/topic_rates.js",
            "js/activities/launch/topic_flow.js",
            "js/activities/launch/index.js",
        ),
        "styles": ("css/activities/launch.css",),
    },
    "build": {
        "scripts": (
            "js/activities/build/job_log.js",
            "js/activities/build/index.js",
        ),
        "styles": ("css/activities/build.css",),
    },
}


def _activity_files(kind: str) -> tuple[str, ...]:
    return tuple(path for activity in ACTIVITIES.values() for path in activity[kind])


# Scripts the workbench loads in its head, in load order; diagram modules load on demand.
WORKBENCH_SCRIPTS = (
    CORE_SCRIPTS
    + (
        "js/core/store.js",
        "js/core/daemon.js",
        "js/diagrams/diagram_base.js",
        "js/diagrams/elk_canvas.js",
        "js/diagrams/event_graph.js",
        "js/diagrams/timing_model.js",
        "js/diagrams/latency_source.js",
        "js/inspector/info_panel.js",
    )
    + SHELL_SCRIPTS
    + _activity_files("scripts")
)

# Script that drives the workbench once its body is parsed.
WORKBENCH_ENTRY = "js/pages/workbench_page.js"

# Stylesheets in cascade order: tokens first, then the areas reading them.
WORKBENCH_STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/shell.css",
    "css/widgets.css",
    "css/inspector.css",
    "css/node_diagram.css",
    "css/logic_diagram.css",
    "css/sequence_diagram.css",
    "css/treemap.css",
) + _activity_files("styles")

# Content pages (systems index, status pages).
PAGE_STYLESHEETS = (
    "css/tokens.css",
    "css/base.css",
    "css/pages.css",
)

PAGE_SCRIPTS = ("js/core/sd.js",)


def bundle_files() -> list[str]:
    """Every bundled file a site carries, relative to WEB_ROOT."""
    return sorted(
        path.relative_to(WEB_ROOT).as_posix()
        for subdir in BUNDLE_SUBDIRS
        for path in (WEB_ROOT / subdir).rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )


def web_file(relative_path: str) -> Optional[Path]:
    """File under WEB_ROOT named by a slash-separated relative path, None when it is not one.

    Empty, dot and hidden segments are refused, and so is any path resolving outside WEB_ROOT.
    """
    parts = relative_path.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") or "\\" in p or "\0" in p for p in parts):
        return None
    candidate = WEB_ROOT.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(WEB_ROOT.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None
