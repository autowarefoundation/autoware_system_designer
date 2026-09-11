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


"""Install-tree locations shared by the deployment pages and the systems index."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

INSTALL_DIR_NAME = "install"
SYSTEMS_INDEX_FILENAME = "systems.html"


def get_install_root(path: Path) -> Path | None:
    """Nearest enclosing 'install' directory of a path, or None when outside one."""
    parts = path.resolve().parts
    if INSTALL_DIR_NAME not in parts:
        return None
    # The last occurrence wins when nested install trees are stacked.
    idx = len(parts) - 1 - parts[::-1].index(INSTALL_DIR_NAME)
    return Path(*parts[: idx + 1])


def systems_index_link(web_dir: str, fallback: str = "") -> str:
    """Relative href from a generated web directory to the install-root systems index."""
    install_root = get_install_root(Path(web_dir))
    if install_root and install_root.exists():
        try:
            return os.path.join(os.path.relpath(install_root, web_dir), SYSTEMS_INDEX_FILENAME)
        except ValueError:
            logger.warning("Could not calculate relative path from %s to %s", web_dir, install_root)
    return fallback
