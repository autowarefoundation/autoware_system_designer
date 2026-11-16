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

"""Past runs, read from the runtime's log directories.

A log base holds one directory per run of a compute unit (see the runtime's ``run_log``), with the
rates record beside the manifest when the run kept one.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from autoware_system_designer_runtime.rates_record import RATES_FILE, RatesRecord
from autoware_system_designer_runtime.run_log import (
    LOG_BASE,
    RUN_MANIFEST,
    STREAMS,
    TAIL_LINES,
    member_dir_name,
    read_run_manifest,
    tail_lines,
)

from ..exports import SystemExport
from . import api

MAX_RUNS = 100


class RunArchive:
    """The runs under the log bases. ``session_dirs`` lists the log directory of each launch session
    with whether the session still runs."""

    def __init__(self, log_base: Optional[Path], session_dirs: Callable[[], Iterable[tuple[Path, bool]]]) -> None:
        self.log_base = log_base or LOG_BASE
        self._session_dirs = session_dirs
        # The rates record read last, with the path, change time and size of its file.
        self._rates_record: Optional[tuple[tuple[Path, int, int], RatesRecord]] = None
        # Run directory -> its manifest as read last, with the change time and size of the manifest file.
        self._manifests: dict[Path, tuple[tuple[int, int], Optional[dict[str, Any]]]] = {}

    def log_bases(self, sessions: Optional[Iterable[tuple[Path, bool]]] = None) -> list[Path]:
        """Directories holding run directories: the log base and the parents of the sessions' log
        directories."""
        bases = [self.log_base]
        for log_dir, _ in self._session_dirs() if sessions is None else sessions:
            if log_dir.parent not in bases:
                bases.append(log_dir.parent)
        return bases

    async def runs(
        self, system: Optional[SystemExport] = None, *, exclude_units: Iterable[str] = ()
    ) -> list[dict[str, Any]]:
        """Past runs, newest first: the directories holding a run manifest, less those an active session
        writes, its session serving them. Runs of ``exclude_units`` are left out, as are those of other
        systems with ``system``. The log bases are read on a worker thread, a manifest once per change of
        its file."""
        sessions = list(self._session_dirs())
        active = {log_dir for log_dir, running in sessions if running}
        return await asyncio.get_running_loop().run_in_executor(
            None, self._scan, self.log_bases(sessions), active, system, set(exclude_units)
        )

    def _scan(
        self, bases: Iterable[Path], active: set[Path], system: Optional[SystemExport], excluded: set[str]
    ) -> list[dict[str, Any]]:
        found = []
        held, seen = self._manifests, {}
        for base in bases:
            try:
                with os.scandir(base) as listing:
                    entries = [Path(entry.path) for entry in listing if entry.is_dir()]
            except OSError:
                continue
            for run_dir in entries:
                try:
                    stat = (run_dir / RUN_MANIFEST).stat()
                except OSError:
                    continue
                version = (stat.st_mtime_ns, stat.st_size)
                entry = held.get(run_dir)
                if entry is None or entry[0] != version:
                    entry = (version, read_run_manifest(run_dir))
                seen[run_dir] = entry
                manifest = entry[1]
                if manifest is None or run_dir in active:
                    continue
                if system is not None and not _run_of_system(manifest, system):
                    continue
                if manifest.get("compute_unit") not in excluded:
                    found.append((_started(manifest), run_dir, manifest))
        self._manifests = seen
        found.sort(key=lambda run: run[0], reverse=True)
        return [_run_summary(run_dir, manifest) for _, run_dir, manifest in found[:MAX_RUNS]]

    def run(self, name: str) -> tuple[Path, dict[str, Any]]:
        """The log directory and manifest of a past run, by directory name under one of the log bases."""
        if not _plain_name(name):
            raise api.ApiError(api.INVALID_PARAMS, "run must be a directory name")
        for base in self.log_bases():
            run_dir = base / name
            manifest = read_run_manifest(run_dir) if run_dir.is_dir() else None
            if manifest is not None:
                return run_dir, manifest
        raise api.ApiError(api.NOT_FOUND, f"no run {name!r}")

    def run_members(self, name: str) -> dict[str, Any]:
        """A past run with its members: the manifest's member paths that have a directory, each with its
        directory name."""
        run_dir, manifest = self.run(name)
        listed = manifest.get("members")
        paths = [path for path in listed if isinstance(path, str)] if isinstance(listed, list) else []
        members = [
            {"path": path, "slug": member_dir_name(path)}
            for path in paths
            if (run_dir / member_dir_name(path)).is_dir()
        ]
        return {**_run_summary(run_dir, manifest), "members": members}

    async def run_rates(
        self,
        name: str,
        *,
        sample: Optional[int] = None,
        start: Optional[int] = None,
        span_s: Optional[float] = None,
    ) -> dict[str, Any]:
        """Topic rates of a past run from its rates record: from sample number ``start``, else from
        ``span_s`` seconds earlier, up to sample number ``sample``, the whole run by default. ``times``
        lists the record's samples, ``density`` the counted events per second over the interval ending
        at each, and ``start`` and ``sample`` are the numbers of the span's first and end samples. The
        record is read on a worker thread."""
        run_dir, manifest = self.run(name)
        path = run_dir / RATES_FILE
        if not path.is_file():
            raise api.ApiError(api.NOT_FOUND, f"run {name!r} recorded no topic rates")
        try:
            rates = await asyncio.get_running_loop().run_in_executor(
                None, self._read_rates, path, sample, start, span_s
            )
        except (OSError, ValueError) as exc:
            raise api.ApiError(api.NOT_FOUND, f"rates of run {name!r} are not readable: {exc}") from exc
        return {**_run_summary(run_dir, manifest), **rates}

    def _read_rates(
        self, path: Path, sample: Optional[int], start: Optional[int], span_s: Optional[float]
    ) -> dict[str, Any]:
        """Rates of the record at ``path``, which stays loaded while its file is unchanged."""
        stat = path.stat()
        version = (path, stat.st_mtime_ns, stat.st_size)
        held = self._rates_record
        if held is None or held[0] != version:
            held = self._rates_record = (version, RatesRecord.load(path))
        record = held[1]
        return {
            "times": record.times,
            "density": record.density(),
            **record.rates(sample=sample, start=start, span_s=span_s),
        }

    def run_tail(self, name: str, member: str) -> dict[str, Any]:
        """Tails of a member's ``out`` and ``err`` files in a past run; ``member`` is its directory name."""
        if not _plain_name(member):
            raise api.ApiError(api.INVALID_PARAMS, "member must be a directory name")
        member_dir = self.run(name)[0] / member
        if not member_dir.is_dir():
            raise api.ApiError(api.NOT_FOUND, f"no member {member!r} in run {name!r}")
        return {"run": name, "member": member, **member_tails(member_dir)}


def _plain_name(name: Optional[str]) -> bool:
    """A single directory name: no separators, no traversal."""
    return bool(name) and "/" not in name and "\\" not in name and "\x00" not in name and name not in (".", "..")


def _run_of_system(manifest: Mapping[str, Any], system: SystemExport) -> bool:
    """Whether a run's manifest names ``system``, by system name or by a structure path under its
    export directory."""
    if manifest.get("system") == system.name:
        return True
    structure = manifest.get("system_structure")
    if not isinstance(structure, str):
        return False
    try:
        Path(structure).resolve().relative_to(system.export_dir.resolve())
    except (ValueError, OSError):
        return False
    return True


def _started(manifest: Mapping[str, Any]) -> float:
    """Start time of a run by its manifest; 0 where it gives none."""
    started = manifest.get("started")
    return float(started) if isinstance(started, (int, float)) and not isinstance(started, bool) else 0.0


def _run_summary(run_dir: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    members = manifest.get("members")

    def text(key: str) -> Optional[str]:
        value = manifest.get(key)
        return value if isinstance(value, str) else None

    return {
        "run": run_dir.name,
        "dir": str(run_dir),
        "started": _started(manifest),
        "mode": text("mode"),
        "compute_unit": text("compute_unit"),
        "deploy": text("deploy"),
        "launch": text("launch"),
        "member_count": len(members) if isinstance(members, list) else 0,
        "rates": (run_dir / RATES_FILE).is_file(),
    }


def member_tails(member_dir: Path) -> dict[str, list[str]]:
    """The last lines of each output file of a member directory, an unterminated last line included."""
    tails = {}
    for stream in STREAMS:
        lines, rest = tail_lines(member_dir / stream)
        if rest:
            lines = [*lines, rest.decode("utf-8", "replace")][-TAIL_LINES:]
        tails[stream] = lines
    return tails
