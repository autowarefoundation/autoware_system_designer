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

"""Process-group spawn and graceful kill.

SIGINT on the process group, wait up to N seconds, then SIGKILL on the process group.

SIGINT is the stop signal of ROS 2 launch: rclcpp and rclpy shut their context down on it and leave
the ROS graph. SIGTERM is a failure signal to a glog failure handler, which ends the process with
its DDS participant still announced.

A member is its whole process group: it has ended once the leader has exited and no process of the
group is left.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from pathlib import Path
from typing import Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


async def spawn_pgrp(
    cmd: Sequence[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    stdout_path: Optional[Path] = None,
    stderr_path: Optional[Path] = None,
) -> asyncio.subprocess.Process:
    """Spawn *cmd* in a new session (pgid == pid) so the entire tree is killable via killpg."""
    stdout_file = None
    stderr_file = None
    try:
        stdout_file = open(stdout_path, "ab", buffering=0) if stdout_path else asyncio.subprocess.DEVNULL
        stderr_file = open(stderr_path, "ab", buffering=0) if stderr_path else asyncio.subprocess.DEVNULL
        return await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            stdout=stdout_file,
            stderr=stderr_file,
            stdin=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
    finally:
        # asyncio dup'd the fds into the child; we can close our handles.
        for f in (stdout_file, stderr_file):
            if hasattr(f, "close"):
                try:
                    f.close()
                except Exception:
                    pass


# Signal that asks a member to stop.
STOP_SIGNAL = signal.SIGINT
# Interval at which a group is looked at while it ends.
GROUP_POLL_S = 0.1
# Wait for a group to be gone after SIGKILL.
KILL_WAIT_S = 5.0


def _killpg(pid: int, sig: int) -> None:
    """Send *sig* to process group; tolerates ESRCH (already gone) silently."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        logger.warning("killpg(%d, %d) permission denied: %s", pid, sig, e)
    except OSError as e:
        logger.warning("killpg(%d, %d) failed: %s", pid, sig, e)


def _group_exists(pgid: int) -> bool:
    """Whether the group holds a process, running or zombie."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _running_groups() -> Optional[set[int]]:
    """The groups holding a running process, zombies not counted; None when /proc is unreadable."""
    try:
        entries = os.listdir("/proc")
    except OSError:
        return None
    groups = set()
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as f:
                # "pid (comm) state ppid pgrp ..."; comm may hold spaces and parentheses.
                fields = f.read().rpartition(")")[2].split()
        except OSError:
            continue
        if len(fields) >= 3 and fields[0] != "Z" and fields[2].isdigit():
            groups.add(int(fields[2]))
    return groups


def group_alive(pgid: int) -> bool:
    """Whether a process of the group still runs; zombies awaiting their reaper do not count."""
    if not _group_exists(pgid):
        return False
    groups = _running_groups()
    return groups is None or pgid in groups


class _GroupScan:
    """The running groups as of one /proc scan, shared by every group polled within half a poll interval."""

    def __init__(self) -> None:
        self._at = float("-inf")
        self._groups: Optional[set[int]] = None

    def alive(self, pgid: int) -> bool:
        if not _group_exists(pgid):
            return False
        now = time.monotonic()
        if now - self._at >= GROUP_POLL_S / 2:
            self._groups = _running_groups()
            self._at = now
        return self._groups is None or pgid in self._groups


_scan = _GroupScan()


async def _group_gone(pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    # The first look is a scan of its own; a shared one may predate the group's processes.
    alive = group_alive
    while alive(pgid):
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(GROUP_POLL_S)
        alive = _scan.alive
    return True


async def _kill_group(pgid: int, *, name: str) -> bool:
    _killpg(pgid, signal.SIGKILL)
    if await _group_gone(pgid, KILL_WAIT_S):
        return True
    logger.error("[%s] survived SIGKILL (pgid=%d), giving up", name, pgid)
    return False


async def reap_group(pgid: int, *, name: str, timeout: float = 5.0) -> None:
    """End the processes a leader that has exited left in its group: SIGINT, then SIGKILL after *timeout*."""
    if not group_alive(pgid):
        return
    logger.warning("[%s] exited with processes left in pgid=%d, sending %s", name, pgid, STOP_SIGNAL.name)
    _killpg(pgid, STOP_SIGNAL)
    if await _group_gone(pgid, timeout):
        return
    logger.warning("[%s] processes left after %.1fs, sending SIGKILL to pgid=%d", name, timeout, pgid)
    await _kill_group(pgid, name=name)


async def graceful_kill(
    proc: asyncio.subprocess.Process,
    *,
    name: str,
    timeout: float = 5.0,
) -> Optional[int]:
    """Send SIGINT to the process group, wait up to *timeout* for the whole group, then SIGKILL.

    Returns the leader's exit code (or None if it could not be waited for). A leader that has
    exited already has what it left in its group ended the same way.
    """
    pid = proc.pid
    if proc.returncode is not None:
        await reap_group(pid, name=name, timeout=timeout)
        return proc.returncode

    logger.debug("[%s] %s pgid=%d", name, STOP_SIGNAL.name, pid)
    _killpg(pid, STOP_SIGNAL)

    deadline = time.monotonic() + timeout
    try:
        exit_code = await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[%s] did not exit within %.1fs, sending SIGKILL to pgid=%d",
            name,
            timeout,
            pid,
        )
        _killpg(pid, signal.SIGKILL)
        try:
            exit_code = await asyncio.wait_for(proc.wait(), timeout=KILL_WAIT_S)
        except asyncio.TimeoutError:
            logger.error("[%s] survived SIGKILL (pgid=%d), giving up", name, pid)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] wait after SIGKILL failed: %s", name, e)
            return None
        if not await _group_gone(pid, KILL_WAIT_S):
            logger.error("[%s] survived SIGKILL (pgid=%d), giving up", name, pid)
        return exit_code

    if not await _group_gone(pid, max(deadline - time.monotonic(), 0.0)):
        logger.warning(
            "[%s] exited with processes left in its group after %.1fs, sending SIGKILL to pgid=%d",
            name,
            timeout,
            pid,
        )
        await _kill_group(pid, name=name)
    return exit_code
