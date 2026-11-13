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

"""Bind the runtime package to this source tree, ahead of any installed copy."""

import fcntl
import os
import random
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

# Domains handed to tests; one lock file per domain in a directory shared by the test runs of the host.
TEST_DOMAINS = range(150, 231)
DOMAIN_LOCK_DIR = Path(tempfile.gettempdir()) / "autoware_system_designer_test_domains"

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


@pytest.fixture
def ros_domain_id():
    """A ROS domain no other test run of this host holds, kept for the length of the test."""
    DOMAIN_LOCK_DIR.mkdir(mode=0o777, exist_ok=True)
    for domain in random.sample(TEST_DOMAINS, len(TEST_DOMAINS)):
        try:
            fd = os.open(DOMAIN_LOCK_DIR / str(domain), os.O_CREAT | os.O_RDWR, 0o666)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            continue
        try:
            yield domain
        finally:
            os.close(fd)
        return
    pytest.fail("every test ROS domain is held by another test run")


@pytest.fixture
def fake_process_groups(monkeypatch):
    """For tests whose members are fake processes: their pids name no process group of the test."""
    from autoware_system_designer_runtime._impl.core import regular_actor

    async def no_group(pgid, *, name, timeout):
        return None

    monkeypatch.setattr(regular_actor, "reap_group", no_group)
