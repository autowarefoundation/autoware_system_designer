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
from typing import List


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "on", "yes", "y"}


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run deployment build through tee_run.py and decide whether to propagate "
            "failures based on strict mode."
        )
    )
    parser.add_argument("--log-file", required=True, help="Path to combined log file")
    parser.add_argument(
        "--print-level",
        default="ERROR",
        help="Print level forwarded to autoware_system_designer_PRINT_LEVEL",
    )
    parser.add_argument("tee_run_script", help="Path to tee_run.py")
    parser.add_argument("deployment_process_script", help="Path to deployment_process.py")
    parser.add_argument("deployment_file", help="Input deployment/design/system target")
    parser.add_argument("resource_dir", help="System designer resource directory")
    parser.add_argument("output_root_dir", help="Deployment output root")
    parser.add_argument(
        "workspace_yaml",
        nargs="?",
        default=None,
        help="Optional workspace.yaml path",
    )
    return parser.parse_args(argv)


def main(argv: List[str]) -> int:
    args = _parse_args(argv)

    strict = _truthy(os.environ.get("AUTOWARE_SYSTEM_DESIGNER_BUILD_DEPLOY_STRICT", "0"))

    env = os.environ.copy()
    env["autoware_system_designer_PRINT_LEVEL"] = args.print_level

    command = [
        sys.executable,
        args.tee_run_script,
        "--log-file",
        args.log_file,
        "--",
        sys.executable,
        "-d",
        args.deployment_process_script,
        args.deployment_file,
        args.resource_dir,
        args.output_root_dir,
    ]
    if args.workspace_yaml:
        command.append(args.workspace_yaml)

    return_code = subprocess.call(command, env=env)
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
