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

import json

import pytest

from autoware_system_designer.builder.resolution.bundle_version import (
    BundleVersionError,
    VersionRequirement,
    parse_bundle_version,
    read_bundle_version,
    read_manifest_paths,
)

# ── parsing ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw, major, minor",
    [("v4.1", 4, 1), ("4.1", 4, 1), ("5", 5, 0), (5, 5, 0), ("v5.0.1", 5, 0), ("v4", 4, 0)],
)
def test_parse_accepts_major_minor_forms(raw, major, minor):
    version = parse_bundle_version(raw)
    assert (version.major, version.minor) == (major, minor)


@pytest.mark.parametrize("raw", ["", None, "latest", "v4.x", "4.1.0.0", True])
def test_parse_rejects_non_versions(raw):
    with pytest.raises(BundleVersionError):
        parse_bundle_version(raw)


# ── requirement ──────────────────────────────────────────────────────────────

CENTERPOINT = VersionRequirement(major=4, min_minor=1)


# Same table as autoware_lidar_centerpoint/test/test_ml_package_version.cpp
# (supportedVersions / unsupportedVersions), so the two checkers can be diffed by eye.
@pytest.mark.parametrize("raw", ["v4.1", "4.1", "v4.2", "v4.10"])
def test_centerpoint_supported_versions(raw):
    assert CENTERPOINT.satisfied_by(parse_bundle_version(raw))


@pytest.mark.parametrize("raw", ["v4.0", "v3.9", "v5.0"])
def test_centerpoint_unsupported_versions(raw):
    assert not CENTERPOINT.satisfied_by(parse_bundle_version(raw))


def test_requirement_without_min_minor_accepts_any_minor():
    requirement = VersionRequirement.from_config({"major": 5})
    assert requirement.satisfied_by(parse_bundle_version("v5.0"))
    assert requirement.satisfied_by(parse_bundle_version("v5.7"))
    assert not requirement.satisfied_by(parse_bundle_version("v4.9"))


@pytest.mark.parametrize(
    "spec", [None, {}, {"major": "4"}, {"major": 4, "min_minor": -1}, {"major": 4, "min_minor": "1"}]
)
def test_malformed_requirement_is_rejected(spec):
    with pytest.raises(BundleVersionError):
        VersionRequirement.from_config(spec)


# ── reading from a bundle ────────────────────────────────────────────────────


def test_read_default_deploy_metadata(tmp_path):
    (tmp_path / "deploy_metadata.yaml").write_text("version: v5.0\n")
    version, source = read_bundle_version(tmp_path, None)
    assert (version.major, version.minor, source) == (5, 0, "deploy_metadata.yaml:version")


def test_read_nested_yaml_key_path(tmp_path):
    (tmp_path / "ml_package.param.yaml").write_text("/**:\n  ros__parameters:\n    version: v4.1\n")
    spec = {"file": "ml_package.param.yaml", "version_key": ["/**", "ros__parameters", "version"]}
    version, source = read_bundle_version(tmp_path, spec)
    assert (version.major, version.minor) == (4, 1)
    assert source == "ml_package.param.yaml:/**.ros__parameters.version"


def test_read_flat_yaml_key(tmp_path):
    (tmp_path / "meta.yaml").write_text("release: 3.2\n")
    version, _ = read_bundle_version(tmp_path, {"file": "meta.yaml", "version_key": "release"})
    assert (version.major, version.minor) == (3, 2)


def test_read_json_integer_value(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps({"bundle": {"version": 7}}))
    version, _ = read_bundle_version(tmp_path, {"file": "meta.json", "version_key": ["bundle", "version"]})
    assert (version.major, version.minor) == (7, 0)


def test_read_missing_file_is_an_error(tmp_path):
    with pytest.raises(BundleVersionError, match="is missing from bundle"):
        read_bundle_version(tmp_path, None)


def test_read_missing_key_is_an_error(tmp_path):
    (tmp_path / "deploy_metadata.yaml").write_text("name: x\n")
    with pytest.raises(BundleVersionError, match="key 'version' not found"):
        read_bundle_version(tmp_path, None)


def test_read_unparseable_value_names_its_source(tmp_path):
    (tmp_path / "deploy_metadata.yaml").write_text("version: latest\n")
    with pytest.raises(BundleVersionError, match="'latest'.*deploy_metadata.yaml:version"):
        read_bundle_version(tmp_path, None)


def test_read_rejects_unknown_file_type(tmp_path):
    (tmp_path / "meta.txt").write_text("version: 1\n")
    with pytest.raises(BundleVersionError, match="must be YAML or JSON"):
        read_bundle_version(tmp_path, {"file": "meta.txt"})


# ── manifest path keys (a consumer's requires_paths) ─────────────────────────

ML_PACKAGE_SPEC = {
    "file": "ml_package.param.yaml",
    "version_key": ["/**", "ros__parameters", "version"],
}
REQUIRED = ["encoder_onnx_path", "head_onnx_path"]


def _write_ml_package(tmp_path, **entries):
    lines = "".join(f"    {key}: {value}\n" for key, value in entries.items())
    (tmp_path / "ml_package.param.yaml").write_text(f"/**:\n  ros__parameters:\n    version: v4.1\n{lines}")


def test_manifest_paths_resolve_beside_the_version_key(tmp_path):
    _write_ml_package(tmp_path, encoder_onnx_path="encoder.onnx", head_onnx_path="head.onnx")
    (tmp_path / "encoder.onnx").write_text("")
    (tmp_path / "head.onnx").write_text("")
    resolved = read_manifest_paths(tmp_path, ML_PACKAGE_SPEC, REQUIRED)
    assert resolved == [
        ("encoder_onnx_path", str(tmp_path / "encoder.onnx")),
        ("head_onnx_path", str(tmp_path / "head.onnx")),
    ]


def test_manifest_paths_resolve_flat_keys_by_default(tmp_path):
    (tmp_path / "deploy_metadata.yaml").write_text("version: v5.0\nonnx_path: model.onnx\n")
    (tmp_path / "model.onnx").write_text("")
    resolved = read_manifest_paths(tmp_path, None, ["onnx_path"])
    assert resolved == [("onnx_path", str(tmp_path / "model.onnx"))]


def test_no_required_paths_checks_nothing(tmp_path):
    assert read_manifest_paths(tmp_path, ML_PACKAGE_SPEC, None) == []
    assert read_manifest_paths(tmp_path, ML_PACKAGE_SPEC, []) == []


def test_required_path_missing_from_the_manifest_is_an_error(tmp_path):
    _write_ml_package(tmp_path, encoder_onnx_path="encoder.onnx")
    (tmp_path / "encoder.onnx").write_text("")
    with pytest.raises(BundleVersionError, match="path key 'head_onnx_path' not found"):
        read_manifest_paths(tmp_path, ML_PACKAGE_SPEC, REQUIRED)


def test_required_path_naming_a_missing_file_is_an_error(tmp_path):
    _write_ml_package(tmp_path, encoder_onnx_path="encoder.onnx", head_onnx_path="head.onnx")
    (tmp_path / "encoder.onnx").write_text("")
    with pytest.raises(BundleVersionError, match="'head_onnx_path: head.onnx' names a file missing"):
        read_manifest_paths(tmp_path, ML_PACKAGE_SPEC, REQUIRED)


def test_required_path_value_must_be_a_file_name(tmp_path):
    _write_ml_package(tmp_path, encoder_onnx_path="[a, b]", head_onnx_path="head.onnx")
    (tmp_path / "head.onnx").write_text("")
    with pytest.raises(BundleVersionError, match="must be a file name"):
        read_manifest_paths(tmp_path, ML_PACKAGE_SPEC, REQUIRED)


def test_required_paths_require_the_manifest_file(tmp_path):
    with pytest.raises(BundleVersionError, match="is missing from bundle"):
        read_manifest_paths(tmp_path, ML_PACKAGE_SPEC, REQUIRED)
