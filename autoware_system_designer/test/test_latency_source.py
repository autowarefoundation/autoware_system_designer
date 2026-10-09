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

"""The latency file in both shapes: bare JSON and the bundle's script."""

import json

from autoware_system_designer.visualizer.latency_source import latency_script_text, parse_latency_text

DATA = {"schema": "autoware_system_designer/latency/2", "run": {"mode": 'Say "hi"\\now'}}


def test_bare_json_parses():
    assert parse_latency_text(json.dumps(DATA)) == DATA


def test_script_round_trips_with_escapes_in_the_mode():
    text = latency_script_text('Say "hi"\\now', DATA)
    assert text.startswith(
        'window.latencyData = window.latencyData || {};\nwindow.latencyData["Say \\"hi\\"\\\\now"] = '
    )
    assert parse_latency_text(text) == DATA


def test_script_without_a_trailing_semicolon_parses():
    text = f'window.latencyData["Test"] = {json.dumps(DATA)}'
    assert parse_latency_text(text) == DATA
