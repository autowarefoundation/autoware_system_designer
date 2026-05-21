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

Mirrors play_launch ``graceful_kill`` in
``src/play_launch/src/member_actor/regular_node_actor.rs`` — SIGTERM on the
process group, wait up to N seconds, then SIGKILL on the process group.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
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
    """Spawn *cmd* as the leader of a fresh process group.

    Using ``start_new_session=True`` makes the child its own session leader
    and process-group leader (pgid == pid). All descendants inherit that
    group, so we can kill the entire tree with a single ``killpg``.
    """
    stdout_file = open(stdout_path, "ab", buffering=0) if stdout_path else asyncio.subprocess.DEVNULL
    stderr_file = open(stderr_path, "ab", buffering=0) if stderr_path else asyncio.subprocess.DEVNULL

    try:
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


def _killpg(pid: int, sig: int) -> None:
    """Send *sig* to the process group led by *pid*.

    Tolerates ESRCH (group already gone) silently; logs everything else.
    """
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError as e:
        logger.warning("killpg(%d, %d) permission denied: %s", pid, sig, e)
    except OSError as e:
        logger.warning("killpg(%d, %d) failed: %s", pid, sig, e)


async def graceful_kill(
    proc: asyncio.subprocess.Process,
    *,
    name: str,
    timeout: float = 5.0,
) -> Optional[int]:
    """Send SIGTERM to the process group, wait up to *timeout*, then SIGKILL.

    Returns the final exit code (or None if the wait raised).
    """
    pid = proc.pid
    if proc.returncode is not None:
        return proc.returncode

    logger.debug("[%s] SIGTERM pgid=%d", name, pid)
    _killpg(pid, signal.SIGTERM)

    try:
        return await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "[%s] did not exit within %.1fs, sending SIGKILL to pgid=%d",
            name,
            timeout,
            pid,
        )
        _killpg(pid, signal.SIGKILL)
        try:
            return await proc.wait()
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] wait after SIGKILL failed: %s", name, e)
            return None
