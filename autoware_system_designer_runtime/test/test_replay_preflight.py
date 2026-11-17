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

"""Pre-flight of a replay: the bag against the boundary, the real endpoints against the design."""

import pytest

from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.replay.bag import BagError, info_from_metadata, read_bag_info, remap_topics
from autoware_system_designer_runtime._impl.replay.config import ConfigError, ReplayConfig
from autoware_system_designer_runtime._impl.replay.preflight import (
    ERROR,
    WARNING,
    RealEndpoint,
    check_bag,
    check_endpoints,
    fed_topics,
)
from autoware_system_designer_runtime._impl.replay.scope import compute_boundary, select_scope

from .measure_fixtures import in_port, node, out_port, process, system
from .test_replay_scope import pipeline_design

TRANSIENT_LOCAL = "- history: 3\n  depth: 0\n  reliability: 1\n  durability: 1"


def bag(topics: dict) -> dict:
    return {
        "rosbag2_bagfile_information": {
            "storage_identifier": "sqlite3",
            "starting_time": {"nanoseconds_since_epoch": 1000},
            "duration": {"nanoseconds": 500},
            "topics_with_message_count": [
                {
                    "topic_metadata": {"name": name, "type": msg_type, "offered_qos_profiles": qos},
                    "message_count": count,
                }
                for name, (msg_type, count, qos) in topics.items()
            ],
        }
    }


def boundary_of(design, *patterns):
    return compute_boundary(design, select_scope(design["data"], list(patterns)))


def codes(problems, level=None):
    return sorted(p.code for p in problems if level is None or p.level == level)


def test_bag_metadata_gives_topics_types_and_span(tmp_path):
    (tmp_path / "metadata.yaml").write_text(
        "rosbag2_bagfile_information:\n"
        "  storage_identifier: mcap\n"
        "  starting_time: {nanoseconds_since_epoch: 1000}\n"
        "  duration: {nanoseconds: 500}\n"
        "  topics_with_message_count:\n"
        "    - topic_metadata: {name: /in, type: std_msgs/msg/String, offered_qos_profiles: ''}\n"
        "      message_count: 3\n"
    )

    info = read_bag_info(tmp_path)

    assert (info.storage_id, info.start_ns, info.end_ns) == ("mcap", 1000, 1500)
    assert info.topics["/in"].msg_type == "std_msgs/msg/String" and info.topics["/in"].count == 3
    assert info.fingerprint


def test_bag_durability_is_read_from_the_offered_profiles():
    info = info_from_metadata(
        bag({"/map": ("std_msgs/msg/String", 1, TRANSIENT_LOCAL), "/in": ("std_msgs/msg/String", 1, "")}), "bag"
    )

    assert info.topics["/map"].transient_local
    assert not info.topics["/in"].transient_local


def test_bag_with_every_input_passes_and_names_what_is_fed():
    boundary = boundary_of(pipeline_design(), "/mod/b")
    info = info_from_metadata(
        bag({"/mid": ("std_msgs/msg/String", 5, ""), "/tf": ("std_msgs/msg/String", 9, ""), "/x": ("a/msg/B", 1, "")}),
        "bag",
    )

    assert check_bag(boundary, info) == []
    assert fed_topics(boundary, info, ["/x"]) == {
        "/mid": "std_msgs/msg/String",
        "/tf": "std_msgs/msg/String",
        "/x": "a/msg/B",
    }


def test_missing_or_mistyped_input_fails_the_preflight():
    boundary = boundary_of(pipeline_design(), "/mod/b")
    info = info_from_metadata(bag({"/mid": ("std_msgs/msg/Int32", 5, "")}), "bag")

    problems = check_bag(boundary, info, extra_inputs=["/extra"])

    assert codes(problems, ERROR) == ["input_missing", "input_missing", "input_type"]


def test_accepted_input_and_input_without_publisher_do_not_fail():
    boundary = boundary_of(pipeline_design(), "/mod")
    info = info_from_metadata(bag({"/in": ("std_msgs/msg/String", 5, "")}), "bag")

    problems = check_bag(boundary, info, accept=["/tf"])

    # /lonely has no publisher in the design either.
    assert codes(problems) == ["input_without_publisher"]
    assert codes(check_bag(boundary, info), ERROR) == ["input_missing"]


def test_input_also_published_inside_the_scope_is_flagged():
    boundary = boundary_of(pipeline_design(), "/mod")
    info = info_from_metadata(
        bag({"/in": ("std_msgs/msg/String", 5, ""), "/tf": ("std_msgs/msg/String", 5, "")}), "bag"
    )

    assert "input_mixed" in codes(check_bag(boundary, info), WARNING)


def test_client_without_server_in_scope_fails_until_accepted():
    client = {"name": "ask", "msg_type": "std_srvs/srv/Trigger", "topic": ["ask"], "role": "client"}
    design = system([node("/mod/a", in_ports=[client])])
    boundary = boundary_of(design, "/mod")
    info = info_from_metadata(bag({}), "bag")

    assert codes(check_bag(boundary, info), ERROR) == ["service_outside"]
    assert check_bag(boundary, info, accept=["/ask"]) == []


def endpoint_design():
    return system(
        [
            node(
                "/mod/a",
                in_ports=[in_port("in", "/in", "a.in"), in_port("tf", "/tf", "a.tf")],
                out_ports=[out_port("out", "/out", "a.out", ["a.tick"])],
                events=[process("tick", "a.tick", "periodic", [], ["a.out"], 10.0)],
            )
        ]
    )


def check(endpoints, **kwargs):
    design = endpoint_design()
    return check_endpoints(NodeGraph.from_system_structure(design), {"/mod/a": "/mod/a"}, endpoints, **kwargs)


FULL = [
    RealEndpoint("s", "/mod/a", "/in"),
    # A helper node of the same process subscribes for the design node.
    RealEndpoint("s", "/transform_listener_impl_55aa", "/tf", process_nodes=("/mod/a",)),
    RealEndpoint("p", "/mod/a", "/out"),
    RealEndpoint("t", "/mod/a", period_ns=100_000_000),
    RealEndpoint("s", "/mod/a", "/parameter_events"),
    RealEndpoint("p", "/mod/a", "/rosout"),
]


def test_endpoints_that_match_the_design_raise_nothing():
    assert check(FULL) == []


def test_undeclared_subscription_fails_and_undeclared_publisher_warns():
    problems = check(FULL + [RealEndpoint("s", "/mod/a", "/secret"), RealEndpoint("p", "/mod/a", "/debug")])

    assert codes(problems, ERROR) == ["undeclared_subscription"]
    assert codes(problems, WARNING) == ["undeclared_publisher"]
    assert codes(check(FULL + [RealEndpoint("s", "/mod/a", "/secret")], accept=["/secret"]), ERROR) == []
    assert check(FULL + [RealEndpoint("s", "/mod/a", "/secret")], fed=["/secret"]) == []


def test_subscription_of_an_unconnected_port_only_warns():
    problems = check(
        FULL + [RealEndpoint("s", "/mod/a", "/mod/a/input/spare")], unconnected={"/mod/a": {"/mod/a/input/spare"}}
    )

    assert [(p.level, p.code) for p in problems] == [(WARNING, "input_unconnected")]


def test_missing_endpoints_and_timer_rate_mismatch_warn():
    problems = check([RealEndpoint("s", "/mod/a", "/in"), RealEndpoint("t", "/mod/a", period_ns=500_000_000)])

    assert codes(problems, ERROR) == []
    assert codes(problems) == ["event_mismatch", "input_not_subscribed", "output_not_published"]


def test_config_reads_rules_and_rejects_unknown_keys():
    config = ReplayConfig.from_mapping(
        {
            "scope": "/mod",
            "inputs": ["/tf"],
            "compare": {
                "default": "exact",
                "topics": {"/out": {"abs_tolerance": 1e-6, "ignore_fields": ["header.stamp"]}, "/diag": "ignore"},
            },
        }
    )

    assert config.scope == ["/mod"] and config.inputs == ["/tf"]
    assert config.rule_for("/out").rule == "fields" and config.rule_for("/out").abs_tolerance == 1e-6
    assert config.rule_for("/diag").rule == "ignore"
    assert config.rule_for("/other").rule == "exact"
    with pytest.raises(ConfigError, match="unknown"):
        ReplayConfig.from_mapping({"scopes": ["/mod"]})
    with pytest.raises(ConfigError, match="rule"):
        ReplayConfig.from_mapping({"compare": {"topics": {"/out": {"rule": "close"}}}})


def _recorded(topics: dict):
    return info_from_metadata(bag(topics), "bag")


def test_remap_feeds_a_recorded_topic_under_the_boundary_name():
    design = pipeline_design()
    boundary = boundary_of(design, "/mod")
    (missing,) = [entry for entry in boundary.inputs if entry.topic == "/in"]
    recorded = _recorded({"/vehicle/raw": (missing.msg_type, 4, ""), "/other": ("std_msgs/msg/Int32", 1, "")})

    problems = check_bag(boundary, recorded)
    assert "input_missing" in codes(problems, ERROR)
    assert any("to remap: /vehicle/raw" in p.message for p in problems)

    remapped = remap_topics(recorded, {"/vehicle/raw": missing.topic})
    assert "input_missing" not in codes(check_bag(boundary, remapped, accept=["/tf"]), ERROR)
    assert fed_topics(boundary, remapped, [])[missing.topic] == missing.msg_type
    assert remapped.topics[missing.topic].source == "/vehicle/raw"
    assert "/vehicle/raw" not in remapped.topics and remapped.topics["/other"].source == "/other"


def test_remap_onto_a_recorded_name_shadows_that_topic():
    design = pipeline_design()
    boundary = boundary_of(design, "/mod")
    (fed,) = [entry for entry in boundary.inputs if entry.topic == "/in"]
    recorded = _recorded({fed.topic: (fed.msg_type, 4, ""), "/retaken": (fed.msg_type, 2, "")})

    remapped = remap_topics(recorded, {"/retaken": fed.topic})

    assert remapped.shadowed == [fed.topic]
    assert (remapped.topics[fed.topic].source, remapped.topics[fed.topic].count) == ("/retaken", 2)
    assert "input_shadowed" in codes(check_bag(boundary, remapped), WARNING)


def test_remap_refuses_an_unrecorded_source_and_a_shared_target():
    recorded = _recorded({"/a": ("std_msgs/msg/String", 1, ""), "/b": ("std_msgs/msg/String", 1, "")})

    with pytest.raises(BagError, match="not in the bag"):
        remap_topics(recorded, {"/missing": "/x"})
    with pytest.raises(BagError, match="both to /x"):
        remap_topics(recorded, {"/a": "/x", "/b": "/x"})
    with pytest.raises(BagError, match="absolute"):
        remap_topics(recorded, {"/a": "x"})
    assert remap_topics(recorded, {"/a": "/a"}) is recorded


def test_config_reads_the_remap():
    assert ReplayConfig.from_mapping({"remap": {"/a": "/b"}}).remap == {"/a": "/b"}
    with pytest.raises(ConfigError, match="remap"):
        ReplayConfig.from_mapping({"remap": ["/a"]})
