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

"""Subsystem targets: an instance behind a generated entry launch file."""

import json
import xml.etree.ElementTree as ET

import pytest
import yaml
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.common.exceptions import SystemDesignerError
from autoware_system_designer.generator.subsystem_launcher import check_replaceable_launch_file

SUBSYSTEM = "pipeline_pkg/design/pipeline.subsystem.yaml"


def _rewrite_subsystem(workspace, **changes):
    path = workspace / SUBSYSTEM
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


def test_subsystem_generates_one_launch_file(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)

    entry = run.out_root / "launch" / "pipeline.launch.xml"
    root = ET.parse(entry).getroot()
    assert {a.get("name"): a.get("default") for a in root.findall("arg")} == {
        "score_threshold": "0.3",
        "pointcloud_container_name": "pointcloud_container",
        "use_sim_time": "false",
        "input/pointcloud": "/sensing/lidar/concatenated/pointcloud",
        "input/get_config": "/api/perception/get_config",
        "output/objects": "/perception/objects",
        "output/reset": "/perception/pipeline/reset",
    }
    assert not run.launcher_dir("default").exists()

    # System launches precede the instance.
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
    # Services are bound like topics: a server through its output argument, clients through the input one.
    assert nodes["node_merger"][1]["~/output/reset"] == "$(var output/reset)"
    assert remaps["~/input/get_config"] == "$(var input/get_config)"
    assert nodes["filter_secondary"][1]["~/input/get_config"] == "$(var input/get_config)"

    # Nodes sit in the group of their namespace; the external group starts no container.
    groups = _groups(root)
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


def test_subsystem_manifest_records_the_launch_file_interface(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)

    manifest = json.loads((run.exports_dir / "deployment.json").read_text())
    assert manifest["kind"] == "subsystem"
    assert manifest["launch_units"] == {"default": []}
    subsystem = manifest["subsystem"]
    assert subsystem["launch_file"] == "launch/pipeline.launch.xml"
    assert (run.out_root / subsystem["launch_file"]).is_file()
    assert subsystem["arguments"] == [
        {"name": "score_threshold", "default": "0.3", "description": ""},
        {
            "name": "pointcloud_container_name",
            "default": "pointcloud_container",
            "description": "container started by the caller",
        },
        {"name": "use_sim_time", "default": "false", "description": ""},
    ]
    assert subsystem["external_containers"] == [
        {
            "name": "$(var pointcloud_container_name)",
            "package": "rclcpp_components",
            "executable": "component_container_mt",
        }
    ]
    assert subsystem["subscribers"] == [{"name": "pointcloud", "topic": "/sensing/lidar/concatenated/pointcloud"}]
    assert subsystem["publishers"] == [{"name": "objects", "topic": "/perception/objects"}]
    assert subsystem["servers"] == [{"name": "reset", "topic": "/perception/pipeline/reset"}]
    assert subsystem["clients"] == [{"name": "get_config", "topic": "/api/perception/get_config"}]
    assert subsystem["system_launches"] == [
        {
            "package": "autoware_global_parameter_loader",
            "file": "launch/global_params.launch.py",
            "scoped": False,
            "arguments": [{"name": "use_sim_time", "value": "$(var use_sim_time)"}],
        }
    ]


def test_subsystem_interface_is_the_system_boundary(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    root = run.structure("default")["data"]
    assert {p["name"]: (p["role"], "/" + "/".join(p["topic"])) for p in root["in_ports"]} == {
        "pointcloud": ("subscriber", "/sensing/lidar/concatenated/pointcloud"),
        "get_config": ("client", "/api/perception/get_config"),
    }
    assert {p["name"]: (p["role"], "/" + "/".join(p["topic"])) for p in root["out_ports"]} == {
        "objects": ("publisher", "/perception/objects"),
        "reset": ("server", "/perception/pipeline/reset"),
    }
    boundary = {port["unique_id"] for port in root["in_ports"] + root["out_ports"]}
    linked = {link[end]["unique_id"] for link in root["links"] for end in ("from_port", "to_port")}
    assert boundary <= linked


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"connections": []}, "unknown key(s) ['connections']"),
        ({"instance": [{"name": "pipeline", "entity": "Pipeline.module"}]}, "'instance' must be a mapping"),
        ({"publishers": [{"name": "objects", "topic": "relative"}]}, "must be an absolute ROS name"),
        ({"publishers": [{"name": "objects", "topic": "//objects"}]}, "must be an absolute ROS name"),
        (
            {"clients": [{"name": "pointcloud", "topic": "/api/pointcloud"}]},
            "client port 'pointcloud' is already declared in subscribers",
        ),
        ({"servers": [{"name": "restart", "topic": "/perception/restart"}]}, "'restart' not in 'pipeline'"),
        ({"name": "other"}, "differs from the file name"),
        ({"name": "pipe.line"}, "must match"),
        ({"instance": {"name": "pipe.line", "entity": "Pipeline.module"}}, "instance name 'pipe.line' must match"),
        ({"publishers": [{"name": "obj.ects", "topic": "/perception/objects"}]}, "port name 'obj.ects' must match"),
        ({"instance": {"name": "pipeline", "entity": "Pipeline.system"}}, "must be a module or a node"),
        (
            {"instance": {"name": "pipeline", "entity": "Pipeline.module", "parameter_set": "Pipeline.parameter_set"}},
            "must be a list of parameter set names",
        ),
        ({"variables": [{"name": "score_threshold", "value": 1}]}, "both an argument and a variable"),
        ({"arguments": [{"name": "a"}, {"name": "a"}]}, "argument 'a' is declared twice"),
        (
            {
                "arguments": [
                    {"name": "score_threshold"},
                    {"name": "pointcloud_container_name"},
                    {"name": "use_sim_time"},
                    {"name": "output/objects"},
                ]
            },
            "external interface 'output/objects' collides with an argument",
        ),
        (
            {
                "arguments": [{"name": "score_threshold"}, {"name": "use_sim_time"}],
            },
            "$(var pointcloud_container_name)",
        ),
        ({"subscribers": [{"name": "lidar", "topic": "/sensing/lidar"}]}, "input port 'lidar' not in 'pipeline'"),
        ({"publishers": [{"name": "markers", "topic": "/perception/markers"}]}, "'markers' not in 'pipeline'"),
        ({"system_launches": [{"package": "p", "file": "f", "scoped": "no"}]}, "'scoped' must be a boolean"),
        ({"system_launches": [{"package": "p", "file": "f", "arguments": [{"name": "a"}]}]}, "requires 'value'"),
    ],
)
def test_subsystem_errors(tmp_path, changes, message):
    workspace = stage_case("subsystem", tmp_path)
    _rewrite_subsystem(workspace, **changes)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    assert message in str(excinfo.value)
    assert "pipeline.subsystem.yaml" in str(excinfo.value)


def test_subsystem_external_group_name_resolves_variables(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    data = yaml.safe_load((workspace / SUBSYSTEM).read_text())
    data["node_groups"][0]["name"] = "$(var container_ns)/$(var pointcloud_container_name)"
    _rewrite_subsystem(
        workspace, node_groups=data["node_groups"], variables=[{"name": "container_ns", "value": "/sensing"}]
    )
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    nodes = _nodes(run.out_root / "launch" / "pipeline.launch.xml")
    assert nodes["filter_primary"][0] == "/sensing/$(var pointcloud_container_name)"
    manifest = json.loads((run.exports_dir / "deployment.json").read_text())
    assert [c["name"] for c in manifest["subsystem"]["external_containers"]] == [
        "/sensing/$(var pointcloud_container_name)"
    ]


DIAG = "diagnostic_msgs/msg/DiagnosticArray"


def _export_diagnostics(workspace, global_topic, topic_a, topic_b):
    """Merger and the primary filter each publish 'diag', exported as diag_a and diag_b."""

    def edit(relative, change):
        path = workspace / "pipeline_pkg" / relative
        data = yaml.safe_load(path.read_text())
        change(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    port = {"name": "diag", "message_type": DIAG, **({"global": global_topic} if global_topic else {})}
    for node in ("Merger.node.yaml", "Filter.node.yaml"):
        edit(node, lambda data: data.setdefault("publishers", []).append(dict(port)))

    def module(data):
        data["publishers"] += [{"name": "diag_a"}, {"name": "diag_b"}]
        data["connections"] += [
            ["node_merger.publisher.diag", "publisher.diag_a"],
            ["filter_primary.publisher.diag", "publisher.diag_b"],
        ]

    edit("Pipeline.module.yaml", module)
    edit(
        "design/pipeline.subsystem.yaml",
        lambda data: data["publishers"].extend(
            [{"name": "diag_a", "topic": topic_a}, {"name": "diag_b", "topic": topic_b}]
        ),
    )


def test_subsystem_interface_ports_share_no_topic(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    _export_diagnostics(workspace, None, "/diagnostics", "/diagnostics")
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    assert "E_INTERFACE_TOPIC_SHARED" in str(excinfo.value)
    assert "publisher 'diag_a', publisher 'diag_b'" in str(excinfo.value)


def test_subsystem_interface_ports_share_a_global_topic(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    _export_diagnostics(workspace, "/diagnostics", "/diagnostics", "/diagnostics")
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    launch_file = run.out_root / "launch" / "pipeline.launch.xml"
    root = ET.parse(launch_file).getroot()
    # A global topic is fixed: no interface argument, and every node keeps it.
    assert not [arg.get("name") for arg in root.iter("arg") if "diag" in arg.get("name")]
    nodes = _nodes(launch_file)
    for name in ("filter_primary", "filter_secondary", "node_merger"):
        assert nodes[name][1]["~/output/diag"] == "/diagnostics"


def test_subsystem_interface_keeps_a_global_topic(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    _export_diagnostics(workspace, "/diagnostics", "/diagnostics", "/other")
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    assert "E_INTERFACE_GLOBAL" in str(excinfo.value)


def test_external_group_requires_a_subsystem(tmp_path):
    workspace = stage_case("module_pipeline", tmp_path)
    path = workspace / "pipeline_pkg/Chain.system.yaml"
    data = yaml.safe_load(path.read_text())
    data["node_groups"][0]["external"] = True
    path.write_text(yaml.safe_dump(data, sort_keys=False))
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "pipeline_pkg/Chain.system.yaml", tmp_path)
    assert "only a subsystem" in str(excinfo.value)


def test_generated_launch_file_replaces_only_a_generated_one(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    check_replaceable_launch_file(str(run.out_root / "launch" / "pipeline.launch.xml"))
    check_replaceable_launch_file(str(tmp_path / "absent.launch.xml"))

    handwritten = tmp_path / "pipeline.launch.xml"
    handwritten.write_text("<launch/>\n")
    with pytest.raises(SystemDesignerError) as excinfo:
        check_replaceable_launch_file(str(handwritten))
    assert "was not generated" in str(excinfo.value)


def test_subsystem_parameters_stay_apart_from_package_config(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    own = tmp_path / "out" / "config" / "pipeline" / "own.param.yaml"
    own.parent.mkdir(parents=True)
    own.write_text("{}\n")
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    assert [p.name for p in own.parent.iterdir()] == ["own.param.yaml"]
    assert (run.out_root / "exports/pipeline/config/perception/pipeline/filter_primary.param.yaml").is_file()


def test_subsystem_parameter_files(tmp_path):
    workspace = stage_case("subsystem", tmp_path)
    run = run_pipeline(workspace, SUBSYSTEM, tmp_path, strict=True)
    root = ET.parse(run.out_root / "launch" / "pipeline.launch.xml").getroot()
    params = {
        node.get("name"): [(p.get("from"), p.get("name"), p.get("value")) for p in node.iter("param")]
        for node in root.iter("composable_node")
    }
    file_ref = "$(find-pkg-share pipeline_pkg)/exports/pipeline/config/perception/pipeline/{}.param.yaml"
    # Build-time values load from a file; a value with a substitution stays inline after it.
    assert params["filter_primary"] == [
        (file_ref.format("filter_primary"), None, None),
        (None, "score_threshold", "$(var score_threshold)"),
    ]
    assert params["filter_secondary"] == [(file_ref.format("filter_secondary"), None, None)]
    written = yaml.safe_load(
        (run.out_root / "exports/pipeline/config/perception/pipeline/filter_secondary.param.yaml").read_text()
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


def test_subsystem_parameter_order():
    from autoware_system_designer.generator.subsystem_parameters import plan_node_parameters

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


def test_subsystem_package_paths():
    from autoware_system_designer.generator.subsystem_parameters import PackagePaths

    paths = PackagePaths([("/ws/install/pkg/share/pkg", "pkg"), ("/ws/src/pkg", "pkg"), ("/ws/src/pkg_b", "pkg_b")])
    assert paths.portable("/ws/install/pkg/share/pkg/config/a.yaml") == "$(find-pkg-share pkg)/config/a.yaml"
    assert paths.portable({"v": ["/ws/src/pkg_b/data/x.csv"]}) == {"v": ["$(find-pkg-share pkg_b)/data/x.csv"]}
    assert paths.portable("/ws/src/pkg_c/x") == "/ws/src/pkg_c/x"


def test_generate_subsystem_script(tmp_path):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from pipeline_harness import FIXTURES_DIR

    # A workspace nested under another 'src' directory.
    package_dir = tmp_path / "src" / "ws" / "src" / "pipeline_pkg"
    shutil.copytree(FIXTURES_DIR / "subsystem" / "pipeline_pkg", package_dir)
    script = Path(__file__).resolve().parents[1] / "script" / "generate_subsystem.py"
    result = subprocess.run(
        [sys.executable, str(script), str(package_dir / "design" / "pipeline.subsystem.yaml"), "--source-only"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "filter_primary" in _nodes(package_dir / "launch" / "pipeline.launch.xml")
    assert (package_dir / "exports/pipeline/config/perception/pipeline/filter_secondary.param.yaml").is_file()
    # Only the parameter files of the export reach the package source.
    assert [p.name for p in (package_dir / "exports" / "pipeline").iterdir()] == ["config"]
    assert not (package_dir / "config" / "pipeline").exists()
