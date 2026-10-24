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

"""Queue events: a `to_queue` outcome fills a node-owned queue, a `reads` entry binds a reader to it.

Filling is a trigger relation, so the filler's rate reaches the queue and stops
there; reading carries no rate, so the reader keeps the rate of its own trigger.
"""

import pytest
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.common.exceptions import SystemDesignerError
from autoware_system_designer.model.events import Process, QueueEvent


def _node(structure, name):
    stack = [structure["data"]]
    while stack:
        item = stack.pop()
        if item.get("entity_type") == "node" and item.get("name") == name:
            return item
        stack.extend(item.get("children") or [])
    raise AssertionError(f"node {name} not exported")


def test_queue_read_keeps_reader_on_its_own_clock(tmp_path):
    workspace = stage_case("queue_reads", tmp_path)
    run = run_pipeline(workspace, "queue_pkg/Queued.system.yaml", tmp_path)
    corrector = _node(run.structure("default"), "corrector")
    events = {event["name"]: event for event in corrector["events"]}

    queue = events["imu"]
    assert queue["type"] == "queue"
    assert queue["trigger_ids"] == [events["queue_imu"]["unique_id"]]
    assert queue["action_ids"] == []
    assert queue["reader_ids"] == [events["undistort"]["unique_id"]]
    assert events["undistort"]["read_ids"] == [queue["unique_id"]]

    # The IMU chain paces the queue; the lidar chain alone paces the reader.
    assert events["queue_imu"]["frequency"] == 100.0
    assert queue["frequency"] == 100.0
    assert events["undistort"]["frequency"] == 10.0


def test_from_queue_without_a_filler_is_rejected(tmp_path):
    workspace = stage_case("errors_unknown_queue", tmp_path)
    with pytest.raises(SystemDesignerError, match="Queue not found: twist"):
        run_pipeline(workspace, "queue_pkg/Unknown.system.yaml", tmp_path)


def test_queue_is_not_a_trigger_condition():
    process = Process("run", ["ns"], {"trigger_conditions": [{"queue": "imu"}], "outcomes": []})
    with pytest.raises(ValueError, match="queue"):
        process.set_condition([process.event], [])


def test_reads_accepts_only_from_queue():
    process = Process("run", ["ns"], {"reads": [{"on_queue": "imu"}]})
    with pytest.raises(ValueError, match="Invalid read type: on_queue"):
        process.set_reads({"imu": QueueEvent("imu", ["ns"])})


def test_queue_id_is_distinct_from_a_process_of_the_same_name():
    filler = Process("imu", ["ns"], {"trigger_conditions": [], "outcomes": [{"to_queue": "imu"}]})
    queues = {}
    filler.set_outcomes([filler.event], [], queues)
    assert queues["imu"].unique_id != filler.event.unique_id
    assert filler.event.actions == [queues["imu"]]
