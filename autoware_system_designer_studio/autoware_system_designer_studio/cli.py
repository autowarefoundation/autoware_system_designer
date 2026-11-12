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

"""``autoware-system-designer-studio``: serve the studio over an install tree, or pack it for ``file://``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import signal
import sys
from pathlib import Path
from typing import Optional, Sequence

from .exports import resolve_install_root
from .pack import PackError, pack
from .serve import peers
from .serve.build import resolve_workspace
from .serve.launch import LaunchEnvError, capture_env
from .serve.server import BIND_HOST, HEARTBEAT_INTERVAL_S, ServeDaemon

logger = logging.getLogger("autoware_system_designer.studio")

PROG = "autoware-system-designer-studio"
DEFAULT_PORT = 8780
_UNIT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


def _unit_name(value: str) -> str:
    if not _UNIT_NAME.match(value):
        raise argparse.ArgumentTypeError(f"invalid compute unit name {value!r} (expected {_UNIT_NAME.pattern})")
    return value


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--install-root",
        help="install tree holding the exports (default: first COLCON_PREFIX_PATH entry, "
        "else the tree holding this package)",
    )
    parser.add_argument(
        "--record-dir",
        type=Path,
        help="measurement record root (default: $AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR, "
        "else $ROS_HOME/autoware_system_designer/records)",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog=PROG, description="Autoware System Designer studio.")
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser("serve", help=f"serve the studio and the control API on http://{BIND_HOST}")
    _add_common(serve)
    serve.add_argument(
        "--workspace",
        help="colcon workspace builds run in (default: parent of the install tree when it holds src/; "
        "without one, builds are unavailable)",
    )
    serve.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"TCP port, 0 picks a free one (default {DEFAULT_PORT})"
    )
    serve.add_argument(
        "--compute-unit",
        dest="compute_units",
        action="append",
        type=_unit_name,
        help="compute unit local to this daemon (repeatable; default: every onboard unit)",
    )
    serve.add_argument(
        "--peer",
        dest="peers",
        action="append",
        default=[],
        metavar="UNIT=URL",
        help="daemon serving another compute unit, as <unit>=http://<host>:<port> (repeatable; "
        "replaces the site file's binding of the unit)",
    )
    serve.add_argument(
        "--peer-ssh",
        dest="peer_ssh",
        action="append",
        default=[],
        metavar="UNIT=DESTINATION",
        help="SSH destination the unit's peer is forwarded from, for a peer on another device (repeatable)",
    )
    serve.add_argument(
        "--site-file",
        type=Path,
        help=f"YAML file binding compute units to peer daemons under 'peers' (default: ${peers.SITE_FILE_ENV})",
    )
    serve.add_argument(
        "--env-script",
        type=Path,
        help="setup script sourced once into the environment launch jobs run in "
        "(default: this process's environment; start serve from a sourced shell)",
    )
    serve.add_argument(
        "--rviz-config",
        type=Path,
        help="RViz configuration opened by rviz.start (default: autoware_launch's autoware.rviz "
        "under the launch environment's ament prefixes, else none)",
    )
    serve.add_argument(
        "--heartbeat-interval",
        type=float,
        default=HEARTBEAT_INTERVAL_S,
        help=f"seconds between event-stream heartbeats (default {HEARTBEAT_INTERVAL_S:g})",
    )

    pack_cmd = commands.add_parser("pack", help="write the studio pages of the exports to a directory for file://")
    _add_common(pack_cmd)
    pack_cmd.add_argument("-o", "--output", type=Path, required=True, help="directory to write (created)")
    pack_cmd.add_argument(
        "--system",
        dest="systems",
        action="append",
        help="system to include, by name or package:name (repeatable; default: every exported system)",
    )

    args = parser.parse_args(argv)
    if args.command == "serve":
        if not 0 <= args.port <= 65535:
            serve.error("--port must be within 0..65535")
        if args.rviz_config is not None and not args.rviz_config.is_file():
            serve.error(f"--rviz-config {args.rviz_config} is not a file")
        if args.heartbeat_interval <= 0:
            serve.error("--heartbeat-interval must be positive")
        try:
            site_file = peers.site_file_path(args.site_file)
            from_file = [] if site_file is None else peers.load_site_file(site_file)
            args.peer_specs = peers.merge_specs(from_file, [peers.parse_peer(text) for text in args.peers])
            args.peer_specs = peers.with_ssh(args.peer_specs, [peers.parse_peer_ssh(text) for text in args.peer_ssh])
            peers.validate_specs(args.peer_specs)
        except peers.PeerConfigError as exc:
            serve.error(str(exc))
        local = set(args.compute_units or ()) & {spec.unit for spec in args.peer_specs}
        if local:
            serve.error(f"compute unit(s) {', '.join(sorted(local))} are both local and bound to a peer")
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


def _install_root(args: argparse.Namespace) -> Path:
    install_root: Optional[Path] = resolve_install_root(args.install_root)
    if install_root is None or not install_root.is_dir():
        sys.exit("no install tree; pass --install-root or source the workspace setup script")
    return install_root


def _run_serve(args: argparse.Namespace) -> None:
    install_root = _install_root(args)
    try:
        launch_env = capture_env(args.env_script)
    except LaunchEnvError as exc:
        sys.exit(str(exc))
    daemon = ServeDaemon(
        install_root,
        port=args.port,
        served_units=args.compute_units,
        workspace=resolve_workspace(install_root, args.workspace),
        record_root=args.record_dir,
        launch_env=launch_env,
        env_script=args.env_script,
        rviz_config=args.rviz_config,
        peer_specs=args.peer_specs,
        heartbeat_s=args.heartbeat_interval,
    )
    try:
        asyncio.run(_serve(daemon))
    except OSError as exc:
        sys.exit(f"cannot serve on {BIND_HOST}:{args.port}: {exc}")


def _run_pack(args: argparse.Namespace) -> None:
    install_root = _install_root(args)
    output = args.output.expanduser().resolve()
    try:
        written, errors = pack(install_root, output, systems=args.systems, record_root=args.record_dir)
    except PackError as exc:
        sys.exit(str(exc))
    for error in errors:
        logger.warning("skipped %s: %s", error.path, error.message)
    print(f"packed {len(written)} file(s); open file://{output / 'index.html'}", flush=True)


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level, format="[%(levelname)s] %(name)s: %(message)s")
    if args.command == "serve":
        _run_serve(args)
    else:
        _run_pack(args)


if __name__ == "__main__":
    main()
