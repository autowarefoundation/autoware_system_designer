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

"""``pack``: write the studio site of exported systems to a directory that opens from ``file://``."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Iterable, Optional

from .exports import DiscoveryError, SystemExport, discover_systems
from .pages import assets
from .pages.render import INDEX_PAGE, SYSTEMS_DIR, Site

logger = logging.getLogger(__name__)

# Marks a directory pack wrote; only such a directory is cleared and rewritten.
MARKER = ".autoware_system_designer_studio"
OWNED_ENTRIES = (INDEX_PAGE, SYSTEMS_DIR, assets.BUNDLE_DIR)


class PackError(Exception):
    pass


def select_systems(systems: list[SystemExport], wanted: Optional[Iterable[str]]) -> list[SystemExport]:
    """Systems named by id (``package:system``) or by system name; all when none is named."""
    if not wanted:
        return systems
    chosen = []
    for name in wanted:
        matches = [s for s in systems if name in (s.id, s.name)]
        if not matches:
            known = ", ".join(s.id for s in systems) or "none"
            raise PackError(f"no exported system {name!r}; available: {known}")
        chosen += [s for s in matches if s not in chosen]
    return chosen


def _prepare(out_dir: Path) -> None:
    if out_dir.exists():
        if not out_dir.is_dir():
            raise PackError(f"{out_dir} is not a directory")
        if any(out_dir.iterdir()) and not (out_dir / MARKER).is_file():
            raise PackError(f"{out_dir} is not empty and was not written by pack; choose another directory")
        for name in OWNED_ENTRIES:
            entry = out_dir / name
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            elif entry.exists() or entry.is_symlink():
                entry.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / MARKER).write_text("written by autoware-system-designer-studio pack\n")


def pack(
    install_root: Path,
    out_dir: Path,
    *,
    systems: Optional[Iterable[str]] = None,
    record_root: Optional[Path] = None,
) -> tuple[list[str], list[DiscoveryError]]:
    """Write the site and its bundle; returns the site paths written and the discovery errors."""
    found, errors = discover_systems(install_root)
    site = Site(select_systems(found, systems), record_root=record_root)
    _prepare(out_dir)
    written: list[str] = []
    for path in site.paths():
        text = site.render(path)
        if text is None:
            continue
        target = out_dir.joinpath(*path.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(path)
    for relative in assets.bundle_files():
        target = out_dir / assets.BUNDLE_DIR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assets.WEB_ROOT / relative, target)
    logger.info("packed %d page(s) and the bundle into %s", len(written), out_dir)
    return written, errors
