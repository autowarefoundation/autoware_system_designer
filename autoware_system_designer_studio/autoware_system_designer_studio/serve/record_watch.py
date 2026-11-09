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

"""Watch of the measurement record root: a ``records.changed`` event per new latency record.

The runtime writes a record at the end of each measure window, whether launched by this daemon or
from a shell; the record root is polled for it. Polling runs only while an event stream is open and
reads one directory per ``<package>/<system>/latency/<mode>``. A record counts once its size has
held still over two polls; the first poll seeds silently.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from autoware_system_designer_runtime import records

from .events import EventHub

logger = logging.getLogger(__name__)

TOPIC_RECORDS_CHANGED = "records.changed"
POLL_INTERVAL_S = 2.0


class RecordWatcher:
    """Newest latency record per system and mode under the record root, reported as it changes."""

    def __init__(self, hub: EventHub, record_root: Optional[Path] = None, poll_s: float = POLL_INTERVAL_S) -> None:
        self._hub = hub
        self.record_root = record_root or records.record_root()
        self._poll_s = poll_s
        # (package, system, mode) -> (name, size) of the record last reported or seeded.
        self._known: dict[tuple[str, str, str], tuple[str, int]] = {}
        # Same key -> (name, size) seen on the previous poll, not yet settled.
        self._pending: dict[tuple[str, str, str], tuple[str, int]] = {}
        self._seeded = False

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_s)
            if self._hub.subscriber_count == 0:
                continue
            try:
                await asyncio.to_thread(self.scan)
            except Exception:  # noqa: BLE001 - a poll must not end the watch
                logger.exception("record watch failed")

    def scan(self) -> list[dict[str, Any]]:
        """One poll; returns the events it published."""
        newest = _newest_records(self.record_root)
        published: list[dict[str, Any]] = []
        if not self._seeded:
            self._known = dict(newest)
            self._seeded = True
            return published
        for key, current in newest.items():
            if self._known.get(key) == current:
                self._pending.pop(key, None)
                continue
            if self._pending.get(key) != current:
                self._pending[key] = current
                continue
            self._pending.pop(key, None)
            self._known[key] = current
            package, system, mode = key
            data = {
                "system": f"{package}:{system}",
                "mode": mode,
                "record": str(records.latency_record_dir(package, system, mode, self.record_root) / current[0]),
            }
            self._hub.publish(TOPIC_RECORDS_CHANGED, data)
            published.append(data)
        for key in list(self._known):
            if key not in newest:
                del self._known[key]
                self._pending.pop(key, None)
        return published


def _newest_records(root: Path) -> dict[tuple[str, str, str], tuple[str, int]]:
    """(name, size) of the newest record of every ``<package>/<system>/latency/<mode>`` directory."""
    found: dict[tuple[str, str, str], tuple[str, int]] = {}
    for package in _subdirs(root):
        for system in _subdirs(package):
            for mode in _subdirs(system / records.LATENCY_KIND):
                newest: Optional[tuple[str, int]] = None
                try:
                    with os.scandir(mode) as entries:
                        for entry in entries:
                            if not entry.name.endswith(records.RECORD_SUFFIX) or not entry.is_file():
                                continue
                            if newest is None or entry.name > newest[0]:
                                newest = (entry.name, entry.stat().st_size)
                except OSError:
                    continue
                if newest is not None:
                    found[(package.name, system.name, mode.name)] = newest
    return found


def _subdirs(path: Path) -> list[Path]:
    try:
        return [entry for entry in path.iterdir() if entry.is_dir()]
    except OSError:
        return []
