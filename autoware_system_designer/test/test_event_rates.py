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
