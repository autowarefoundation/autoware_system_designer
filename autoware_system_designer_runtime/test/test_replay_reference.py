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
from pathlib import Path

import pytest

from autoware_system_designer_runtime._impl.replay import reference as ref
from autoware_system_designer_runtime.replay import ReferenceSaveError, _save_reference


def _run_outputs(tmp_path, name, content):
    out_dir = tmp_path / name
    (out_dir / ref.OUTPUTS_DIR).mkdir(parents=True)
    (out_dir / ref.OUTPUTS_DIR / "data").write_text(content)
    return out_dir


def test_a_saved_reference_holds_the_outputs_and_the_key(tmp_path):
    reference_dir = tmp_path / "records" / "key"

    _save_reference(_run_outputs(tmp_path, "run", "first"), reference_dir, {"scope": ["/a"]})

    assert (reference_dir / ref.OUTPUTS_DIR / "data").read_text() == "first"
    assert json.loads((reference_dir / ref.REFERENCE_FILE).read_text())["key"] == {"scope": ["/a"]}


def test_a_saved_reference_replaces_the_one_in_its_place(tmp_path):
    reference_dir = tmp_path / "records" / "key"
    _save_reference(_run_outputs(tmp_path, "run1", "first"), reference_dir, {})
    (reference_dir / "stale").write_text("")

    _save_reference(_run_outputs(tmp_path, "run2", "second"), reference_dir, {})

    assert (reference_dir / ref.OUTPUTS_DIR / "data").read_text() == "second"
    assert sorted(p.name for p in reference_dir.parent.iterdir()) == ["key"]
    assert not (reference_dir / "stale").exists()


def test_a_directory_that_is_not_a_reference_is_not_replaced(tmp_path):
    reference_dir = tmp_path / "project"
    reference_dir.mkdir()
    (reference_dir / "keep.txt").write_text("mine")

    with pytest.raises(ReferenceSaveError):
        _save_reference(_run_outputs(tmp_path, "run", "first"), reference_dir, {})

    assert (reference_dir / "keep.txt").read_text() == "mine"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["project", "run"]


def test_a_sibling_that_is_not_a_reference_is_not_removed(tmp_path):
    reference_dir = tmp_path / "key"
    retired = tmp_path / "key.old"
    retired.mkdir()
    (retired / "keep.txt").write_text("mine")

    with pytest.raises(ReferenceSaveError):
        _save_reference(_run_outputs(tmp_path, "run", "first"), reference_dir, {})

    assert (retired / "keep.txt").read_text() == "mine"
    assert not reference_dir.exists()


def test_a_file_system_root_is_not_a_reference(tmp_path):
    with pytest.raises(ReferenceSaveError):
        _save_reference(_run_outputs(tmp_path, "run", "first"), Path("/"), {})
