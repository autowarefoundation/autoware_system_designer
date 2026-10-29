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

"""Rates of a trigger condition: an `and` runs at its slowest condition, an `or` at its quickest,
and a `once` condition gates the chain without pacing it."""

import pytest

from autoware_system_designer.model.events import Event, Process, resolve_event_rates


def _inputs(**rates):
    """Input events standing for the ports a condition names, each arriving at its own rate."""
    events = {}
    for name, rate in rates.items():
        event = Event("input_" + name, ["ns"])
        event.set_type("on_input")
        event.frequency = rate
        events[name] = event
    return events


def _rate(trigger_conditions, inputs):
    process = Process("run", ["ns"], {"trigger_conditions": trigger_conditions, "outcomes": []})
    process.set_condition([process.event], list(inputs.values()))
    resolve_event_rates(list(inputs.values()) + process.get_event_list())
    return process.event


def test_and_runs_at_the_slowest_condition():
    inputs = _inputs(packets=100.0)
    event = _rate([{"and": [{"on_input": "packets"}, {"periodic": 10.0}]}], inputs)
    assert event.type == "and"
    assert event.frequency == 10.0


def test_or_runs_at_the_quickest_condition():
    inputs = _inputs(packets=100.0)
    event = _rate([{"or": [{"on_input": "packets"}, {"periodic": 10.0}]}], inputs)
    assert event.frequency == 100.0


def test_a_list_of_conditions_is_an_or():
    inputs = _inputs(packets=100.0, status=1.0)
    event = _rate([{"on_input": "packets"}, {"on_input": "status"}], inputs)
    assert event.frequency == 100.0


def test_once_gates_an_and_without_pacing_it():
    inputs = _inputs(pointcloud=10.0, vector_map=0.2)
    event = _rate([{"and": [{"on_input": "pointcloud"}, {"once": "vector_map"}]}], inputs)
    assert event.latches == [inputs["vector_map"]]
    assert inputs["vector_map"] in event.triggers
    assert event.frequency == 10.0


def test_a_bare_once_gates_an_and_without_pacing_it():
    inputs = _inputs(pointcloud=10.0)
    event = _rate([{"and": [{"on_input": "pointcloud"}, {"once": None}]}], inputs)
    assert event.frequency == 10.0


def test_a_once_alone_never_repeats():
    inputs = _inputs(vector_map=0.2)
    event = _rate([{"once": "vector_map"}], inputs)
    assert event.frequency == 0.0


def test_nested_chains_resolve_from_the_inside_out():
    inputs = _inputs(objects=50.0, trajectory=20.0)
    event = _rate(
        [{"and": [{"or": [{"on_input": "objects"}, {"on_input": "trajectory"}]}, {"periodic": 30.0}]}],
        inputs,
    )
    assert event.frequency == 30.0


def test_the_rate_reaches_the_outputs_of_the_process():
    inputs = _inputs(packets=100.0)
    output = Event("output_points", ["ns"])
    output.set_type("to_output")
    process = Process(
        "decode",
        ["ns"],
        {
            "trigger_conditions": [{"and": [{"on_input": "packets"}, {"periodic": 10.0}]}],
            "outcomes": [{"to_output": "points"}],
        },
    )
    process.set_condition([process.event], list(inputs.values()))
    process.set_outcomes([process.event], [output], {})
    resolve_event_rates(list(inputs.values()) + process.get_event_list() + [output])
    assert output.frequency == 10.0


def test_an_input_that_also_paces_is_no_latch():
    for conditions in (
        [{"on_input": "imu"}, {"on_input": "pointcloud"}, {"once": "imu"}],
        [{"once": "imu"}, {"on_input": "imu"}, {"on_input": "pointcloud"}],
    ):
        inputs = _inputs(imu=100.0, pointcloud=10.0)
        event = _rate([{"and": conditions}], inputs)
        assert event.latches == []
        assert event.frequency == 10.0


def _timed_inputs(**profiles):
    inputs = _inputs(**{name: rate for name, (rate, _) in profiles.items()})
    for name, (_, timeout) in profiles.items():
        inputs[name].timeout = timeout
    return inputs


def test_or_takes_the_timeout_of_its_quickest_condition():
    inputs = _timed_inputs(fast=(100.0, 0.05), slow=(1.0, 2.0))
    event = _rate([{"or": [{"on_input": "fast"}, {"on_input": "slow"}]}], inputs)
    assert event.frequency == 100.0
    assert event.timeout == 0.05


def test_and_takes_the_timeout_of_its_slowest_condition():
    inputs = _timed_inputs(fast=(100.0, 0.05), slow=(1.0, 2.0))
    event = _rate([{"and": [{"on_input": "fast"}, {"on_input": "slow"}]}], inputs)
    assert event.frequency == 1.0
    assert event.timeout == 2.0


def test_and_pairs_the_timeout_with_the_condition_its_rate_comes_from():
    inputs = _timed_inputs(fast=(10.0, 0.5), slow=(1.0, 0.2))
    event = _rate([{"and": [{"on_input": "fast"}, {"on_input": "slow"}]}], inputs)
    assert event.frequency == 1.0
    assert event.timeout == 0.2


def test_or_pairs_the_timeout_with_the_condition_its_rate_comes_from():
    inputs = _timed_inputs(fast=(50.0, 1.0), slow=(5.0, 0.1))
    event = _rate([{"or": [{"on_input": "fast"}, {"on_input": "slow"}]}], inputs)
    assert event.frequency == 50.0
    assert event.timeout == 1.0


def test_an_and_of_latches_alone_never_repeats():
    inputs = _inputs(vector_map=0.2, calibration=0.1)
    event = _rate([{"and": [{"once": "vector_map"}, {"once": "calibration"}]}], inputs)
    assert event.pacing_triggers == []
    assert event.frequency == 0.0


def test_a_wide_or_listed_downstream_first_settles_at_its_quickest_condition():
    # branch i is i + 1 stages long and carries the faster rate, so the `or` sees a new
    # quickest condition each time one more branch settles
    inputs = _inputs(**{f"in_{i}": 10.0 * (i + 1) for i in range(24)})
    processes = []
    for i in range(24):
        for depth in range(i + 1):
            condition = {"on_input": f"in_{i}"} if depth == 0 else {"on_trigger": f"b{i}_{depth - 1}"}
            processes.append(Process(f"b{i}_{depth}", ["ns"], {"trigger_conditions": [condition]}))
    merge = Process("merge", ["ns"], {"trigger_conditions": [{"on_trigger": f"b{i}_{i}"} for i in range(24)]})
    tail = Process("tail", ["ns"], {"trigger_conditions": [{"on_trigger": "merge"}]})
    processes += [merge, tail]
    process_events = [process.event for process in processes]
    for process in processes:
        process.set_condition(process_events, list(inputs.values()))
    events = [event for process in processes for event in process.get_event_list()]
    resolve_event_rates(list(reversed(events)) + list(inputs.values()))
    assert merge.event.frequency == 240.0
    assert tail.event.frequency == 240.0


def test_expected_latency_belongs_to_the_process_gate():
    inputs = _inputs(pointcloud=10.0)
    process = Process(
        "run",
        ["ns"],
        {
            "trigger_conditions": [{"and": [{"on_input": "pointcloud"}, {"periodic": 10.0}]}],
            "outcomes": [],
            "expected_latency_ms": 30.0,
        },
    )
    process.set_condition([process.event], list(inputs.values()))
    assert process.event.expected_latency_ms == 30.0
    assert process.event.process_event is True
    # the nested periodic condition is a gate of the chain, not a run of its own
    (condition,) = process.event.children
    assert condition.process_event is False
    assert condition.expected_latency_ms is None


def test_expected_latency_must_be_a_non_negative_number():
    inputs = _inputs(pointcloud=10.0)
    for value in (-0.1, "fast"):
        process = Process(
            "run",
            ["ns"],
            {"trigger_conditions": [{"on_input": "pointcloud"}], "outcomes": [], "expected_latency_ms": value},
        )
        with pytest.raises(ValueError, match="expected_latency_ms"):
            process.set_condition([process.event], list(inputs.values()))
