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

"""Invariants across schema files that cannot be expressed with a cross-file ``$ref``."""

from autoware_system_designer import DESIGN_FORMAT_VERSION
from autoware_system_designer.schema.json_schema_loader import load_schema


def test_required_data_type_enum_matches_data_category_enum():
    data_schema = load_schema("data", DESIGN_FORMAT_VERSION)
    node_schema = load_schema("node", DESIGN_FORMAT_VERSION)
    categories = data_schema["properties"]["category"]["enum"]
    types = node_schema["properties"]["required_data"]["items"]["properties"]["type"]["enum"]
    assert categories == types


def test_required_data_binding_references_are_bundle_keys():
    node_schema = load_schema("node", DESIGN_FORMAT_VERSION)
    binding = node_schema["properties"]["required_data"]["items"]["properties"]["binding"]["properties"]
    assert list(binding) == ["param_values"]
    assert binding["param_values"]["items"]["properties"]["from"]["pattern"] == "^bundle:.+$"
