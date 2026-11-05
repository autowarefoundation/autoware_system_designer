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

"""Declared compute units: validation of the declaration and its export."""

import pytest
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.builder.compute_units import check_component_compute_units
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.deploy import load_build_artifacts
from autoware_system_designer.model.config import ComputeUnit
from autoware_system_designer.parser.data_parser import ConfigParser
from autoware_system_designer.schema.yaml_schema import get_semantic_checks

SYSTEM_HEAD = """\
autoware_system_design_format: 0.5.0

name: Probe.system

modes:
  - name: default
    default: true
  - name: sim

compute_units:
  - name: main_ecu
  - name: sub_ecu
    kind: offboard
"""


def _system_issues(text: str):
    import yaml

    config = yaml.safe_load(text)
    issues = []
    for check in get_semantic_checks("system"):
        issues.extend(check(config))
    return [issue for issue in issues if issue.yaml_path != "/autoware_system_design_format"]


def test_declaration_accepts_bound_components():
    text = SYSTEM_HEAD + """
components:
  - name: a
    entity: A.node
    compute_unit: main_ecu
  - name: b
    entity: B.node
    compute_unit: sub_ecu

connections: []

sim:
  override:
    components:
      - name: b
        entity: BSim.node
        compute_unit: sub_ecu
"""
    assert _system_issues(text) == []


def test_declaration_rejects_undeclared_unit_in_mode_override():
    text = SYSTEM_HEAD + """
components:
  - name: a
    entity: A.node
    compute_unit: main_ecu

connections: []

sim:
  override:
    components:
      - name: a
        entity: ASim.node
        compute_unit: ghost_ecu
"""
    issues = _system_issues(text)
    assert len(issues) == 1
    assert issues[0].yaml_path == "/sim/override/components/0/compute_unit"
    assert "ghost_ecu" in issues[0].message


def test_declaration_rejects_component_without_unit():
    text = SYSTEM_HEAD + """
components:
  - name: a
    entity: A.node

connections: []
"""
    issues = _system_issues(text)
    assert [issue.yaml_path for issue in issues] == ["/components/0/compute_unit"]
    assert "has no compute_unit" in issues[0].message


def test_declaration_rejects_duplicate_unit_names():
    text = """\
autoware_system_design_format: 0.5.0

name: Probe.system

compute_units:
  - name: main_ecu
  - name: main_ecu

components:
  - name: a
    entity: A.node
    compute_unit: main_ecu

connections: []
"""
    issues = _system_issues(text)
    assert [issue.yaml_path for issue in issues] == ["/compute_units/1/name"]


def test_undeclared_system_keeps_free_form_units():
    """Designs that only tag components keep working: nothing to check against."""
    text = """\
autoware_system_design_format: 0.4.0

name: Probe.system

components:
  - name: a
    entity: A.node
    compute_unit: anything

connections: []
"""
    assert _system_issues(text) == []


def test_variant_root_declaration_is_forbidden():
    text = """\
autoware_system_design_format: 0.5.0

name: Probe.system

base: Other.system

compute_units:
  - name: main_ecu
"""
    issues = _system_issues(text)
    assert any(issue.yaml_path == "/compute_units" for issue in issues)


def test_unknown_kind_is_rejected_by_schema(tmp_path):
    path = tmp_path / "Probe.system.yaml"
    path.write_text("""\
autoware_system_design_format: 0.5.0

name: Probe.system

compute_units:
  - name: main_ecu
    kind: dummy

components:
  - name: a
    entity: A.node
    compute_unit: main_ecu

connections: []
""")
    with pytest.raises(ValidationError) as excinfo:
        ConfigParser().parse_entity_file(str(path))
    assert "/compute_units/0/kind" in str(excinfo.value)


@pytest.mark.parametrize("name", ["Main_ECU", "1st_ecu", "main-ecu", "main/ecu", ""])
def test_invalid_unit_name_is_rejected_by_schema(tmp_path, name):
    path = tmp_path / "Probe.system.yaml"
    path.write_text(f"""\
autoware_system_design_format: 0.5.0

name: Probe.system

compute_units:
  - name: "{name}"

components:
  - name: a
    entity: A.node
    compute_unit: "{name}"

connections: []
""")
    with pytest.raises(ValidationError) as excinfo:
        ConfigParser().parse_entity_file(str(path))
    assert "/compute_units/0/name" in str(excinfo.value)


def test_resolved_check_rejects_invalid_unit_name(tmp_path):
    """Mode overrides are not covered by the schema, so the resolved config checks names too."""
    path = tmp_path / "Probe.system.yaml"
    path.write_text(SYSTEM_HEAD + """
components:
  - name: a
    entity: A.node
    compute_unit: main_ecu

connections: []
""")
    config = ConfigParser().parse_entity_file(str(path))
    config.compute_units.append(ComputeUnit(name="Sim-ECU"))
    with pytest.raises(ValidationError) as excinfo:
        check_component_compute_units(config)
    assert "Sim-ECU" in str(excinfo.value)


def test_parser_builds_typed_compute_units(tmp_path):
    path = tmp_path / "Probe.system.yaml"
    path.write_text(SYSTEM_HEAD + """
components:
  - name: a
    entity: A.node
    compute_unit: main_ecu

connections: []
""")
    config = ConfigParser().parse_entity_file(str(path))
    assert config.compute_units == [
        ComputeUnit(name="main_ecu"),
        ComputeUnit(name="sub_ecu", kind="offboard"),
    ]
    assert config.compute_units[0].kind == "onboard"


def test_resolved_check_names_component_and_unit(tmp_path):
    path = tmp_path / "Probe.system.yaml"
    path.write_text(SYSTEM_HEAD + """
components:
  - name: a
    entity: A.node
    compute_unit: main_ecu

connections: []
""")
    config = ConfigParser().parse_entity_file(str(path))
    config.components.append({"name": "late", "entity": "L.node", "compute_unit": "ghost_ecu"})
    with pytest.raises(ValidationError) as excinfo:
        check_component_compute_units(config)
    assert "late" in str(excinfo.value)
    assert "ghost_ecu" in str(excinfo.value)


def test_export_carries_declaration_and_launches_every_declared_unit(tmp_path):
    workspace = stage_case("compute_units", tmp_path)
    run = run_pipeline(workspace, "units_pkg/Multi.system.yaml", tmp_path)

    artifacts = load_build_artifacts(str(run.out_root), run.system_name)
    assert [unit["name"] for unit in artifacts.compute_units] == ["main_ecu", "sub_ecu", "remote_ecu"]
    assert [unit["kind"] for unit in artifacts.compute_units] == ["onboard", "onboard", "offboard"]

    for mode in ("default", "solo"):
        structure = run.structure(mode)
        assert [unit["name"] for unit in structure["metadata"]["compute_units"]] == [
            "main_ecu",
            "sub_ecu",
            "remote_ecu",
        ]
        for unit in ("main_ecu", "sub_ecu"):
            assert (run.launcher_dir(mode) / unit / f"{unit}.launch.xml").is_file()
        # The offboard unit's component is designed but never launched by the export.
        assert "monitor" in {child["name"] for child in structure["data"]["children"]}
        assert not (run.launcher_dir(mode) / "remote_ecu").exists()
        assert not any(run.launcher_dir(mode).rglob("monitor*.launch.xml"))

    # solo mode removes the only sub_ecu component; the unit still has an (empty) launcher.
    solo_sub = (run.launcher_dir("solo") / "sub_ecu" / "sub_ecu.launch.xml").read_text()
    assert "<include" not in solo_sub

    page = (run.exports_dir / "visualization" / "web" / f"{run.system_name}_launch_commands.html").read_text()
    assert "--compute-unit sub_ecu" in page
    assert page.count("solo/sub_ecu/sub_ecu.launch.xml") >= 1
    assert "remote_ecu" not in page
