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

import pytest

from autoware_system_designer.builder.resolution.data_variant_scanner import DataVariantError, DataVariantScanner

# An enumerated axis over a sortable one, laid out as one directory per axis.
ML_AXES = [
    {"name": "model_variant", "values": ["standard", "lite"], "default": "standard"},
    {"name": "release_version", "values": ["v1", "v2", "v3", "v10"], "sortable": True, "default": "latest"},
]
ML_PATTERN = "lidar_centerpoint/$(var model_variant)/$(var release_version)"


def make_dirs(root, *relative_paths):
    for rel in relative_paths:
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def ml_root(tmp_path):
    return make_dirs(tmp_path, "lidar_centerpoint/standard/v1", "lidar_centerpoint/standard/v2")


def scanner(root, axes=ML_AXES, pattern=ML_PATTERN):
    return DataVariantScanner(root, axes, pattern)


# ── resolution ──────────────────────────────────────────────────────────────


def test_latest_resolves_to_greatest_existing_version(ml_root):
    resolved = scanner(ml_root).resolve({"model_variant": "standard", "release_version": "latest"})
    assert resolved.relative_path == "lidar_centerpoint/standard/v2"
    assert resolved.values == {"model_variant": "standard", "release_version": "v2"}


def test_natural_order_beats_lexicographic_order(tmp_path):
    root = make_dirs(tmp_path, "lidar_centerpoint/standard/v2", "lidar_centerpoint/standard/v10")
    resolved = scanner(root).resolve({"release_version": "latest"})
    assert resolved.values["release_version"] == "v10"


def test_mixed_form_sibling_names_do_not_raise(tmp_path):
    """Numeric and textual directory names must not be compared against each other."""
    root = make_dirs(
        tmp_path,
        "lidar_centerpoint/standard/v2",
        "lidar_centerpoint/standard/20260101",
        "lidar_centerpoint/standard/latest_backup",
    )
    axes = [
        ML_AXES[0],
        {"name": "release_version", "values": ["v2", "20260101", "latest_backup"], "sortable": True},
    ]
    resolved = scanner(root, axes=axes).resolve({"release_version": "latest"})
    assert resolved.values["release_version"] in {"v2", "20260101", "latest_backup"}


def test_latest_ranges_over_declared_values_only(tmp_path):
    """A newer directory the entity does not declare support for is not selected."""
    root = make_dirs(tmp_path, "lidar_centerpoint/standard/v2", "lidar_centerpoint/standard/v11")
    resolved = scanner(root).resolve({"release_version": "latest"})
    assert resolved.values["release_version"] == "v2"


def test_hidden_directory_is_not_a_variant_candidate(tmp_path):
    """Tooling state such as ``.cache/`` under an axis position never stands for a value."""
    root = make_dirs(tmp_path, "maps/20260101", "maps/20260601", "maps/.cache")
    axes = [{"name": "date", "values": ["20260101", "20260601"], "sortable": True, "default": "latest"}]
    resolved = DataVariantScanner(root, axes, "maps/$(var date)").resolve()
    assert resolved.values == {"date": "20260601"}


def test_hidden_directory_cannot_be_requested_explicitly(tmp_path):
    root = make_dirs(tmp_path, "maps/20260101", "maps/.cache")
    axes = [{"name": "date", "default": "20260101"}]
    with pytest.raises(DataVariantError, match="does not provide it"):
        DataVariantScanner(root, axes, "maps/$(var date)").resolve({"date": ".cache"})


def test_literal_dot_segment_in_a_pattern_is_honoured(tmp_path):
    root = make_dirs(tmp_path, ".internal/a", ".internal/b")
    axes = [{"name": "slot", "values": ["a", "b"], "default": "a"}]
    resolved = DataVariantScanner(root, axes, ".internal/$(var slot)").resolve({"slot": "b"})
    assert resolved.relative_path == ".internal/b"


def test_requested_version_without_a_directory_is_rejected(ml_root):
    """A missing version must fail, not silently degrade to the parent bundle."""
    with pytest.raises(DataVariantError, match="does not provide it"):
        scanner(ml_root).resolve({"model_variant": "standard", "release_version": "v3"})


def test_requested_model_variant_without_a_directory_is_rejected(ml_root):
    with pytest.raises(DataVariantError, match="does not provide it"):
        scanner(ml_root).resolve({"model_variant": "lite", "release_version": "v1"})


def test_root_level_bundle_resolves_to_root(tmp_path):
    resolved = DataVariantScanner(tmp_path, [], None).resolve()
    assert resolved.relative_path == ""
    assert resolved.path == tmp_path


def test_fixed_segments_are_matched_literally(tmp_path):
    root = make_dirs(tmp_path, "01/sample_sensor_kit", "02/sample_sensor_kit")
    axes = [{"name": "vehicle_id", "values": ["01", "02"], "default": "01"}]
    resolved = DataVariantScanner(root, axes, "$(var vehicle_id)/sample_sensor_kit").resolve({"vehicle_id": "02"})
    assert resolved.relative_path == "02/sample_sensor_kit"


def test_files_under_root_are_not_treated_as_bundles(tmp_path):
    root = make_dirs(tmp_path, "lidar_centerpoint/standard/v1")
    (root / "lidar_centerpoint" / "standard" / "v2").write_text("not a directory")
    resolved = scanner(root).resolve()
    assert resolved.relative_path == "lidar_centerpoint/standard/v1"


def test_explicit_and_defaulted_requests_resolve_identically(ml_root):
    """A default is a request; spelling it out must not change the outcome."""
    defaulted = scanner(ml_root).resolve({"model_variant": "standard"})
    explicit = scanner(ml_root).resolve({"model_variant": "standard", "release_version": "latest"})
    assert defaulted == explicit
    assert defaulted.relative_path == "lidar_centerpoint/standard/v2"


def test_resolved_values_cover_every_declared_axis(ml_root):
    resolved = scanner(ml_root).resolve()
    assert set(resolved.values) == {"model_variant", "release_version"}


# ── unresolvable roots ──────────────────────────────────────────────────────


def test_missing_root_directory_is_an_error(tmp_path):
    with pytest.raises(DataVariantError, match="no bundle directory under"):
        scanner(tmp_path / "absent").resolve({"model_variant": "standard"})


def test_absent_root_is_an_error():
    with pytest.raises(DataVariantError, match="no bundle 'root'"):
        scanner(None).resolve({"model_variant": "standard"})


# ── request validation ──────────────────────────────────────────────────────


def test_unknown_requested_axis_is_rejected(ml_root):
    with pytest.raises(DataVariantError, match="unknown variant axis 'nope'"):
        scanner(ml_root).resolve({"nope": "x"})


def test_value_outside_the_declared_enumeration_is_rejected(ml_root):
    with pytest.raises(DataVariantError, match="not in declared values"):
        scanner(ml_root).resolve({"model_variant": "medium"})


def test_latest_on_a_non_sortable_axis_is_rejected(ml_root):
    with pytest.raises(DataVariantError, match="not sortable"):
        scanner(ml_root).resolve({"model_variant": "latest"})


# ── definition validation (shared with the linter) ───────────────────────────


def test_duplicate_axis_name_is_reported():
    errors = DataVariantScanner(
        None, [{"name": "a", "default": "1"}, {"name": "a", "default": "2"}], "$(var a)"
    ).validate_definition()
    assert any("Duplicate variant axis name 'a'" in e for e in errors)


def test_latest_default_on_a_non_sortable_axis_is_reported():
    errors = DataVariantScanner(None, [{"name": "a", "default": "latest"}], "$(var a)").validate_definition()
    assert any("sortable" in e for e in errors)


def test_sortable_axis_without_values_is_reported():
    errors = DataVariantScanner(None, [{"name": "a", "sortable": True}], "$(var a)").validate_definition()
    assert any("declares no 'values'" in e for e in errors)


def test_pattern_referencing_an_undeclared_variable_is_reported():
    errors = DataVariantScanner(None, [{"name": "a", "default": "1"}], "$(var nope)/$(var a)").validate_definition()
    assert any("unknown variable 'nope'" in e for e in errors)


def test_declared_axis_missing_from_path_pattern_is_reported():
    errors = DataVariantScanner(None, [{"name": "a", "default": "1"}], "fixed").validate_definition()
    assert any("axis 'a' does not appear in path_pattern" in e for e in errors)


def test_axis_used_twice_in_path_pattern_is_reported():
    errors = DataVariantScanner(None, [{"name": "a", "default": "1"}], "$(var a)/x/$(var a)").validate_definition()
    assert any("'a' more than once" in e for e in errors)


def test_legacy_path_patterns_field_is_reported_with_a_migration_hint():
    from autoware_system_designer.schema.yaml_schema import data_semantics

    issues = data_semantics({"variants": [], "path_patterns": ["a", "b"]})
    assert any("declare a single 'path_pattern'" in i.message for i in issues)


def test_invalid_definition_blocks_resolution(tmp_path):
    with pytest.raises(DataVariantError, match="unknown variable"):
        DataVariantScanner(tmp_path, [{"name": "a", "default": "1"}], "$(var nope)/$(var a)").resolve()
