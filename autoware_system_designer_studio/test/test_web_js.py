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

"""Runs the web bundle's node test suite; skipped where node is not installed."""

import shutil
import subprocess
from pathlib import Path

import pytest

JS_TEST_DIR = Path(__file__).resolve().parent / "js"


# First major release of Node.js with the built-in test runner.
NODE_TEST_RUNNER_MAJOR = 18


def _node_major():
    """Major version of the installed node; None without one."""
    if shutil.which("node") is None:
        return None
    version = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False).stdout
    digits = version.strip().lstrip("v").partition(".")[0]
    return int(digits) if digits.isdigit() else None


@pytest.mark.skipif(
    (_node_major() or 0) < NODE_TEST_RUNNER_MAJOR,
    reason=f"node {NODE_TEST_RUNNER_MAJOR} or later is not installed",
)
def test_visualizer_js_suite():
    suites = sorted(str(path) for path in JS_TEST_DIR.glob("*.test.js"))
    result = subprocess.run(
        ["node", "--test", *suites],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
