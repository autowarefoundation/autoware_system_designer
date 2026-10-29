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

"""Cached YAML data is isolated from mutation by its consumers."""

from pathlib import Path

from autoware_system_designer.parser.data_parser import ConfigParser
from autoware_system_designer.parser.yaml_parser import YamlParser

FIXTURES = Path(__file__).parent / "fixtures" / "designs"
TALKER = FIXTURES / "single_node" / "demo_pkg" / "Talker.node.yaml"


def test_cache_hit_is_unaffected_by_mutation_of_earlier_result(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text("a: [1, 2]\nb: {c: 3}\n")
    parser = YamlParser(cache_enabled=True)

    first = parser.load_config(path)
    first.pop("a")
    first["b"]["c"] = 0

    assert parser.load_config(path) == {"a": [1, 2], "b": {"c": 3}}

    with_source, _ = parser.load_config_with_source(path)
    with_source.clear()
    assert parser.load_config_with_source(path)[0] == {"a": [1, 2], "b": {"c": 3}}


def test_entity_file_parses_repeatedly_with_cache(monkeypatch):
    import autoware_system_designer.parser.data_parser as data_parser

    monkeypatch.setattr(data_parser, "yaml_parser", YamlParser(cache_enabled=True))
    parser = ConfigParser(strict_mode=False)

    first = parser.parse_entity_file(str(TALKER))
    second = parser.parse_entity_file(str(TALKER))

    assert [p.name for p in second.outputs] == [p.name for p in first.outputs]
