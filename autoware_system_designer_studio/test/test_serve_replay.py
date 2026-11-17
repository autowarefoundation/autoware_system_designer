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

"""The serve daemon reads a bag's topics and runs a replay as a job, with a fake replay command."""

import json
import os
import stat
import sys
import time

import pytest

from .test_serve import PKG, SYSTEM, UNITS, _install_tree, _Running

SYSTEM_ID = f"{PKG}:{SYSTEM}"

# Writes the files of a replay into its --log-dir and exits with FAKE_REPLAY_EXIT.
FAKE_REPLAY = f"""#!{sys.executable}
import json, os, sys
from pathlib import Path

args = sys.argv[1:]
run = Path(args[args.index("--log-dir") + 1])
out = run / "replay"
out.mkdir(parents=True, exist_ok=True)
config = json.loads(Path(args[args.index("--config") + 1]).read_text())
(out / "boundary.json").write_text(json.dumps({{"fed": {{"/in": "t"}}, "recorded": {{"/out": "t"}}, "problems": []}}))
if "--check" not in args:
    (out / "result.json").write_text(json.dumps({{"completed": True, "outputs": 3, "violations": [], "ignored": 1}}))
    (out / "verdict.json").write_text(json.dumps({{"passed": True, "topics": [{{"topic": "/out", "status": "match"}}]}}))
print("scope", ",".join(config["scope"]), flush=True)
print('[12:00:00] replay - replay progress: {{"phase": "pre-process", "step": "launching"}}', flush=True)
if "--check" not in args:
    print('[12:00:01] replay - replay progress: {{"phase": "pre-process", "step": "up", "fraction": 1}}', flush=True)
    print('[12:00:02] replay - replay progress: {{"phase": "replay", "step": "1 of 2 s", "fraction": 0.5}}', flush=True)
sys.exit(int(os.environ.get("FAKE_REPLAY_EXIT", "0")))
"""


def _bag(root):
    bag = root / "bag"
    bag.mkdir()
    (bag / "metadata.yaml").write_text(
        "rosbag2_bagfile_information:\n"
        "  storage_identifier: sqlite3\n"
        "  starting_time: {nanoseconds_since_epoch: 1000}\n"
        "  duration: {nanoseconds: 500}\n"
        "  topics_with_message_count:\n"
        "    - topic_metadata: {name: /in, type: std_msgs/msg/String, offered_qos_profiles: ''}\n"
        "      message_count: 3\n"
    )
    return bag


@pytest.fixture
def served(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command = bin_dir / "autoware-system-designer-replay"
    command.write_text(FAKE_REPLAY)
    command.chmod(command.stat().st_mode | stat.S_IXUSR)
    running = _Running(
        _install_tree(tmp_path, compute_units=UNITS),
        served_units=["main_ecu"],
        launch_env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "ROS_DISTRO": "test"},
        log_base=tmp_path / "logs",
        heartbeat_s=60,
    )
    yield running
    running.close()


def _call(running, method, params):
    _, body = running.rpc({"id": 1, "method": method, "params": params})
    return body


def _finished(running, job_id, timeout=10.0):
    job = running.daemon.jobs.get(job_id)
    deadline = time.time() + timeout
    while job.active:
        assert time.time() < deadline, f"{job_id} did not end"
        time.sleep(0.02)
    return job.to_view()


def test_bag_topics_are_read_by_path(served, tmp_path):
    body = _call(served, "replay.bag", {"path": str(_bag(tmp_path))})

    assert body["ok"], body
    assert body["result"]["topics"] == [{"name": "/in", "type": "std_msgs/msg/String", "count": 3}]
    assert _call(served, "replay.bag", {"path": str(tmp_path / "none")})["error"]["code"] == "not_found"


def test_bag_with_metadata_is_read_as_is_by_uri(served, tmp_path):
    bag = _bag(tmp_path)

    body = _call(served, "replay.bag", {"path": bag.as_uri(), "generate": True})

    assert body["ok"], body
    assert (body["result"]["metadata"], body["result"]["path"]) == ("file", str(bag))
    assert _call(served, "replay.bag", {"path": str(bag), "generate": "yes"})["error"]["code"] == "invalid_params"


def test_a_bag_file_is_found_by_name_and_size(served, tmp_path):
    storage = tmp_path / "bags" / "day" / "run" / "run_0.mcap"
    storage.parent.mkdir(parents=True)
    storage.write_bytes(b"12345")
    (tmp_path / "bags" / "run_0.mcap").write_bytes(b"1234")
    find = {"name": "run_0.mcap", "size": 5, "directories": [str(tmp_path / "bags")]}

    assert _call(served, "replay.find", find)["result"] == {"path": str(storage)}
    assert _call(served, "replay.find", {**find, "size": 6})["error"]["code"] == "not_found"
    assert _call(served, "replay.find", {**find, "directories": []})["error"]["code"] == "not_found"
    assert _call(served, "replay.find", {**find, "name": "day/run_0.mcap"})["error"]["code"] == "invalid_params"

    served.daemon.bag_dirs = [tmp_path / "bags" / "day"]
    assert _call(served, "replay.find", {**find, "directories": []})["result"] == {"path": str(storage)}


def test_replay_runs_as_a_job_with_its_configuration_and_summary(served, tmp_path):
    params = {
        "system": SYSTEM_ID,
        "mode": "Runtime",
        "bag": str(_bag(tmp_path)),
        "deploy": "car_a",
        "scope": ["/perception"],
        "remap": {"/raw": "/in"},
        "accept": ["/map"],
        "skip_outputs": ["/debug"],
        "duration": 2.5,
        "domain_id": 7,
        "save_reference": True,
    }

    body = _call(served, "replay.start", params)

    assert body["ok"], body
    job = body["result"]
    assert (job["kind"], job["key"]) == ("replay", "replay")
    argv = job["argv"]
    assert argv[1].endswith("system_structure/Runtime.json")
    assert argv[argv.index("--deploy") + 1] == "car_a"
    assert argv[argv.index("--duration") + 1] == "2.5" and argv[argv.index("--domain-id") + 1] == "7"
    assert "--save-reference" in argv and "--check" not in argv
    run_dir = tmp_path / "logs" / os.path.basename(job["detail"]["run_dir"])
    assert json.loads((run_dir / "replay_config.json").read_text()) == {
        "scope": ["/perception"],
        "remap": {"/raw": "/in"},
        "accept": ["/map"],
        "outputs": [],
        "skip_outputs": ["/debug"],
    }

    done = _finished(served, job["id"])
    assert done["state"] == "succeeded"
    assert done["detail"]["fed"] == ["/in"] and done["detail"]["recorded"] == ["/out"]
    assert done["detail"]["result"]["completed"] is True and "ignored" not in done["detail"]["result"]
    assert done["detail"]["verdict"]["passed"] is True
    assert done["detail"]["progress"] == {"phase": "replay", "step": "1 of 2 s", "fraction": 0.5}
    assert done["detail"]["phases"] == {"pre-process": 1.0, "replay": 0.5}


def test_pre_flight_passes_check_and_writes_no_result(served, tmp_path):
    body = _call(
        served,
        "replay.start",
        {"system": SYSTEM_ID, "mode": "Runtime", "bag": str(_bag(tmp_path)), "scope": ["/a"], "check": True},
    )

    assert "--check" in body["result"]["argv"]
    done = _finished(served, body["result"]["id"])
    assert done["detail"]["problems"] == [] and "result" not in done["detail"]


@pytest.mark.parametrize(
    "change",
    [
        {"scope": []},
        {"scope": ["relative"]},
        {"remap": {"/a": "b"}},
        {"duration": 0},
        {"domain_id": 500},
        {"check": "yes"},
        {"mode": "Nope"},
        {"deploy": "unknown"},
        {"bag": ""},
    ],
)
def test_replay_refuses_invalid_requests(served, tmp_path, change):
    params = {"system": SYSTEM_ID, "mode": "Runtime", "bag": str(tmp_path / "bag"), "scope": ["/a"], **change}

    assert _call(served, "replay.start", params)["error"]["code"] == "invalid_params"


def test_replay_is_unavailable_without_a_ros_environment(tmp_path):
    running = _Running(
        _install_tree(tmp_path, compute_units=UNITS),
        launch_env={**os.environ, "ROS_DISTRO": ""},
        heartbeat_s=60,
    )
    try:
        body = _call(running, "replay.start", {"system": SYSTEM_ID, "mode": "Runtime", "bag": "/b", "scope": ["/a"]})
    finally:
        running.close()

    assert body["error"]["code"] == "unavailable"
