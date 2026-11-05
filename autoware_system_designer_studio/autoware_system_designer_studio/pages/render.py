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

"""The studio site: every page and data script drawn from exported systems.

One URL layout serves both the daemon (rendered per request) and ``pack`` (written to disk)::

    index.html                                  systems index
    assets/{css,js}/...                         the web bundle
    systems/<package>/<system>/index.html       workbench (diagrams)
    systems/<package>/<system>/launch.html      launch commands
    systems/<package>/<system>/config.js        workbench config
    systems/<package>/<system>/data/<mode>.js   the mode's system structure, vis guides attached
    systems/<package>/<system>/data/<mode>_latency.js   the mode's latency, when there is any

Data travels as scripts so a packed site works from ``file://``, where pages cannot fetch.
Links are relative, so the same pages work under any root.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterator, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .. import latency
from ..exports import SystemExport
from ..vis_guide import inject_vis_guides
from . import assets
from .launch_commands import launch_commands_view

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

INDEX_PAGE = "index.html"
SYSTEMS_DIR = "systems"
WORKBENCH_PAGE = "index.html"
LAUNCH_PAGE = "launch.html"
CONFIG_SCRIPT = "config.js"
DATA_DIR = "data"
LATENCY_SCRIPT_SUFFIX = "_latency.js"
EDITOR_SCHEME = "vscode"

# A system page sits three directories below the site root.
SYSTEM_PAGE_ROOT = "../../../"


def system_dir(export: SystemExport) -> str:
    return f"{SYSTEMS_DIR}/{export.package}/{export.name}"


def system_pages(export: SystemExport) -> dict[str, str]:
    """Site-relative paths of a system's pages."""
    base = system_dir(export)
    return {"overview": f"{base}/{WORKBENCH_PAGE}", "launch_commands": f"{base}/{LAUNCH_PAGE}"}


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        # Markup templates escape every interpolated value; JS templates quote via tojson.
        autoescape=select_autoescape(enabled_extensions=("html.jinja2",), default=False),
    )
    # tojson keeps declaration key order.
    env.policies["json.dumps_kwargs"] = {}
    return env


def _bundle_links(root: str, paths: tuple[str, ...]) -> list[str]:
    return [f"{root}{assets.BUNDLE_DIR}/{path}" for path in paths]


def data_script_text(global_name: str, mode: str, data: Any) -> str:
    """Script assigning one mode's object under a page global, loadable from file://."""
    key = json.dumps(mode)
    return f"window.{global_name} = window.{global_name} || {{}};\nwindow.{global_name}[{key}] = {json.dumps(data)};\n"


class Site:
    """Pages of a set of exported systems; ``render`` returns None for a path that is not one."""

    def __init__(self, systems: list[SystemExport], *, record_root: Optional[Path] = None) -> None:
        self.systems = {(s.package, s.name): s for s in systems}
        self.record_root = record_root
        self._env = _environment()

    # -- layout ------------------------------------------------------------

    def paths(self) -> Iterator[str]:
        """Every page and data script of the site, the bundle excluded."""
        yield INDEX_PAGE
        for export in self.systems.values():
            base = system_dir(export)
            yield from (f"{base}/{WORKBENCH_PAGE}", f"{base}/{LAUNCH_PAGE}", f"{base}/{CONFIG_SCRIPT}")
            for mode in export.mode_keys():
                if export.structure_path(mode).is_file():
                    yield f"{base}/{DATA_DIR}/{mode}.js"
                if export.latency_file(mode, self.record_root) is not None:
                    yield f"{base}/{DATA_DIR}/{mode}{LATENCY_SCRIPT_SUFFIX}"

    def render(self, path: str) -> Optional[str]:
        if path == INDEX_PAGE:
            return self._index()
        parts = path.split("/")
        if len(parts) < 4 or parts[0] != SYSTEMS_DIR:
            return None
        export = self.systems.get((parts[1], parts[2]))
        if export is None:
            return None
        rest = parts[3:]
        if rest == [WORKBENCH_PAGE]:
            return self._workbench(export)
        if rest == [LAUNCH_PAGE]:
            return self._launch_commands(export)
        if rest == [CONFIG_SCRIPT]:
            return self._config(export)
        if len(rest) == 2 and rest[0] == DATA_DIR:
            return self._data(export, rest[1])
        return None

    # -- pages -------------------------------------------------------------

    def _index(self) -> str:
        systems = [
            {"name": e.name, "package": e.package, **system_pages(e)}
            for e in sorted(self.systems.values(), key=lambda e: (e.package, e.name))
        ]
        return self._env.get_template("systems_index.html.jinja2").render(
            systems=systems,
            stylesheets=_bundle_links("", assets.PAGE_STYLESHEETS),
            scripts=_bundle_links("", assets.PAGE_SCRIPTS),
        )

    def _workbench(self, export: SystemExport) -> str:
        return self._env.get_template("overview.html.jinja2").render(
            deployment_name=export.name,
            stylesheets=_bundle_links(SYSTEM_PAGE_ROOT, assets.OVERVIEW_STYLESHEETS),
            scripts=_bundle_links(SYSTEM_PAGE_ROOT, assets.OVERVIEW_SCRIPTS),
            entry_script=f"{SYSTEM_PAGE_ROOT}{assets.BUNDLE_DIR}/{assets.OVERVIEW_ENTRY}",
        )

    def _launch_commands(self, export: SystemExport) -> str:
        return self._env.get_template("launch_commands.html.jinja2").render(
            system_name=export.name,
            package_name=export.package,
            systems_index_path=f"{SYSTEM_PAGE_ROOT}{INDEX_PAGE}",
            overview_path=WORKBENCH_PAGE,
            stylesheets=_bundle_links(SYSTEM_PAGE_ROOT, assets.PAGE_STYLESHEETS),
            scripts=_bundle_links(SYSTEM_PAGE_ROOT, assets.PAGE_SCRIPTS),
            **launch_commands_view(export),
        )

    def _config(self, export: SystemExport) -> str:
        modes = [m for m in export.mode_keys() if export.structure_path(m).is_file()]
        default_mode = export.manifest.get("default_mode")
        if default_mode not in modes:
            default_mode = modes[0] if modes else ""
        return self._env.get_template("data/deployment_config.js.jinja2").render(
            deployment_name=export.name,
            package_name=export.package,
            systems_index_path=f"{SYSTEM_PAGE_ROOT}{INDEX_PAGE}",
            launch_commands_path=LAUNCH_PAGE,
            available_modes=modes,
            default_mode=default_mode,
            available_diagram_types=list(assets.DIAGRAM_SCRIPTS),
            diagram_scripts={
                name: f"{SYSTEM_PAGE_ROOT}{assets.BUNDLE_DIR}/{script}" for name, script in assets.DIAGRAM_SCRIPTS.items()
            },
            data_base=f"{DATA_DIR}/",
            latency_modes=[m for m in modes if export.latency_file(m, self.record_root) is not None],
            default_diagram_type=assets.DEFAULT_DIAGRAM_TYPE,
            editor_scheme=EDITOR_SCHEME,
            system_definition_file=export.system_file,
        )

    def _data(self, export: SystemExport, filename: str) -> Optional[str]:
        if filename.endswith(LATENCY_SCRIPT_SUFFIX):
            mode = filename[: -len(LATENCY_SCRIPT_SUFFIX)]
            if mode in export.mode_keys():
                return self._latency(export, mode)
        if filename.endswith(".js"):
            mode = filename[: -len(".js")]
            if mode in export.mode_keys() and export.structure_path(mode).is_file():
                return self._structure(export, mode)
        return None

    def _structure(self, export: SystemExport, mode: str) -> str:
        data, metadata = export.structure(mode)
        inject_vis_guides(data)
        # Declared compute units travel with each mode's tree; the node diagram groups components by them.
        view = {**data, "compute_units": metadata.get("compute_units") or [], "mode": mode}
        return data_script_text(assets.DATA_GLOBAL, mode, view)

    def _latency(self, export: SystemExport, mode: str) -> Optional[str]:
        source = export.latency_file(mode, self.record_root)
        if source is None:
            return None
        try:
            data = latency.load_latency_file(source)
        except (OSError, ValueError) as exc:
            logger.warning("latency file ignored: %s (%s)", source, exc)
            return None
        return latency.latency_script_text(mode, data)
