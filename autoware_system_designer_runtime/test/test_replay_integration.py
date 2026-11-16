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

"""A talker and a listener replayed from a bag give the same recorded outputs on every run;
skipped without the tracer library, rosbag2 or the demo nodes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from autoware_system_designer_runtime._impl.measure.tracer import locate_tracer

from .measure_fixtures import in_port, node, out_port, process, system

S = 1_000_000_000
T0 = 1_700_000_000 * S
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _ready() -> bool:
    try:
        import rosbag2_py  # noqa: F401
        from ament_index_python.packages import get_package_prefix

        locate_tracer()
        return (Path(get_package_prefix("demo_nodes_cpp")) / "lib" / "demo_nodes_cpp" / "talker").is_file()
    except Exception:  # noqa: BLE001
        return False


pytestmark = [
    pytest.mark.timing,
    pytest.mark.skipif(not _ready(), reason="tracer library, rosbag2_py or demo_nodes_cpp not installed"),
]


def _demo_node(path: str, executable: str, **ports) -> dict:
    entity = node(path, **ports)
    entity["launcher"].update({"package": "demo_nodes_cpp", "executable": executable})
    return entity


def _design() -> dict:
    """/talker publishes /chatter from a one-second wall timer; /listener hears it."""
    return system(
        [
            _demo_node(
                "/talker",
                "talker",
                out_ports=[out_port("chatter", "/chatter", "t.out", ["t.tick"])],
                events=[process("tick", "t.tick", "periodic", [], ["t.out"], 1.0)],
            ),
            _demo_node("/listener", "listener", in_ports=[in_port("chatter", "/chatter", "l.in")]),
        ]
    )


def _write_bag(path: Path, seconds: float) -> None:
    """A bag of /tick messages every half second; no node hears them, they carry the clock forward."""
    import rosbag2_py
    from rclpy.serialization import serialize_message
    from std_msgs.msg import String

    writer = rosbag2_py.SequentialWriter()
    writer.open(
        rosbag2_py.StorageOptions(uri=str(path), storage_id="sqlite3"),
        rosbag2_py.ConverterOptions(input_serialization_format="cdr", output_serialization_format="cdr"),
    )
    writer.create_topic(rosbag2_py.TopicMetadata(name="/tick", type="std_msgs/msg/String", serialization_format="cdr"))
    for step in range(int(seconds * 2) + 1):
        writer.write("/tick", serialize_message(String(data=str(step))), T0 + step * S // 2)
    del writer


def _replay(tmp_path: Path, name: str, domain: int, *arguments: str) -> tuple[int, dict, Path]:
    out = tmp_path / name
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{SOURCE_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "autoware_system_designer_runtime.replay",
            str(tmp_path / "design.json"),
            "--scope",
            "/talker",
            "--scope",
            "/listener",
            "--bag",
            str(tmp_path / "bag"),
            "--config",
            str(tmp_path / "replay.yaml"),
            "--domain-id",
            str(domain),
            "--log-dir",
            str(out),
            "--reference",
            str(tmp_path / "reference"),
            *arguments,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    result_file = out / "replay" / "result.json"
    result = json.loads(result_file.read_text()) if result_file.is_file() else {"stderr": done.stderr[-4000:]}
    return done.returncode, result, out


def _outputs(run: Path) -> list[tuple[int, str]]:
    from rclpy.serialization import deserialize_message
    from std_msgs.msg import String

    from autoware_system_designer_runtime._impl.replay.bag import read_messages

    return [
        (stamp - T0, deserialize_message(data, String).data)
        for stamp, _, data in read_messages(run / "replay" / "outputs")
    ]


def test_talker_listener_replay_is_repeatable(tmp_path, ros_domain_id):
    (tmp_path / "design.json").write_text(json.dumps(_design()))
    (tmp_path / "replay.yaml").write_text("inputs: [/tick]\noutputs: [/chatter]\n")
    _write_bag(tmp_path / "bag", 3.2)

    code, first, run = _replay(tmp_path, "first", ros_domain_id, "--save-reference")
    assert code == 0, first
    assert first["completed"] and first["violations"] == []
    # The talker's wall timer runs on driver time: one message per second of the bag.
    assert _outputs(run) == [(1 * S, "Hello World: 1"), (2 * S, "Hello World: 2"), (3 * S, "Hello World: 3")]
    assert (first["timer_fires"], first["outputs"]) == (3, 3)
    # Each /chatter message ran the listener's callback once.
    assert first["callbacks"] == 6

    code, second, _ = _replay(tmp_path, "second", ros_domain_id)
    assert code == 0, second
    assert second["reference"]["passed"] is True
    assert second["outputs_digest"] == first["outputs_digest"]

    code, shorter, _ = _replay(tmp_path, "shorter", ros_domain_id, "--duration", "2.2")
    assert code == 1, shorter
    assert shorter["outputs"] == 2 and shorter["reference"]["passed"] is False


def test_check_endpoints_needs_no_bag_and_reports_the_scope(tmp_path, ros_domain_id):
    design = _design()
    design["data"]["children"][1]["in_ports"] = []
    (tmp_path / "design.json").write_text(json.dumps(design))
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{SOURCE_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "autoware_system_designer_runtime.replay",
            str(tmp_path / "design.json"),
            "--scope",
            "/",
            "--check-endpoints",
            "--domain-id",
            str(ros_domain_id),
            "--log-dir",
            str(tmp_path / "run"),
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    result = json.loads((tmp_path / "run" / "replay" / "result.json").read_text())
    # The listener subscribes to /chatter, which this design does not declare.
    assert done.returncode == 2, done.stderr[-2000:]
    assert [p["code"] for p in result["endpoint_problems"] if p["level"] == "error"] == ["undeclared_subscription"]
