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

"""Topic propagation across module interface ports."""

from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.model.links import Link
from autoware_system_designer.model.ports import InPort, OutPort

MSG = "sensor_msgs/msg/PointCloud2"


def test_nested_input_chain_shares_topic():
    node_in = InPort("pointcloud", MSG, ["perception", "pipeline", "inner", "filter"])
    inner_in = InPort("pointcloud", MSG, ["perception", "pipeline", "inner"])
    outer_in = InPort("pointcloud", MSG, ["perception", "pipeline"])
    source = OutPort("pointcloud", MSG, ["sensing", "lidar"])

    Link(MSG, inner_in, node_in)
    Link(MSG, outer_in, inner_in)
    Link(MSG, source, outer_in)

    assert outer_in.get_reference_list() == [node_in]
    for port in (source, outer_in, inner_in, node_in):
        assert port.get_topic() == "/sensing/lidar/pointcloud"
    for port in (outer_in, inner_in, node_in):
        assert port.servers == [source]
    assert set(source.users) == {outer_in, inner_in, node_in}


def test_nested_output_chain_shares_topic():
    node_out = OutPort("objects", MSG, ["perception", "pipeline", "inner", "merger"])
    inner_out = OutPort("objects", MSG, ["perception", "pipeline", "inner"])
    outer_out = OutPort("objects", MSG, ["perception", "pipeline"])
    sink = InPort("objects", MSG, ["sink"])

    Link(MSG, node_out, inner_out)
    Link(MSG, inner_out, outer_out)
    Link(MSG, outer_out, sink)

    assert outer_out.get_reference_list() == [node_out]
    for port in (node_out, inner_out, outer_out, sink):
        assert port.get_topic() == "/perception/pipeline/objects"
    for port in (node_out, inner_out, outer_out):
        assert port.users == [sink]
    assert sink.servers == [node_out]


def _ports(value, found):
    if isinstance(value, dict):
        if "port_path" in value:
            found.append(value)
        for item in value.values():
            _ports(item, found)
    elif isinstance(value, list):
        for item in value:
            _ports(item, found)
    return found


def test_shared_external_input_is_one_port(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)
    run = run_pipeline(workspace, "pipeline_pkg/Chain.system.yaml", tmp_path)

    topics = {}
    for port in _ports(run.structure("default"), []):
        assert port["topic"], f"{port['port_path']} has no topic"
        topics.setdefault(port["unique_id"], set()).add(tuple(port["topic"]))
    assert all(len(seen) == 1 for seen in topics.values())
