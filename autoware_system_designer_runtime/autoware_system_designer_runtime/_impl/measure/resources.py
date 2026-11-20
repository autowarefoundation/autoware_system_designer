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

"""CPU, memory and GPU use of the running members, read from ``/proc`` and the GPU driver.

A member is its whole process group (a ``ros2 run`` leader and the node it starts), so its CPU and
memory are the sums over the processes of the group. CPU is in cores (1.0 = one core busy over the
span), from the difference of two readings; memory is the resident set (RSS) of every reading and
the proportional set (PSS, shared pages split among their users) at a slower interval, since
``smaps_rollup`` walks the whole address space. Nothing runs inside the nodes: the readings come
from this process, and only while someone asks for them.

GPU: per device load and memory through NVML (``libnvidia-ml.so.1``) when the driver has it, per
process memory and utilization where NVML reports the members' pids; NVML reports host pids, so in
a nested pid namespace (a container) only the device totals are shown. A Jetson has no NVML: its
device load comes from sysfs, and its GPU memory is the unified RAM already in each process's RSS.
"""

from __future__ import annotations

import ctypes
import glob
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_TICK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
_PAGE_KB = (os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096) // 1024
# Interval of the PSS reads, and of the scans for the processes of each group.
PSS_INTERVAL_S = 10.0
GROUP_SCAN_S = 5.0
# Device load files of Jetson GPUs, per mille.
_SYSFS_GPU_LOAD = (
    "/sys/devices/gpu.0/load",
    "/sys/devices/platform/gpu.0/load",
    "/sys/devices/platform/*.gpu/load",
    "/sys/devices/platform/bus@0/*.gpu/load",
)


@dataclass
class _Proc:
    """One process as read from ``/proc/<pid>/stat``."""

    start: int  # start time since boot in ticks, tells a reused pid apart
    ticks: int  # user + system CPU time
    rss_kb: int
    threads: int


@dataclass
class Reading:
    """One reading of every member: its processes by (pid, start), and the host's counters."""

    t: float  # monotonic seconds
    members: dict[str, dict[tuple[int, int], _Proc]] = field(default_factory=dict)
    leaders: dict[str, int] = field(default_factory=dict)
    host_busy: int = 0
    host_total: int = 0
    mem_total_kb: int = 0
    mem_available_kb: int = 0
    gpu: Optional[dict[str, Any]] = None


def _read_stat(pid: int) -> Optional[tuple[int, _Proc]]:
    """Process group and counters of a process; None once it is gone."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            text = handle.read()
    except OSError:
        return None
    fields = text[text.rfind(b")") + 2 :].split()
    try:
        return int(fields[2]), _Proc(
            start=int(fields[19]),
            ticks=int(fields[11]) + int(fields[12]),
            rss_kb=int(fields[21]) * _PAGE_KB,
            threads=int(fields[17]),
        )
    except (IndexError, ValueError):
        return None


def _read_pss_kb(pid: int) -> Optional[int]:
    try:
        with open(f"/proc/{pid}/smaps_rollup", "rb") as handle:
            for line in handle:
                if line.startswith(b"Pss:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _host_cpu() -> tuple[int, int]:
    """Busy and total ticks of every CPU since boot."""
    try:
        with open("/proc/stat", "rb") as handle:
            values = [int(v) for v in handle.readline().split()[1:]]
    except (OSError, ValueError):
        return 0, 0
    idle = sum(values[3:5])  # idle, iowait
    total = sum(values[:8])  # guest time is already in user
    return total - idle, total


def _host_memory() -> tuple[int, int]:
    total = available = 0
    try:
        with open("/proc/meminfo", "rb") as handle:
            for line in handle:
                if line.startswith(b"MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith(b"MemAvailable:"):
                    available = int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return total, available


def nested_pid_namespace() -> bool:
    """Whether this process sees other pids than the host does (a container's pid namespace)."""
    try:
        with open("/proc/self/status", "rb") as handle:
            for line in handle:
                if line.startswith(b"NSpid:"):
                    return len(line.split()) > 2
    except OSError:
        pass
    return False


# ---- NVML ---------------------------------------------------------------------------------------


class _Utilization(ctypes.Structure):
    _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]


class _Memory(ctypes.Structure):
    _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]


class _ProcessInfoV2(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint),
        ("usedGpuMemory", ctypes.c_ulonglong),
        ("gpuInstanceId", ctypes.c_uint),
        ("computeInstanceId", ctypes.c_uint),
    ]


class _ProcessInfoV1(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_uint), ("usedGpuMemory", ctypes.c_ulonglong)]


class _ProcessSample(ctypes.Structure):
    _fields_ = [
        ("pid", ctypes.c_uint),
        ("timeStamp", ctypes.c_ulonglong),
        ("smUtil", ctypes.c_uint),
        ("memUtil", ctypes.c_uint),
        ("encUtil", ctypes.c_uint),
        ("decUtil", ctypes.c_uint),
    ]


_NVML_SUCCESS = 0
_NVML_ERROR_INSUFFICIENT_SIZE = 7
# NVML's "not available" memory value.
_NVML_NA = (1 << 64) - 1


class Nvml:
    """The few NVML calls the sampler needs, through ctypes; ``open`` returns None without a driver."""

    def __init__(self, lib: ctypes.CDLL) -> None:
        self._lib = lib
        count = ctypes.c_uint(0)
        if lib.nvmlDeviceGetCount_v2(ctypes.byref(count)) != _NVML_SUCCESS:
            raise OSError("nvmlDeviceGetCount failed")
        self.handles = []
        for index in range(count.value):
            handle = ctypes.c_void_p()
            if lib.nvmlDeviceGetHandleByIndex_v2(index, ctypes.byref(handle)) == _NVML_SUCCESS:
                self.handles.append((index, handle))
        # Per device, the newest process sample time read.
        self._seen: dict[int, int] = {}
        self._processes, self._info = self._process_call()

    @classmethod
    def open(cls) -> Optional["Nvml"]:
        try:
            lib = ctypes.CDLL("libnvidia-ml.so.1")
            if lib.nvmlInit_v2() != _NVML_SUCCESS:
                return None
            return cls(lib)
        except (OSError, AttributeError) as exc:
            logger.debug("NVML unavailable: %s", exc)
            return None

    def _process_call(self) -> tuple[list[Callable], type]:
        for suffix, info in (("_v3", _ProcessInfoV2), ("_v2", _ProcessInfoV2), ("", _ProcessInfoV1)):
            calls = [
                getattr(self._lib, f"nvmlDevice{kind}RunningProcesses{suffix}", None)
                for kind in ("Compute", "Graphics")
            ]
            if all(calls):
                return calls, info
        return [], _ProcessInfoV1

    def name(self, handle: ctypes.c_void_p) -> str:
        buffer = ctypes.create_string_buffer(96)
        if self._lib.nvmlDeviceGetName(handle, buffer, len(buffer)) != _NVML_SUCCESS:
            return "GPU"
        return buffer.value.decode(errors="replace")

    def device(self, handle: ctypes.c_void_p) -> dict[str, Any]:
        out: dict[str, Any] = {}
        util = _Utilization()
        if self._lib.nvmlDeviceGetUtilizationRates(handle, ctypes.byref(util)) == _NVML_SUCCESS:
            out["util"] = util.gpu
        memory = _Memory()
        if self._lib.nvmlDeviceGetMemoryInfo(handle, ctypes.byref(memory)) == _NVML_SUCCESS:
            out["mem_used_kb"] = memory.used // 1024
            out["mem_total_kb"] = memory.total // 1024
        return out

    def process_memory(self, handle: ctypes.c_void_p) -> dict[int, int]:
        """GPU memory in KB per pid, compute and graphics contexts summed."""
        used: dict[int, int] = {}
        for call in self._processes:
            count = ctypes.c_uint(0)
            result = call(handle, ctypes.byref(count), None)
            if result not in (_NVML_SUCCESS, _NVML_ERROR_INSUFFICIENT_SIZE) or not count.value:
                continue
            count.value += 8  # room for processes started since the size query
            infos = (self._info * count.value)()
            if call(handle, ctypes.byref(count), infos) != _NVML_SUCCESS:
                continue
            for info in infos[: count.value]:
                if info.usedGpuMemory != _NVML_NA:
                    used[info.pid] = used.get(info.pid, 0) + info.usedGpuMemory // 1024
                else:
                    used.setdefault(info.pid, 0)
        return used

    def process_utilization(self, index: int, handle: ctypes.c_void_p) -> dict[int, int]:
        """SM utilization in percent per pid over the samples since the last call; empty where the
        device does not report it."""
        call = getattr(self._lib, "nvmlDeviceGetProcessUtilization", None)
        if call is None:
            return {}
        since = ctypes.c_ulonglong(self._seen.get(index, 0))
        count = ctypes.c_uint(0)
        result = call(handle, None, ctypes.byref(count), since)
        if result != _NVML_ERROR_INSUFFICIENT_SIZE or not count.value:
            return {}
        samples = (_ProcessSample * count.value)()
        if call(handle, samples, ctypes.byref(count), since) != _NVML_SUCCESS:
            return {}
        best: dict[int, int] = {}
        newest = since.value
        for sample in samples[: count.value]:
            newest = max(newest, sample.timeStamp)
            best[sample.pid] = max(best.get(sample.pid, 0), sample.smUtil)
        self._seen[index] = newest
        return best


class GpuReader:
    """Device and per-process GPU use; ``source`` says where it comes from, None when nowhere."""

    def __init__(self, *, nvml: Optional[Nvml] = None, sysfs: Optional[list[str]] = None) -> None:
        self._nvml = nvml
        self._sysfs = sysfs if sysfs is not None else []
        self.per_process = nvml is not None and not nested_pid_namespace()
        self._names = [nvml.name(handle) for _, handle in nvml.handles] if nvml is not None else []

    @classmethod
    def detect(cls) -> "GpuReader":
        nvml = Nvml.open()
        if nvml is not None and nvml.handles:
            return cls(nvml=nvml)
        found = sorted({path for pattern in _SYSFS_GPU_LOAD for path in glob.glob(pattern)})
        return cls(sysfs=found)

    @property
    def source(self) -> Optional[str]:
        if self._nvml is not None:
            return "nvml"
        return "sysfs" if self._sysfs else None

    def note(self) -> str:
        if self._nvml is not None:
            if self.per_process:
                return ""
            return "per-process GPU use is not shown: NVML reports host pids, this runtime runs in a pid namespace"
        if self._sysfs:
            return "device load only; GPU memory is unified with RAM and counts in each process's memory"
        return "no GPU found"

    def read(self) -> dict[str, Any]:
        """``{devices: [...], processes: {pid: {mem_kb, util?}}}``."""
        devices: list[dict[str, Any]] = []
        processes: dict[int, dict[str, int]] = {}
        if self._nvml is not None:
            for (index, handle), name in zip(self._nvml.handles, self._names):
                devices.append({"index": index, "name": name, **self._nvml.device(handle)})
                if not self.per_process:
                    continue
                for pid, kb in self._nvml.process_memory(handle).items():
                    entry = processes.setdefault(pid, {"mem_kb": 0})
                    entry["mem_kb"] += kb
                for pid, util in self._nvml.process_utilization(index, handle).items():
                    entry = processes.setdefault(pid, {"mem_kb": 0})
                    entry["util"] = entry.get("util", 0) + util
        for index, path in enumerate(self._sysfs):
            try:
                with open(path, "rb") as handle:
                    load = int(handle.read().strip())
            except (OSError, ValueError):
                continue
            devices.append({"index": index, "name": "GPU", "util": round(load / 10)})
        return {"devices": devices, "processes": processes}


# ---- sampler ------------------------------------------------------------------------------------


class ResourceMonitor:
    """Reads the CPU, memory and GPU use of the members whose leader pids ``leaders()`` returns.

    ``read`` takes a reading, ``usage`` turns two readings into the use over the span between them,
    so concurrent callers keep readings of their own.
    """

    def __init__(self, leaders: Callable[[], dict[str, int]], *, gpu: Optional[GpuReader] = None) -> None:
        self._leaders = leaders
        self._gpu = gpu
        self._gpu_detected = gpu is not None
        # Leader pid -> pids of its group, and the time of the scan that found them.
        self._groups: dict[int, set[int]] = {}
        self._scanned = 0.0
        # (pid, start) -> (PSS in KB, monotonic time of the read).
        self._pss: dict[tuple[int, int], tuple[int, float]] = {}

    @property
    def gpu(self) -> GpuReader:
        if not self._gpu_detected:
            self._gpu = GpuReader.detect()
            self._gpu_detected = True
        assert self._gpu is not None
        return self._gpu

    def read(self) -> Reading:
        now = time.monotonic()
        leaders = self._leaders()
        reading = Reading(t=now, leaders=dict(leaders))
        groups = self._group_pids(set(leaders.values()), now)
        for path, leader in leaders.items():
            procs: dict[tuple[int, int], _Proc] = {}
            for pid in groups.get(leader, {leader}):
                stat = _read_stat(pid)
                if stat is not None and stat[0] == leader:
                    procs[(pid, stat[1].start)] = stat[1]
            reading.members[path] = procs
        reading.host_busy, reading.host_total = _host_cpu()
        reading.mem_total_kb, reading.mem_available_kb = _host_memory()
        try:
            reading.gpu = self.gpu.read()
        except Exception:  # noqa: BLE001 - a driver error must not stop the CPU and memory readings
            logger.exception("reading the GPU failed")
        self._refresh_pss(reading, now)
        return reading

    def usage(self, before: Optional[Reading], after: Reading) -> dict[str, Any]:
        """Use over the span between two readings; ``cpu`` is left out where there is no earlier one."""
        span = max(after.t - before.t, 1e-3) if before is not None else None
        gpu = after.gpu or {"devices": [], "processes": {}}
        gpu_procs: dict[int, dict[str, int]] = gpu.get("processes", {})
        members: dict[str, dict[str, Any]] = {}
        for path, procs in after.members.items():
            if not procs:
                continue
            entry: dict[str, Any] = {
                "pid": after.leaders.get(path),
                "procs": len(procs),
                "threads": sum(p.threads for p in procs.values()),
                "rss_kb": sum(p.rss_kb for p in procs.values()),
            }
            earlier = before.members.get(path) if before is not None else None
            if span is not None and earlier is not None:
                ticks = sum(proc.ticks - earlier[key].ticks for key, proc in procs.items() if key in earlier)
                entry["cpu"] = round(max(ticks, 0) / _TICK / span, 3)
            pss = [self._pss.get(key) for key in procs]
            if pss and all(value is not None for value in pss):
                entry["pss_kb"] = sum(value[0] for value in pss)
            if self._gpu is not None and self._gpu.per_process:
                used = [gpu_procs[pid] for pid, _ in procs if pid in gpu_procs]
                if used:
                    entry["gpu_mem_kb"] = sum(u.get("mem_kb", 0) for u in used)
                    utils = [u["util"] for u in used if "util" in u]
                    if utils:
                        entry["gpu_util"] = sum(utils)
            members[path] = entry
        host: dict[str, Any] = {
            "cpus": os.cpu_count() or 1,
            "mem_total_kb": after.mem_total_kb,
            "mem_used_kb": max(after.mem_total_kb - after.mem_available_kb, 0),
        }
        if before is not None and after.host_total > before.host_total:
            busy = (after.host_busy - before.host_busy) / (after.host_total - before.host_total)
            host["cpu"] = round(busy * host["cpus"], 3)
        reader = self._gpu
        return {
            "time_ns": time.time_ns(),
            "interval_s": round(span, 3) if span is not None else None,
            "host": host,
            "gpu": {
                "source": reader.source if reader is not None else None,
                "per_process": bool(reader and reader.per_process),
                "note": reader.note() if reader is not None else "",
                "devices": gpu.get("devices", []),
            },
            "members": members,
        }

    def _group_pids(self, leaders: set[int], now: float) -> dict[int, set[int]]:
        """Pids per process group led by one of ``leaders``, rescanned at an interval and whenever a
        leader is new."""
        if now - self._scanned < GROUP_SCAN_S and leaders <= set(self._groups):
            return self._groups
        groups: dict[int, set[int]] = {leader: {leader} for leader in leaders}
        try:
            entries = os.listdir("/proc")
        except OSError:
            entries = []
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            try:
                with open(f"/proc/{pid}/stat", "rb") as handle:
                    text = handle.read()
                pgid = int(text[text.rfind(b")") + 2 :].split()[2])
            except (OSError, ValueError, IndexError):
                continue
            if pgid in groups:
                groups[pgid].add(pid)
        self._groups = groups
        self._scanned = now
        return groups

    def _refresh_pss(self, reading: Reading, now: float) -> None:
        live: set[tuple[int, int]] = set()
        for procs in reading.members.values():
            for key in procs:
                live.add(key)
                held = self._pss.get(key)
                if held is None or now - held[1] >= PSS_INTERVAL_S:
                    value = _read_pss_kb(key[0])
                    if value is not None:
                        self._pss[key] = (value, now)
        for key in [key for key in self._pss if key not in live]:
            del self._pss[key]


def format_resources(usage: dict[str, Any], pattern: Optional[str] = None) -> list[str]:
    """Plain-text table of a resource reading, members whose path contains *pattern* only."""
    host = usage["host"]
    lines = [
        f"resources over {usage['interval_s'] or 0:g} s: host CPU {host.get('cpu', 0):.2f} of {host['cpus']} cores, "
        f"memory {host['mem_used_kb'] / 1024:.0f} of {host['mem_total_kb'] / 1024:.0f} MB"
    ]
    for device in usage["gpu"]["devices"]:
        memory = ""
        if "mem_total_kb" in device:
            memory = f", memory {device['mem_used_kb'] / 1024:.0f} of {device['mem_total_kb'] / 1024:.0f} MB"
        lines.append(f"GPU {device['index']} {device['name']}: {device.get('util', 0)} %{memory}")
    if usage["gpu"]["note"]:
        lines.append(usage["gpu"]["note"])
    rows = sorted(usage["members"].items(), key=lambda item: -item[1].get("cpu", 0))
    for path, entry in rows:
        if pattern and pattern not in path:
            continue
        gpu = f"  gpu {entry['gpu_mem_kb'] / 1024:7.1f} MB" if "gpu_mem_kb" in entry else ""
        lines.append(f"  {path:<60} cpu {entry.get('cpu', 0):5.2f}  rss {entry['rss_kb'] / 1024:7.1f} MB{gpu}")
    return lines
