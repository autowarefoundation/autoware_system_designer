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

"""A replay scope is selected by design path and bounded by the design's topics."""

import pytest

from autoware_system_designer_runtime._impl.replay.scope import (
    ScopeError,
    compute_boundary,
    replay_structure,
    scope_node_fqns,
    select_scope,
    unconnected_ports,
)

from .measure_fixtures import chain_design, in_port, node, out_port, system


def pipeline_design() -> dict:
    """/src → /in → /mod/a → /mid → /mod/b → /out → /sink; /mod/b also hears /tf from /src and /mod/a."""
    return system(
        [
            node("/src", out_ports=[out_port("in", "/in", "s.out", []), out_port("tf", "/tf", "s.tf", [])]),
            node(
                "/mod/a",
                in_ports=[in_port("in", "/in", "a.in"), in_port("lonely", "/lonely", "a.lonely")],
                out_ports=[out_port("mid", "/mid", "a.out", []), out_port("tf", "/tf", "a.tf", [])],
            ),
            node(
                "/mod/b",
                in_ports=[in_port("mid", "/mid", "b.in"), in_port("tf", "/tf", "b.tf")],
                out_ports=[out_port("out", "/out", "b.out", [])],
            ),
            node("/sink", in_ports=[in_port("out", "/out", "k.in")]),
        ]
    )


def test_scope_selects_nodes_under_a_path_in_design_order():
    scope = select_scope(pipeline_design()["data"], ["/mod"])

    assert scope.nodes == ["/mod/a", "/mod/b"]
    assert scope.containers == []


def test_scope_includes_the_container_of_a_selected_composable_node():
    scope = select_scope(chain_design()["data"], ["/d"])

    assert scope.nodes == ["/d"]
    assert scope.containers == ["/cc"]
    assert scope.members == ["/d", "/cc"]


def test_scope_path_matches_whole_segments():
    design = system([node("/mod/a"), node("/module/b")])

    assert select_scope(design["data"], ["/mod"]).nodes == ["/mod/a"]


def test_scope_that_selects_nothing_is_an_error():
    with pytest.raises(ScopeError, match="/nowhere"):
        select_scope(pipeline_design()["data"], ["/mod", "/nowhere"])


def test_boundary_inputs_are_fed_from_outside_and_outputs_leave_or_end():
    design = pipeline_design()
    boundary = compute_boundary(design, select_scope(design["data"], ["/mod"]))

    inputs = {entry.topic: entry for entry in boundary.inputs}
    assert set(inputs) == {"/in", "/lonely", "/tf"}
    assert inputs["/in"].publishers == ["/src"]
    assert inputs["/lonely"].publishers == []
    # /tf comes from outside and from the scope itself.
    assert inputs["/tf"].publishers == ["/src"]
    assert inputs["/tf"].scope_publishers == ["/mod/a"]

    outputs = {entry.topic: entry for entry in boundary.outputs}
    assert outputs["/out"].is_boundary and outputs["/out"].outside_subscribers == ["/sink"]
    assert not outputs["/mid"].is_boundary
    assert boundary.boundary_outputs() == {"/out": "std_msgs/msg/String"}
    assert set(boundary.output_types()) == {"/mid", "/out", "/tf"}


def test_boundary_lists_service_ports_with_their_scope_peers():
    client = {"name": "ask", "msg_type": "std_srvs/srv/Trigger", "topic": ["ask"], "role": "client"}
    server = {"name": "ask", "msg_type": "std_srvs/srv/Trigger", "topic": ["ask"], "role": "server"}
    design = system([node("/mod/a", in_ports=[client]), node("/mod/b", out_ports=[server]), node("/c")])

    inside = compute_boundary(design, select_scope(design["data"], ["/mod"]))
    assert [(e.node, e.role, e.scope_peers) for e in inside.services] == [
        ("/mod/a", "client", ["/mod/b"]),
        ("/mod/b", "server", ["/mod/a"]),
    ]

    alone = compute_boundary(design, select_scope(design["data"], ["/mod/a"]))
    assert [(e.node, e.scope_peers) for e in alone.services] == [("/mod/a", [])]


def test_replay_structure_keeps_the_scope_on_simulated_time_without_intra_process():
    design = chain_design()
    scope = select_scope(design["data"], ["/d", "/b"])
    structure = replay_structure(design["data"], scope)

    kept = {child["path"]: child for child in structure["children"]}
    assert set(kept) == {"/b", "/cc", "/d"}
    assert kept["/d"]["launcher"]["use_intra_process_comms"] is False
    assert {"name": "use_sim_time", "value": True, "type": "bool", "parameter_type": "MODE"} in kept["/d"]["parameters"]
    # The design itself is left as it was.
    assert next(c for c in design["data"]["children"] if c["path"] == "/d")["launcher"]["use_intra_process_comms"]


def test_scope_fqns_and_unconnected_ports():
    design = system(
        [
            node(
                "/mod/a",
                in_ports=[
                    in_port("in", "/in", "a.in"),
                    {"name": "spare", "msg_type": "std_msgs/msg/String", "remap_target": "~/input/spare"},
                ],
            )
        ]
    )
    scope = select_scope(design["data"], ["/mod"])

    assert scope_node_fqns(design, scope) == {"/mod/a": "/mod/a"}
    assert unconnected_ports(design, scope) == {"/mod/a": {"/mod/a/input/spare"}}
