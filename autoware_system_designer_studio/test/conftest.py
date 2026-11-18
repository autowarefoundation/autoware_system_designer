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

"""Bind the studio and the runtime it reads exports with to this source tree, ahead of any installed copy."""

import os
import signal
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _package_dir in (_REPO / "autoware_system_designer_runtime", _REPO / "autoware_system_designer_studio"):
    sys.path.insert(0, str(_package_dir))

# Set to run the tests marked ``timing``, whose outcome depends on the timing of the host.
TIMING_TESTS_ENV = "AUTOWARE_SYSTEM_DESIGNER_TIMING_TESTS"


def pytest_configure(config):
    config.addinivalue_line(
        "markers", f"timing: the outcome depends on the timing of the host; run with {TIMING_TESTS_ENV}=1"
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get(TIMING_TESTS_ENV):
        return
    skip = pytest.mark.skip(reason=f"depends on the timing of the host; {TIMING_TESTS_ENV}=1 runs it")
    for item in items:
        if "timing" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True, scope="session")
def handled_interrupt():
    """The session handles SIGINT as the serve daemon does, so a job's process starts with its default action.

    A session started with the signal ignored hands the ignore down to every process a job starts.
    """
    previous = signal.getsignal(signal.SIGINT)
    if previous == signal.SIG_IGN:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    yield
    signal.signal(signal.SIGINT, previous)


@pytest.fixture(autouse=True)
def _user_home(tmp_path, monkeypatch):
    """Each test has a user home of its own, without config overrides."""
    monkeypatch.setenv("AUTOWARE_SYSTEM_DESIGNER_HOME", str(tmp_path / "user_home"))


@pytest.fixture(autouse=True)
def _studio_settings(tmp_path, monkeypatch):
    """Each test reads and writes a studio settings file of its own, whose roots are under the test's
    directory."""
    settings = tmp_path / "studio.yaml"
    settings.write_text(f"log_root: {tmp_path / 'default_logs'}\nconfig_root: {tmp_path / 'default_config'}\n")
    monkeypatch.setenv("AUTOWARE_SYSTEM_DESIGNER_STUDIO_SETTINGS", str(settings))
