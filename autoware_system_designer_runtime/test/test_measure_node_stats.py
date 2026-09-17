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

"""Node statistics on a synthetic run: triggers, process time, response, links, duplicates."""

import pytest

from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.node_stats import analyze, summarize
from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir

from .measure_fixtures import MS, chain_design, write_chain_traces


@pytest.fixture
def analysis(tmp_path):
    start, end = write_chain_traces(tmp_path)
    graph = NodeGraph.from_system_structure(chain_design())
    return analyze(read_trace_dir(tmp_path), graph, start, end)


def _labels(node, topic):
    return {p.trigger.label() for p in node.pubs_by_topic[topic]}


def test_summarize_is_a_population_summary_in_ms():
    s = summarize([1 * MS, 3 * MS])
    assert (s.count, s.min_ms, s.mean_ms, s.max_ms, s.sd_ms) == (2, 1.0, 2.0, 3.0, 1.0)
    assert summarize([]) is None


def test_timer_triggered_output_has_timer_trigger_and_exec(analysis):
    a = analysis.nodes["/a"]
    assert _labels(a, "/x") == {"timer(20 ms)"}
    execs = [p.exec_ns for p in a.pubs_by_topic["/x"]]
    assert all(e == 1 * MS for e in execs)
    assert len(a.pubs_by_topic["/x"]) == 50
    assert analysis.timer_owner[(100, 0xA1)] == "/a"
    assert analysis.timer_fires[(100, 0xA1)] == 50


def test_input_triggered_output_measures_from_the_take(analysis):
    b = analysis.nodes["/b"]
    assert _labels(b, "/y") == {"input(/x)"}
    assert {p.exec_ns for p in b.pubs_by_topic["/y"]} == {2 * MS}
    assert len(b.takes_by_topic["/x"]) == 50


def test_link_communication_is_take_minus_source_timestamp(analysis):
    values = analysis.links[("/x", "/a", "/b")]
    assert len(values) == 50
    # publish out 50 us after in, take 500 us after out, source 20 us after in
    assert all(v == 530_000 for v in values)
    assert ("/x", "/a", "/e") in analysis.links
    assert analysis.comm_invalid == 0


def test_sampling_node_reports_response_from_the_sampled_input(analysis):
    e = analysis.nodes["/e"]
    assert _labels(e, "/r") == {"timer(10 ms)"}
    responses = e.response[("/r", "/x")]
    assert len(responses) == len(e.pubs_by_topic["/r"])
    assert min(responses) > 0 and max(responses) <= 20 * MS + 1 * MS


def test_intra_process_hop_is_folded_into_the_downstream_exec(analysis):
    d = analysis.nodes["/d"]
    assert d.dup_takes == {"/p": 20}
    assert "/p" not in d.takes_by_topic
    assert _labels(d, "/q") == {"input(/p, intra_process)"}
    assert {p.exec_ns for p in d.pubs_by_topic["/q"]} == {3 * MS}
    assert ("/p", "/c", "/d") not in analysis.links


def test_publish_without_a_visible_trigger_is_unknown(analysis):
    f = analysis.nodes["/f"]
    assert _labels(f, "/w") == {"unknown"}
    assert all(p.exec_ns is None for p in f.pubs_by_topic["/w"])


def test_window_excludes_records_outside_it(tmp_path):
    start, end = write_chain_traces(tmp_path)
    graph = NodeGraph.from_system_structure(chain_design())
    half = analyze(read_trace_dir(tmp_path), graph, start + 500 * MS, end)
    assert len(half.nodes["/a"].pubs_by_topic["/x"]) == 25
    assert abs(half.window_s - 0.5) < 1e-9
    assert half.rate(25) == pytest.approx(50.0)


def test_unmatched_nodes_are_kept_by_ros_name(tmp_path):
    start, end = write_chain_traces(tmp_path)
    design = chain_design()
    design["data"]["children"] = [c for c in design["data"]["children"] if c["path"] != "/f"]
    graph = NodeGraph.from_system_structure(design)
    result = analyze(read_trace_dir(tmp_path), graph, start, end)
    assert [n.fqn for n in result.unmatched_nodes()] == ["/f"]
