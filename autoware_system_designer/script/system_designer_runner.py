#!/usr/bin/env python3

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

import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import List


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "on", "yes", "y"}


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Unified wrapper for command logging and deployment strict/non-strict policy.")
    )
    subparsers = parser.add_subparsers(dest="mode")

    run_parser = subparsers.add_parser(
        "run",
        help="Run any command while logging stdout/stderr (tee behavior)",
    )
    run_parser.add_argument("--log-file", required=True, help="Path to combined log file")
    run_parser.add_argument("--append", action="store_true", help="Append to log file")
    run_parser.add_argument(
        "--print-stdout",
        action="store_true",
        help="Also print stdout to terminal (stderr is always printed)",
    )
    run_parser.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        help="Command to run, preceded by --",
    )

    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Run deployment build with strict/non-strict policy",
    )
    deploy_parser.add_argument("--log-file", required=True, help="Path to combined log file")
    deploy_parser.add_argument("--append", action="store_true", help="Append to log file")
    deploy_parser.add_argument(
        "--print-stdout",
        action="store_true",
        help="Also print child stdout to terminal",
    )
    deploy_parser.add_argument(
        "--print-level",
        default="ERROR",
        help="Print level forwarded to AUTOWARE_SYSTEM_DESIGNER_PRINT_LEVEL",
    )
    deploy_parser.add_argument(
        "--strict",
        choices=("auto", "on", "off"),
        default="auto",
        help="Failure policy (default: auto -> AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT)",
    )
    deploy_parser.add_argument("deployment_process_script", help="Path to deployment_process.py")
    deploy_parser.add_argument("deployment_file", help="Input deployment/design/system target")
    deploy_parser.add_argument("resource_dir", help="System designer resource directory")
    deploy_parser.add_argument("output_root_dir", help="Deployment output root")
    deploy_parser.add_argument(
        "workspace_yaml",
        nargs="?",
        default=None,
        help="Optional workspace.yaml path",
    )

    args = parser.parse_args(argv)
    if not args.mode:
        parser.print_help()
        raise SystemExit(2)
    return args


def _pump_stream(
    *,
    stream,
    log_fp,
    log_lock: threading.Lock,
    stop_event: threading.Event,
    terminal_stream,
    print_to_terminal: bool,
) -> None:
    while not stop_event.is_set():
        try:
            chunk = stream.read(4096)
        except Exception:
            break
        if not chunk:
            break

        with log_lock:
            log_fp.write(chunk)
            log_fp.flush()

        if print_to_terminal:
            terminal_stream.buffer.write(chunk)
            terminal_stream.flush()


def _run_with_tee(command: List[str], *, log_file: str, append: bool, print_stdout: bool, env) -> int:
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "ab" if append else "wb"

    with open(log_path, mode) as log_fp:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        assert proc.stdout is not None
        assert proc.stderr is not None

        log_lock = threading.Lock()
        stop_event = threading.Event()

        t_out = threading.Thread(
            target=_pump_stream,
            kwargs={
                "stream": proc.stdout,
                "log_fp": log_fp,
                "log_lock": log_lock,
                "stop_event": stop_event,
                "terminal_stream": sys.stdout,
                "print_to_terminal": print_stdout,
            },
            daemon=True,
        )
        t_err = threading.Thread(
            target=_pump_stream,
            kwargs={
                "stream": proc.stderr,
                "log_fp": log_fp,
                "log_lock": log_lock,
                "stop_event": stop_event,
                "terminal_stream": sys.stderr,
                "print_to_terminal": True,
            },
            daemon=True,
        )

        t_out.start()
        t_err.start()

        return_code = proc.wait()

        stop_event.set()
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.stderr.close()
        except Exception:
            pass

        t_out.join(timeout=1.0)
        t_err.join(timeout=1.0)

        return return_code


def _resolve_strict_mode(strict_arg: str) -> bool:
    if strict_arg == "on":
        return True
    if strict_arg == "off":
        return False
    return _truthy(os.environ.get("AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT", "0"))


def main(argv: List[str]) -> int:
    args = _parse_args(argv)

    if args.mode == "run":
        if not args.cmd or args.cmd[0] != "--":
            raise SystemExit("system_designer_runner.py run: expected command after '--'")
        cmd = args.cmd[1:]
        if not cmd:
            raise SystemExit("system_designer_runner.py run: empty command")

        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        return _run_with_tee(
            cmd,
            log_file=args.log_file,
            append=bool(args.append),
            print_stdout=bool(args.print_stdout),
            env=env,
        )

    strict = _resolve_strict_mode(args.strict)

    env = os.environ.copy()
    env["AUTOWARE_SYSTEM_DESIGNER_PRINT_LEVEL"] = args.print_level
    env.setdefault("PYTHONUNBUFFERED", "1")

    command = [
        sys.executable,
        "-u",
        args.deployment_process_script,
        args.deployment_file,
        args.resource_dir,
        args.output_root_dir,
    ]
    if args.workspace_yaml:
        command.append(args.workspace_yaml)

    return_code = _run_with_tee(
        command,
        log_file=args.log_file,
        append=bool(args.append),
        print_stdout=bool(args.print_stdout),
        env=env,
    )
    if return_code == 0:
        return 0

    if strict:
        return return_code

    sys.stderr.write(
        "[autoware_system_designer] WARNING: deployment build failed but is non-fatal in local build "
        f"(set -DAUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT=ON to fail). "
        f"See log: {args.log_file}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
