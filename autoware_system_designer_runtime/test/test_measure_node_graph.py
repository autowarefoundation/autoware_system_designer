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

"""The node graph reads ports and links from the export and keeps declared events as the claim."""

from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph, resolve_topic

from .measure_fixtures import chain_design, in_port, node, out_port, process, system


def test_resolve_topic_accepts_segments_and_strings():
    assert resolve_topic({"topic": ["a", "b/c"]}) == "/a/b/c"
    assert resolve_topic({"topic": "x/y"}) == "/x/y"
    assert resolve_topic({"topic": []}) is None
    assert resolve_topic({}) is None


def test_links_follow_topics_between_nodes_and_flag_intra_process():
    graph = NodeGraph.from_system_structure(chain_design())

    links = {(l.topic, l.publisher, l.subscriber): l.intra_process for l in graph.links}
    assert links[("/x", "/a", "/b")] is False
    assert links[("/x", "/a", "/e")] is False
    assert links[("/p", "/c", "/d")] is True
    assert graph.intra_inputs(graph.nodes["/d"]) == {"/p"}
    assert graph.intra_inputs(graph.nodes["/b"]) == set()


def test_process_key_groups_composables_with_their_container():
    graph = NodeGraph.from_system_structure(chain_design())

    assert graph.nodes["/c"].process_key == graph.nodes["/d"].process_key == "/cc"
    assert graph.nodes["/a"].process_key == "/a"


def test_probe_topics_are_those_read_only_intra_process():
    design = system(
        [
            node("/cc", state="node_container"),
            node("/c", out_ports=[out_port("p", "/p", "c.out", []), out_port("lonely", "/lonely", "c.out2", [])], state="composable_node", container="/cc", intra=True),
            node("/d", in_ports=[in_port("p", "/p", "d.in")], state="composable_node", container="/cc", intra=True),
            node("/g", in_ports=[in_port("p", "/p", "g.in")]),
            node("/h", out_ports=[out_port("z", "/z", "h.out", [])]),
        ]
    )
    graph = NodeGraph.from_system_structure(design)

    # /p has a DDS reader (/g) so its publishes are visible; /lonely has no reader at all.
    assert graph.probe_topics() == [("/lonely", "std_msgs/msg/String")]

    design["data"]["children"] = [c for c in design["data"]["children"] if c["path"] != "/g"]
    graph = NodeGraph.from_system_structure(design)
    assert [t for t, _ in graph.probe_topics()] == ["/lonely", "/p"]


def test_declared_trigger_reads_clock_inputs_and_absence():
    graph = NodeGraph.from_system_structure(chain_design())

    periodic = graph.declared_trigger(graph.nodes["/a"], "/x")
    assert periodic.kind == "periodic" and periodic.rate_hz == 50.0
    assert periodic.label() == "periodic 50 Hz"

    driven = graph.declared_trigger(graph.nodes["/b"], "/y")
    assert driven.kind == "input" and driven.topics == ["/x"] and driven.gate == "on_input"

    none = graph.declared_trigger(graph.nodes["/f"], "/w")
    assert none.kind == "none"


def test_declared_trigger_follows_process_chains_to_an_upstream_clock():
    design = system(
        [
            node(
                "/lidar",
                out_ports=[out_port("points", "/points", "l.out", ["l.decode"])],
                events=[
                    process("hw", "l.hw", "periodic", [], ["l.decode"], 100.0),
                    process("decode", "l.decode", "and", ["l.hw", "l.in"], ["l.out"]),
                ],
                in_ports=[in_port("packets", "/packets", "l.in")],
            )
        ]
    )
    graph = NodeGraph.from_system_structure(design)

    trigger = graph.declared_trigger(graph.nodes["/lidar"], "/points")
    assert trigger.kind == "input"
    assert trigger.topics == ["/packets"]
    assert trigger.gate == "and"
    assert trigger.upstream_rate_hz == 100.0


def test_ecu_filter_and_service_ports():
    design = chain_design()
    design["data"]["children"][0]["compute_unit"] = "other"
    design["data"]["children"][1]["in_ports"].append(in_port("svc", "/svc", "b.svc", msg="std_srvs/srv/Trigger"))
    graph = NodeGraph.from_system_structure(design, ecu="main")

    assert "/a" not in graph.nodes  # filtered out by ecu
    assert "/svc" not in graph.nodes["/b"].inputs if "/b" in graph.nodes else True
