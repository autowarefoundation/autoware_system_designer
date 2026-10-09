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

"""Chains follow the message flow from detected timers through takes, sampled inputs and intra hops."""

import pytest

from autoware_system_designer_runtime._impl.measure.chains import trace_chains
from autoware_system_designer_runtime._impl.measure.node_graph import NodeGraph
from autoware_system_designer_runtime._impl.measure.node_stats import analyze
from autoware_system_designer_runtime._impl.measure.trace_reader import read_trace_dir

from .measure_fixtures import chain_design, write_chain_traces


@pytest.fixture
def chains(tmp_path):
    start, end = write_chain_traces(tmp_path)
    graph = NodeGraph.from_system_structure(chain_design())
    analysis = analyze(read_trace_dir(tmp_path), graph, start, end)
    return {(c.source, c.target): c for c in trace_chains(analysis)}


def test_direct_chain_through_a_dds_hop(chains):
    chain = chains[("/a:timer:20", "/b:/y")]
    assert chain.hops == 2 and chain.terminal
    assert chain.path == ["/a", "/b"]
    # fire → +1 ms publish (+50 us out) → +500 us take → +2 ms publish
    assert chain.summary.count == 50
    assert chain.summary.mean_ms == pytest.approx(3.55, abs=1e-6)
    assert chain.summary.sd_ms == pytest.approx(0.0, abs=1e-6)


def test_intermediate_publish_is_reported_but_not_terminal(chains):
    chain = chains[("/a:timer:20", "/a:/x")]
    assert chain.hops == 1 and not chain.terminal
    assert chain.summary.mean_ms == pytest.approx(1.0)


def test_sampled_input_continues_at_the_next_publish(chains):
    chain = chains[("/a:timer:20", "/e:/r")]
    assert chain.hops == 2 and chain.terminal
    # the next /r publish after the take, at most one /e period later
    assert chain.summary.min_ms > 1.0
    assert chain.summary.max_ms <= 1.0 + 0.35 + 10.5 + 1e-6
    assert chain.summary.count == 50


def test_intra_process_hop_continues_through_the_upstream_publish(chains):
    chain = chains[("/c:timer:50", "/d:/q")]
    assert chain.hops == 2 and chain.terminal
    assert chain.path == ["/c", "/d"]
    assert chain.summary.mean_ms == pytest.approx(4.0)


def test_own_timer_chain_of_the_sampling_node(chains):
    chain = chains[("/e:timer:10", "/e:/r")]
    assert chain.hops == 1 and chain.terminal
    assert chain.summary.mean_ms == pytest.approx(0.5)


def test_unknown_trigger_starts_no_chain(chains):
    assert not any(source.startswith("/f") for source, _ in chains)
