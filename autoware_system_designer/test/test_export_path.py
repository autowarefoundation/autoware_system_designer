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

import os

import pytest

from autoware_system_designer.builder.export.json_io import iter_mode_data
from autoware_system_designer.common.exceptions import ValidationError
from autoware_system_designer.common.export_layout import ExportLayout
from autoware_system_designer.common.path_utils import export_path, validate_path_segment


def test_identifier_like_segments_are_joined():
    assert export_path("/out", "Deploy_1", "main-ecu", "x.launch.xml") == os.path.join(
        "/out", "Deploy_1", "main-ecu", "x.launch.xml"
    )
    assert validate_path_segment("TypeAlpha.parameter_set") == "TypeAlpha.parameter_set"


@pytest.mark.parametrize("segment", ["..", ".", "", "a/b", "a\\b", "/abs", "a\nb", "a\n", ".hidden", "-x", None, 3])
def test_segments_that_leave_or_hide_in_the_tree_are_rejected(segment):
    with pytest.raises(ValidationError):
        export_path("/out", "ok", segment)


def test_mode_structure_is_read_from_inside_the_export_tree(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    with pytest.raises(ValidationError):
        list(iter_mode_data(["../outside"], str(tmp_path / "structure")))


def test_export_layout_validates_system_name():
    assert ExportLayout("/out", "TypeAlpha").exports_dir.endswith(os.path.join("exports", "TypeAlpha"))
    with pytest.raises(ValidationError):
        ExportLayout("/out", "../escape")
