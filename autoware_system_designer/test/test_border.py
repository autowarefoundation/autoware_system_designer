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

"""Border targets: one entity behind a generated entry launch file."""

import xml.etree.ElementTree as ET

import pytest
import yaml
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.common.exceptions import SystemDesignerError

BORDER = "pipeline_pkg/design/pipeline.border.yaml"


def _rewrite_border(workspace, **changes):
    path = workspace / BORDER
    data = yaml.safe_load(path.read_text())
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def _nodes(launch_file):
    root = ET.parse(launch_file).getroot()
    return {
        node.get("name"): (load.get("target"), {r.get("from"): r.get("to") for r in node.iter("remap")})
        for load in root.iter("load_composable_node")
        for node in load.iter("composable_node")
    }


def test_border_generates_entry_and_component_launchers(tmp_path):
    workspace = stage_case("border", tmp_path)
    run = run_pipeline(workspace, BORDER, tmp_path, strict=True)

    entry = run.out_root / "launch" / "pipeline.launch.xml"
    root = ET.parse(entry).getroot()
    assert {a.get("name"): a.get("default") for a in root.findall("arg")} == {
        "score_threshold": "0.3",
        "pointcloud_container_name": "pointcloud_container",
    }
    include = root.find("include").get("file")
    assert include.startswith("$(dirname)/")
    assert (entry.parent / include[len("$(dirname)/") :]).resolve().is_file()

    nodes = _nodes(run.launcher_dir("default") / "pipeline" / "pipeline" / "pipeline.launch.xml")
    target, remaps = nodes["filter_primary"]
    assert target == "$(var pointcloud_container_name)"
    assert remaps["~/input/pointcloud"] == "/sensing/lidar/concatenated/pointcloud"
    assert nodes["filter_secondary"][1]["~/input/pointcloud"] == "/sensing/lidar/concatenated/pointcloud"
    assert nodes["node_merger"][0] == "/perception/pipeline/pipeline_container"
    assert nodes["node_merger"][1]["~/output/objects"] == "/perception/objects"
    sink = ET.parse(run.launcher_dir("default") / "pipeline" / "sink" / "sink.launch.xml").getroot()
    assert {r.get("from"): r.get("to") for r in sink.iter("remap")} == {"~/input/objects": "/perception/objects"}

    # The external group starts no container; the other one does.
    unit_dir = run.launcher_dir("default") / "pipeline"
    assert sorted(p.name for p in unit_dir.iterdir() if p.is_dir()) == ["pipeline", "pipeline_container", "sink"]


def test_border_wildcard_interface(tmp_path):
    workspace = stage_case("border", tmp_path)
    _rewrite_border(
        workspace,
        components=[{"name": "pipeline", "entity": "Pipeline.module", "path": "/perception/pipeline"}],
        arguments=[{"name": "score_threshold", "default": 0.3}, {"name": "pointcloud_container_name"}],
        connections=[["subscriber.*", "pipeline.subscriber.*"], ["pipeline.publisher.*", "publisher.*"]],
    )
    run = run_pipeline(workspace, BORDER, tmp_path, strict=True)
    nodes = _nodes(run.launcher_dir("default") / "pipeline" / "pipeline" / "pipeline.launch.xml")
    assert nodes["filter_primary"][1]["~/input/pointcloud"] == "/sensing/lidar/concatenated/pointcloud"
    assert nodes["node_merger"][1]["~/output/objects"] == "/perception/objects"


def test_border_output_path(tmp_path):
    workspace = stage_case("border", tmp_path)
    manifest_out = tmp_path / "out"
    from pipeline_harness import _load_deployment_process, write_manifests

    write_manifests(workspace, tmp_path / "manifests")
    _load_deployment_process().build(
        str(workspace / BORDER), str(tmp_path / "manifests"), str(manifest_out), border_output="border/x.launch.xml"
    )
    entry = manifest_out / "border" / "x.launch.xml"
    include = ET.parse(entry).getroot().find("include").get("file")
    assert (entry.parent / include[len("$(dirname)/") :]).resolve().is_file()


@pytest.mark.parametrize(
    "changes, message",
    [
        (
            {"connections": [["pipeline.publisher.objects", "publisher.objects"]]},
            "without a connection: ['pointcloud']",
        ),
        ({"publishers": [{"name": "objects", "topic": "relative"}]}, "must be an absolute ROS topic"),
        ({"name": "other"}, "differs from the file name"),
        ({"name": "pipe.line"}, "must match"),
        ({"entity": "x"}, "unknown key(s) ['entity']"),
        ({"components": [{"name": "pipeline", "entity": "Pipeline.system"}]}, "must be a module or a node"),
        (
            {
                "components": [
                    {"name": "pipeline", "entity": "Pipeline.module", "parameter_set": "Pipeline.parameter_set"}
                ]
            },
            "must be a list of parameter set names",
        ),
        ({"variables": [{"name": "score_threshold", "value": 1}]}, "both an argument and a variable"),
        ({"arguments": [{"name": "a"}, {"name": "a"}]}, "argument 'a' is declared twice"),
        (
            {"connections": [["subscriber.pointcloud", "pipeline.publisher.objects"]]},
            "must be '<component>.subscriber.<port>'",
        ),
        (
            {"connections": [["subscriber.pointcloud", "pipeline.subscriber.lidar"]]},
            "component 'pipeline' has no subscriber 'lidar'",
        ),
        ({"connections": [["subscriber.pointcloud", "publisher.objects"]]}, "cannot join two interface ports"),
    ],
)
def test_border_errors(tmp_path, changes, message):
    workspace = stage_case("border", tmp_path)
    _rewrite_border(workspace, **changes)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, BORDER, tmp_path, strict=True)
    assert message in str(excinfo.value)
    assert "pipeline.border.yaml" in str(excinfo.value)
