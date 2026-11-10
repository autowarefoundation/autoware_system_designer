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

"""The preloaded rcl tracer: where the library is, how a process is armed, the control file.

An armed process keeps counters per endpoint from its first rcl call on; event
records follow the control file in the trace directory, which the runtime
switches on for a measurement window only.
"""

from __future__ import annotations

import logging
import os
import struct
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..core.coordinator import Coordinator, CoordinatorBuilder

logger = logging.getLogger(__name__)

TRACER_PACKAGE = "autoware_system_designer_tracer"
TRACER_LIB_ENV = "ASD_TRACER_LIB"
TRACE_DIR_ENV = "ASD_TRACE_DIR"
TRACE_CAPACITY_ENV = "ASD_TRACE_CAPACITY"
STATS_SLOTS_ENV = "ASD_STATS_SLOTS"

# Mirrors asd_control_t of trace_format.h.
CONTROL_FILE = "control"
CONTROL_MAGIC = b"ASDCTRL\0"
CONTROL_VERSION = 1
_CONTROL = struct.Struct("<8sII48s")
_EVENTS_OFFSET = 12


def locate_tracer() -> Path:
    """The preload library, from ``ASD_TRACER_LIB`` or the ament index."""
    override = os.environ.get(TRACER_LIB_ENV)
    if override:
        path = Path(override)
        if path.is_file():
            return path
        raise FileNotFoundError(f"{TRACER_LIB_ENV}={override} is not a file")
    try:
        from ament_index_python.packages import PackageNotFoundError, get_package_prefix
    except ImportError as exc:  # pragma: no cover - ament is part of every ROS install
        raise FileNotFoundError("ament_index_python is not available") from exc
    try:
        prefix = Path(get_package_prefix(TRACER_PACKAGE))
    except PackageNotFoundError as exc:
        raise FileNotFoundError(f"package {TRACER_PACKAGE} is not installed; build it or set {TRACER_LIB_ENV}") from exc
    path = prefix / "lib" / f"lib{TRACER_PACKAGE}.so"
    if not path.is_file():
        raise FileNotFoundError(f"tracer library missing at {path}")
    return path


def tracer_env(tracer: Path, trace_dir: Path, base: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Environment of a traced process: the preload prepended, the trace directory set."""
    env = dict(base if base is not None else os.environ)
    preload = str(tracer)
    existing = env.get("LD_PRELOAD", "")
    if existing and preload not in existing.split(":"):
        preload = f"{preload}:{existing}"
    elif existing:
        preload = existing
    env["LD_PRELOAD"] = preload
    env[TRACE_DIR_ENV] = str(trace_dir)
    return env


class TraceControl:
    """The control file of a trace directory: one word every traced process reads on each record."""

    def __init__(self, trace_dir: Path) -> None:
        self._path = Path(trace_dir) / CONTROL_FILE

    @property
    def path(self) -> Path:
        return self._path

    def create(self, events: bool = False) -> None:
        """Writes a fresh control file; it must exist before the first traced process starts."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._path.with_name(f".{CONTROL_FILE}.part")
        partial.write_bytes(_CONTROL.pack(CONTROL_MAGIC, CONTROL_VERSION, int(events), b"\0" * 48))
        os.replace(partial, self._path)

    def set_events(self, on: bool) -> None:
        """Switches event recording in every process that mapped the file; takes effect on the next record."""
        fd = os.open(self._path, os.O_WRONLY)
        try:
            os.pwrite(fd, struct.pack("<I", int(on)), _EVENTS_OFFSET)
        finally:
            os.close(fd)

    def events(self) -> bool:
        try:
            data = self._path.read_bytes()
        except OSError:
            return False
        if len(data) < _CONTROL.size:
            return False
        magic, version, events, _ = _CONTROL.unpack_from(data)
        return magic == CONTROL_MAGIC and version == CONTROL_VERSION and events != 0


class TracerArming:
    """Preloads the tracer into every process the coordinator spawns, with event recording off."""

    def __init__(self, tracer: Path, trace_dir: Path) -> None:
        self.tracer = Path(tracer)
        self.trace_dir = Path(trace_dir)
        self.control = TraceControl(self.trace_dir)

    def install(self, builder: "CoordinatorBuilder") -> None:
        builder.add_pre_start_hook(self._pre_start)

    async def _pre_start(self, coord: "Coordinator") -> None:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.control.create(events=False)
        for spec in coord.specs():
            spec.env = tracer_env(self.tracer, self.trace_dir, spec.env)
        logger.info("tracer %s armed, counters in %s", self.tracer, self.trace_dir)
