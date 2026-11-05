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

"""``autoware-system-designer-serve``: loopback daemon over the exported systems of an install tree."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import signal
import sys
from pathlib import Path
from typing import Optional, Sequence

from ._impl.serve.build import resolve_workspace
from ._impl.serve.exports import resolve_install_root
from ._impl.serve.server import BIND_HOST, HEARTBEAT_INTERVAL_S, ServeDaemon

logger = logging.getLogger("autoware_system_designer.serve")

DEFAULT_PORT = 8780
_UNIT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _unit_name(value: str) -> str:
    if not _UNIT_NAME.match(value):
        raise argparse.ArgumentTypeError(f"invalid compute unit name {value!r} (expected {_UNIT_NAME.pattern})")
    return value


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="autoware-system-designer-serve",
        description=f"Serve the exported system visualization and the control API on http://{BIND_HOST}.",
    )
    parser.add_argument(
        "--install-root",
        help="install tree to serve (default: first COLCON_PREFIX_PATH entry, else the tree holding this package)",
    )
    parser.add_argument(
        "--workspace",
        help="colcon workspace builds run in (default: parent of the install tree when it holds src/; "
        "without one, builds are unavailable)",
    )
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"TCP port, 0 picks a free one (default {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--compute-unit",
        dest="compute_units",
        action="append",
        type=_unit_name,
        help="compute unit local to this daemon (repeatable; default: every onboard unit)",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=float,
        default=HEARTBEAT_INTERVAL_S,
        help=f"seconds between event-stream heartbeats (default {HEARTBEAT_INTERVAL_S:g})",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be within 0..65535")
    if args.heartbeat_interval <= 0:
        parser.error("--heartbeat-interval must be positive")
    return args


async def _serve(daemon: ServeDaemon) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    port = await daemon.start()
    print(f"serving {daemon.install_root} on http://{BIND_HOST}:{port}/", flush=True)
    try:
        await stop.wait()
    finally:
        await daemon.stop()


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format="[%(levelname)s] %(name)s: %(message)s")
    install_root: Optional[Path] = resolve_install_root(args.install_root)
    if install_root is None or not install_root.is_dir():
        sys.exit("no install tree to serve; pass --install-root or source the workspace setup script")
    daemon = ServeDaemon(
        install_root,
        port=args.port,
        served_units=args.compute_units,
        workspace=resolve_workspace(install_root, args.workspace),
        heartbeat_s=args.heartbeat_interval,
    )
    try:
        asyncio.run(_serve(daemon))
    except OSError as exc:
        sys.exit(f"cannot serve on {BIND_HOST}:{args.port}: {exc}")


if __name__ == "__main__":
    main()
