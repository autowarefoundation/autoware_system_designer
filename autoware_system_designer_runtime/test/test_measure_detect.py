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

"""Trigger detection diffs the observed event table against the declared process events."""

import pytest

from autoware_system_designer_runtime._impl.measure.detect import (
    STATUS_MATCH,
    STATUS_MISMATCH,
    STATUS_RATE_MISMATCH,
    STATUS_UNDECLARED,
    STATUS_UNKNOWN,
    STATUS_UNOBSERVED,
    ObservedTrigger,
    diff_node,
    diff_output,
    observed_trigger,
)
from autoware_system_designer_runtime._impl.measure.node_graph import DeclaredTrigger, NodeGraph
from autoware_system_designer_runtime._impl.measure.node_stats import analyze
from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir

from .measure_fixtures import chain_design, write_chain_traces


@pytest.fixture
def run(tmp_path):
    start, end = write_chain_traces(tmp_path)
    graph = NodeGraph.from_system_structure(chain_design())
    return graph, analyze(read_trace_dir(tmp_path), graph, start, end)


def test_observed_trigger_is_the_dominant_class(run):
    _, analysis = run
    observed = observed_trigger(analysis.nodes["/a"], "/x")
    assert observed.kind == "timer" and observed.period_ms == 20.0 and observed.share == 1.0
    assert observed.distribution == {"timer(20 ms)": 50}
    assert observed_trigger(analysis.nodes["/a"], "/nothing") is None


def test_diff_output_statuses():
    periodic = DeclaredTrigger(kind="periodic", rate_hz=50.0)
    timer = ObservedTrigger(kind="timer", share=1.0, total=10, period_ms=20.0)
    assert diff_output(periodic, timer, observed_rate_hz=50.0).status == STATUS_MATCH
    slow = ObservedTrigger(kind="timer", share=1.0, total=10, period_ms=100.0)
    assert diff_output(periodic, slow, observed_rate_hz=10.0).status == STATUS_RATE_MISMATCH
    by_input = ObservedTrigger(kind="input", share=0.9, total=10, topic="/x")
    assert diff_output(periodic, by_input, observed_rate_hz=50.0).status == STATUS_MISMATCH
    assert diff_output(periodic, None, observed_rate_hz=None).status == STATUS_UNOBSERVED

    driven = DeclaredTrigger(kind="input", topics=["/x"], gate="on_input")
    assert diff_output(driven, by_input, observed_rate_hz=50.0).status == STATUS_MATCH
    other = ObservedTrigger(kind="input", share=1.0, total=10, topic="/other")
    assert diff_output(driven, other, observed_rate_hz=50.0).status == STATUS_MISMATCH
    assert diff_output(driven, timer, observed_rate_hz=50.0).status == STATUS_MISMATCH
    chained = DeclaredTrigger(kind="input", topics=["/x"], gate="and", upstream_rate_hz=50.0)
    assert diff_output(chained, timer, observed_rate_hz=50.0).status == STATUS_MATCH

    none = DeclaredTrigger(kind="none")
    assert diff_output(none, by_input, observed_rate_hz=1.0).status == STATUS_UNDECLARED
    assert diff_output(none, None, observed_rate_hz=None).status == STATUS_UNDECLARED
    unknown = ObservedTrigger(kind="unknown", share=1.0, total=3)
    assert diff_output(driven, unknown, observed_rate_hz=1.0).status == STATUS_UNKNOWN


def test_diff_node_over_the_synthetic_run(run):
    graph, analysis = run
    rows = {row.output: row for row in diff_node(graph, analysis, analysis.nodes["/a"]).rows}
    assert rows["/x"].status == STATUS_MATCH
    rows = {row.output: row for row in diff_node(graph, analysis, analysis.nodes["/e"]).rows}
    assert rows["/r"].status == STATUS_MATCH
    rows = {row.output: row for row in diff_node(graph, analysis, analysis.nodes["/d"]).rows}
    assert rows["/q"].status == STATUS_MATCH and "intra_process" in rows["/q"].observed
    rows = {row.output: row for row in diff_node(graph, analysis, analysis.nodes["/f"]).rows}
    assert rows["/w"].status == STATUS_UNKNOWN


def test_diff_node_reports_inputs_that_feed_nothing_and_never_taken(tmp_path):
    start, end = write_chain_traces(tmp_path)
    design = chain_design()
    # /b declares a second trigger topic it never takes, and /e's input feeds no process.
    b = next(c for c in design["data"]["children"] if c["path"] == "/b")
    b["in_ports"].append(
        {
            "name": "z",
            "msg_type": "std_msgs/msg/String",
            "topic": ["z"],
            "event": {"unique_id": "b.z", "type": "on_input", "trigger_ids": [], "action_ids": []},
        }
    )
    b["events"][0]["trigger_ids"].append("b.z")
    graph = NodeGraph.from_system_structure(design)
    analysis = analyze(read_trace_dir(tmp_path), graph, start, end)

    # The trace holds no subscription of /b to /z: the node never asked for it.
    diff = diff_node(graph, analysis, analysis.nodes["/b"])
    assert diff.never_subscribed == ["/z"] and diff.never_taken == []
    assert diff_node(graph, analysis, analysis.nodes["/e"]).feeds_nothing == ["/x"]

    # Subscribed but silent is the other case.
    analysis.nodes["/b"].subscribed.add("/z")
    diff = diff_node(graph, analysis, analysis.nodes["/b"])
    assert diff.never_subscribed == [] and diff.never_taken == ["/z"]


def test_diff_node_reports_declared_outputs_never_advertised(tmp_path):
    start, end = write_chain_traces(tmp_path)
    design = chain_design()
    b = next(c for c in design["data"]["children"] if c["path"] == "/b")
    b["out_ports"].append(
        {
            "name": "debug",
            "msg_type": "std_msgs/msg/String",
            "topic": ["b_debug"],
            "event": {"unique_id": "b.debug", "type": "to_output", "trigger_ids": ["b.run"], "action_ids": []},
        }
    )
    graph = NodeGraph.from_system_structure(design)
    analysis = analyze(read_trace_dir(tmp_path), graph, start, end)
    assert diff_node(graph, analysis, analysis.nodes["/b"]).never_advertised == ["/b_debug"]
    assert diff_node(graph, analysis, analysis.nodes["/a"]).never_advertised == []


def test_untriggered_process_differs_from_any_observation():
    untriggered = DeclaredTrigger(kind="untriggered", gate="or")
    timer = ObservedTrigger(kind="timer", share=1.0, total=10, period_ms=100.0)
    row = diff_output(untriggered, timer, observed_rate_hz=10.0)
    assert row.status == STATUS_MISMATCH and "declares no trigger" in row.note
    assert diff_output(untriggered, None, observed_rate_hz=None).status == STATUS_UNOBSERVED
