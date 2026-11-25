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

"""Border targets: components behind a generated entry launch file."""

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
    """Composable nodes by name: (container target, remaps)."""
    root = ET.parse(launch_file).getroot()
    return {
        node.get("name"): (load.get("target"), {r.get("from"): r.get("to") for r in node.iter("remap")})
        for load in root.iter("load_composable_node")
        for node in load.iter("composable_node")
    }


def _groups(root):
    """Namespace pushed by each group, by the names of the nodes directly in it."""
    groups = {}
    for group in root.iter("group"):
        push = group.find("push-ros-namespace")
        if push is None:
            continue
        for child in group:
            if child.tag in ("node", "node_container"):
                groups[child.get("name")] = push.get("namespace")
            elif child.tag == "load_composable_node":
                groups[child.find("composable_node").get("name")] = push.get("namespace")
    return groups


def test_border_generates_one_launch_file(tmp_path):
    workspace = stage_case("border", tmp_path)
    run = run_pipeline(workspace, BORDER, tmp_path, strict=True)

    entry = run.out_root / "launch" / "pipeline.launch.xml"
    root = ET.parse(entry).getroot()
    assert {a.get("name"): a.get("default") for a in root.findall("arg")} == {
        "score_threshold": "0.3",
        "pointcloud_container_name": "pointcloud_container",
        "use_sim_time": "false",
        "input/pointcloud": "/sensing/lidar/concatenated/pointcloud",
        "output/objects": "/perception/objects",
    }
    assert not run.launcher_dir("default").exists()

    # System launches precede the components.
    system_launch = root.find("group")
    assert system_launch.get("scoped") == "false"
    include = system_launch.find("include")
    assert include.get("file") == "$(find-pkg-share autoware_global_parameter_loader)/launch/global_params.launch.py"
    assert {a.get("name"): a.get("value") for a in include.findall("arg")} == {"use_sim_time": "$(var use_sim_time)"}

    # Interface topics are reached through the interface arguments.
    nodes = _nodes(entry)
    target, remaps = nodes["filter_primary"]
    assert target == "$(var pointcloud_container_name)"
    assert remaps["~/input/pointcloud"] == "$(var input/pointcloud)"
    assert nodes["filter_secondary"][1]["~/input/pointcloud"] == "$(var input/pointcloud)"
    assert nodes["node_merger"][0] == "/perception/pipeline/pipeline_container"
    assert nodes["node_merger"][1]["~/output/objects"] == "$(var output/objects)"
    sink = next(n for n in root.iter("node") if n.get("name") == "sink")
    assert {r.get("from"): r.get("to") for r in sink.iter("remap")} == {"~/input/objects": "$(var output/objects)"}

    # Nodes sit in the group of their namespace; the external group starts no container.
    groups = _groups(root)
    assert groups["sink"] == "perception"
    assert groups["filter_primary"] == "pipeline"
    assert groups["pipeline_container"] == "pipeline"
    loaded = [load.find("composable_node") for load in root.iter("load_composable_node")]
    assert all(n.get("namespace") is None for n in list(root.iter("node")) + loaded)
    assert all(n.get("namespace") == "" for n in root.iter("node_container"))
    containers = [c.get("name") for c in root.iter("node_container")]
    assert containers == ["pipeline_container"]
    pipeline_group = next(g for g in root.iter("group") if g.find("node_container") is not None)
    tags = [child.tag for child in pipeline_group]
    assert tags.index("node_container") < tags.index("load_composable_node")
    primary = next(n for n in root.iter("composable_node") if n.get("name") == "filter_primary")
    assert {p.get("name"): p.get("value") for p in primary.iter("param")}["score_threshold"] == "$(var score_threshold)"


def test_border_internal_interface(tmp_path):
    workspace = stage_case("border", tmp_path)
    _rewrite_border(
        workspace,
        publishers=None,
        connections=[
            ["subscriber.pointcloud", "pipeline.subscriber.pointcloud"],
            ["pipeline.publisher.objects", "sink.subscriber.objects"],
        ],
    )
    run = run_pipeline(workspace, BORDER, tmp_path, strict=True)
    entry = run.out_root / "launch" / "pipeline.launch.xml"
    root = ET.parse(entry).getroot()
    assert {let.get("name"): let.get("value") for let in root.findall("let")} == {
        "pipeline/objects": "/perception/pipeline/objects"
    }
    nodes = _nodes(entry)
    assert nodes["node_merger"][1]["~/output/objects"] == "$(var pipeline/objects)"


def test_border_shared_container_is_external(tmp_path):
    workspace = stage_case("border", tmp_path)
    data = yaml.safe_load((workspace / BORDER).read_text())
    groups = data["node_groups"]
    groups[0].pop("external")
    groups[0]["name"] = "shared_container"
    groups[0]["nodes"].append("/perception/sink")
    _rewrite_border(workspace, node_groups=groups)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, BORDER, tmp_path, strict=True)
    assert "a container shared over components is launched externally" in str(excinfo.value)


def test_border_wildcard_interface(tmp_path):
    workspace = stage_case("border", tmp_path)
    _rewrite_border(
        workspace,
        components=[{"name": "pipeline", "entity": "Pipeline.module", "path": "/perception/pipeline"}],
        arguments=[{"name": "score_threshold", "default": 0.3}, {"name": "pointcloud_container_name"}],
        connections=[["subscriber.*", "pipeline.subscriber.*"], ["pipeline.publisher.*", "publisher.*"]],
    )
    run = run_pipeline(workspace, BORDER, tmp_path, strict=True)
    nodes = _nodes(run.out_root / "launch" / "pipeline.launch.xml")
    assert nodes["filter_primary"][1]["~/input/pointcloud"] == "$(var input/pointcloud)"
    assert nodes["node_merger"][1]["~/output/objects"] == "$(var output/objects)"


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
        ({"system_launches": [{"package": "p", "file": "f", "scoped": "no"}]}, "'scoped' must be a boolean"),
        ({"system_launches": [{"package": "p", "file": "f", "arguments": [{"name": "a"}]}]}, "requires 'value'"),
    ],
)
def test_border_errors(tmp_path, changes, message):
    workspace = stage_case("border", tmp_path)
    _rewrite_border(workspace, **changes)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, BORDER, tmp_path, strict=True)
    assert message in str(excinfo.value)
    assert "pipeline.border.yaml" in str(excinfo.value)


def test_border_parameter_files(tmp_path):
    workspace = stage_case("border", tmp_path)
    run = run_pipeline(workspace, BORDER, tmp_path, strict=True)
    root = ET.parse(run.out_root / "launch" / "pipeline.launch.xml").getroot()
    params = {
        node.get("name"): [(p.get("from"), p.get("name"), p.get("value")) for p in node.iter("param")]
        for node in root.iter("composable_node")
    }
    file_ref = "$(find-pkg-share pipeline_pkg)/config/pipeline/perception/pipeline/{}.param.yaml"
    # Build-time values load from a file; a value with a substitution stays inline after it.
    assert params["filter_primary"] == [
        (file_ref.format("filter_primary"), None, None),
        (None, "score_threshold", "$(var score_threshold)"),
    ]
    assert params["filter_secondary"] == [(file_ref.format("filter_secondary"), None, None)]
    written = yaml.safe_load(
        (run.out_root / "config/pipeline/perception/pipeline/filter_secondary.param.yaml").read_text()
    )
    assert written == {
        "/**": {
            "ros__parameters": {
                # The node default follows its default file in launch order.
                "score_threshold": 0.5,
                "max_range": 100.0,
                "labels": ["car", "truck"],
                "filter": {"enabled": True},
            }
        }
    }


def test_border_parameter_order():
    from autoware_system_designer.generator.border_parameters import plan_node_parameters

    node = {
        "param_values": [
            {"name": "a", "value": "1", "parameter_type": "DEFAULT_FILE"},
            {"name": "s", "value": "$(var s)", "parameter_type": "GLOBAL"},
            {"name": "b", "value": "x", "parameter_type": "DEFAULT_FILE"},
            {"name": "s", "value": "false", "parameter_type": "DEFAULT"},
            {"name": "s", "value": "true", "parameter_type": "OVERRIDE"},
            {"name": "a", "value": "2", "parameter_type": "MODE"},
        ],
        "launch_time_param_files": [
            {"path": "$(var dir)/f.yaml", "allow_substs": True, "parameter_type": "OVERRIDE_FILE"}
        ],
    }
    # A later value of an inline name opens a new file; a launch-time file closes the open one.
    assert plan_node_parameters(node) == [
        {"values": {"a": 1, "b": "x"}},
        {"name": "s", "value": "$(var s)"},
        {"values": {"s": False}},
        {"path": "$(var dir)/f.yaml", "allow_substs": True},
        {"values": {"s": True, "a": 2}},
    ]


def test_border_package_paths():
    from autoware_system_designer.generator.border_parameters import PackagePaths

    paths = PackagePaths([("/ws/install/pkg/share/pkg", "pkg"), ("/ws/src/pkg", "pkg"), ("/ws/src/pkg_b", "pkg_b")])
    assert paths.portable("/ws/install/pkg/share/pkg/config/a.yaml") == "$(find-pkg-share pkg)/config/a.yaml"
    assert paths.portable({"v": ["/ws/src/pkg_b/data/x.csv"]}) == {"v": ["$(find-pkg-share pkg_b)/data/x.csv"]}
    assert paths.portable("/ws/src/pkg_c/x") == "/ws/src/pkg_c/x"


def test_generate_border_script(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from pipeline_harness import FIXTURES_DIR

    package_dir = tmp_path / "src" / "pipeline_pkg"
    shutil.copytree(FIXTURES_DIR / "border" / "pipeline_pkg", package_dir)
    script = Path(__file__).resolve().parents[1] / "script" / "generate_border.py"
    result = subprocess.run(
        [sys.executable, str(script), str(package_dir / "design" / "pipeline.border.yaml"), "--source-only"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "filter_primary" in _nodes(package_dir / "launch" / "pipeline.launch.xml")
    assert (package_dir / "config/pipeline/perception/pipeline/filter_secondary.param.yaml").is_file()
    assert not (package_dir / "exports").exists()
