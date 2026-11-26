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

"""Checks on the linked graph: one topic per subscriber, one export per publisher, one path per port."""

import pytest
import yaml
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.common.exceptions import SystemDesignerError
from autoware_system_designer.model.links import Link
from autoware_system_designer.model.ports import InPort, OutPort

MSG = "sensor_msgs/msg/PointCloud2"
PKG = "pipeline_pkg"
SYSTEM = f"{PKG}/Chain.system.yaml"
MODULE = f"{PKG}/Pipeline.module.yaml"
FILTER = f"{PKG}/Filter.node.yaml"

RADAR = {"name": "radar", "entity": "Source.node", "path": "/sensing/radar", "compute_unit": "main_ecu"}
RADAR_TO_PIPELINE = ["radar.publisher.pointcloud", "pipeline.subscriber.pointcloud"]


def _edit(workspace, relative_path, change):
    path = workspace / relative_path
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _build(workspace, tmp_path):
    return run_pipeline(workspace, SYSTEM, tmp_path)


def _fails(workspace, tmp_path, code):
    with pytest.raises(SystemDesignerError) as excinfo:
        _build(workspace, tmp_path)
    assert code in str(excinfo.value)
    return str(excinfo.value)


def _launch_topics(structure, port_name):
    """Topic of the named launch port, by node path."""
    topics = {}

    def walk(data):
        for port in data.get("launcher", {}).get("ports", []):
            if port["name"] == port_name:
                topics[data["path"]] = port["topic"]
        for child in data.get("children", []):
            walk(child)

    walk(structure["data"])
    return topics


def test_ports_are_kept_by_identity():
    source = OutPort("pointcloud", MSG, ["sensing", "lidar"])
    twin_a = InPort("pointcloud", MSG, ["perception", "filter"])
    twin_b = InPort("pointcloud", MSG, ["perception", "filter"])

    Link(MSG, source, twin_a)
    Link(MSG, source, twin_a)
    Link(MSG, source, twin_b)

    assert len(source.users) == 2 and source.users[0] is twin_a and source.users[1] is twin_b
    assert twin_a.servers == [source]


def test_second_publisher_on_another_topic_fails(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)

    def change(system):
        system["components"].append(RADAR)
        system["connections"].append(RADAR_TO_PIPELINE)

    _edit(workspace, SYSTEM, change)
    message = _fails(workspace, tmp_path, "E_TOPIC_CONFLICT")
    assert "/sensing/lidar/pointcloud" in message and "/sensing/radar/pointcloud" in message


def test_publishers_remapped_to_one_topic_share_a_subscriber(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)

    def change(system):
        system["components"].append(RADAR)
        system["connections"].append(RADAR_TO_PIPELINE)
        system["remaps"] = [
            {"source": "lidar.publisher.pointcloud", "topic": "/sensing/pointcloud"},
            {"source": "radar.publisher.pointcloud", "topic": "/sensing/pointcloud"},
        ]

    _edit(workspace, SYSTEM, change)
    topics = _launch_topics(_build(workspace, tmp_path).structure("default"), "pointcloud")
    assert set(topics.values()) == {"/sensing/pointcloud"}
    assert {"/sensing/lidar", "/sensing/radar", "/perception/pipeline/filter_primary"} <= set(topics)


def test_wildcard_onto_one_subscriber_fails(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)

    def change(module):
        module["connections"] = [
            ["subscriber.pointcloud", "filter_*.subscriber.pointcloud"],
            ["filter_*.publisher.objects", "node_merger.subscriber.objects1"],
            ["node_merger.publisher.objects", "publisher.objects"],
        ]

    _edit(workspace, MODULE, change)
    _fails(workspace, tmp_path, "E_TOPIC_CONFLICT")


def test_components_on_one_path_fail(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)
    _edit(workspace, SYSTEM, lambda system: system["components"].append({**RADAR, "path": "/sensing/lidar"}))
    message = _fails(workspace, tmp_path, "E_PORT_PATH_DUPLICATE")
    assert "'lidar'" in message and "'radar'" in message


def test_publisher_exported_twice_fails(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)

    def change(module):
        module["publishers"].append({"name": "objects_alias"})
        module["connections"].append(["node_merger.publisher.objects", "publisher.objects_alias"])

    _edit(workspace, MODULE, change)
    message = _fails(workspace, tmp_path, "E_EXPORT_TWICE")
    assert "already exported as 'objects'" in message


def test_declared_port_without_inner_connection_is_named(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)
    _edit(workspace, MODULE, lambda module: module["publishers"].append({"name": "spare"}))
    _edit(
        workspace,
        SYSTEM,
        lambda system: system["connections"].append(["pipeline.publisher.spare", "sink.subscriber.objects"]),
    )
    message = _fails(workspace, tmp_path, "E_PORT_UNCONNECTED")
    assert "Pipeline.module.yaml" in message


def test_wildcard_skips_global_subscriber(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)

    def add_global(node):
        node["subscribers"].append({"name": "clock", "message_type": MSG, "global": "/clock"})

    _edit(workspace, FILTER, add_global)
    _edit(workspace, MODULE, lambda module: module["subscribers"].append({"name": "clock"}))
    structure = _build(workspace, tmp_path).structure("default")
    assert set(_launch_topics(structure, "clock").values()) == {"/clock"}
    pipeline = next(child for child in structure["data"]["children"] if child["name"] == "pipeline")
    assert [port["name"] for port in pipeline["in_ports"]] == ["pointcloud"]
