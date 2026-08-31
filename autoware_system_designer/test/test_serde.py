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

"""Serde and model-record behavior: the dataclass declarations are the schema."""

from pathlib import Path

from autoware_system_designer.common.source_location import SourceLocation
from autoware_system_designer.model.domain import ParameterType
from autoware_system_designer.model.parameters import Parameter, ParameterFile
from autoware_system_designer.model.ports import InPort, OutPort
from autoware_system_designer.model.serde import dump


def test_parameter_record_normalizes_on_store():
    param = Parameter("gain", 1e-05, "float32")
    assert param.value == "0.00001"
    assert param.data_type == "double"

    param.assign("2e-3", "float64")
    assert param.value == "0.002"


def test_parameter_dump_shape():
    source = SourceLocation(file_path=Path("/ws/A.node.yaml"), line=3, column=5)
    data = dump(Parameter("gain", 0.5, "float", parameter_type=ParameterType.DEFAULT, source=source))
    assert data == {
        "name": "gain",
        "value": "0.5",
        "type": "double",
        "parameter_type": "DEFAULT",
        "source": {"file_path": "/ws/A.node.yaml", "yaml_path": None, "line": 3, "column": 5},
    }


def test_parameter_file_dump_excludes_working_fields():
    data = dump(ParameterFile("cfg", "config/a.yaml", schema_path="schema/a.json", is_override=True))
    assert "schema_path" not in data
    assert data["is_override"] is True
    assert data["parameter_type"] == "DEFAULT_FILE"


def test_port_dump_refs_and_computed():
    out_port = OutPort("objects", "pkg/msg/Objects", ["perception"])
    in_port = InPort("objects", "pkg/msg/Objects", ["planning"])
    out_port.set_users([in_port])
    in_port.set_servers([out_port])

    data = dump(out_port)
    assert data["unique_id"] == out_port.unique_id
    assert data["port_path"] == "/perception/output/objects"
    assert data["connected_ids"] == [in_port.unique_id]
    assert "reference" not in data
    assert data["event"]["type"] == "to_output"
    assert data["event"]["trigger_ids"] == []
