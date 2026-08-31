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

from pathlib import Path
from types import SimpleNamespace

from autoware_system_designer.builder.config.config_registry import (
    EntityGroup,
    SelectionPolicy,
    format_duplicate_report,
)


def _group():
    return EntityGroup(
        full_name="Demo.node",
        name="Demo",
        entity_type="node",
        candidates=[
            SimpleNamespace(file_path=Path("/ws/pkg_a/x.yaml"), package="pkg_a"),
            SimpleNamespace(file_path=Path("/ws/pkg_b/x.yaml"), package="pkg_b"),
        ],
    )


def test_choose_does_not_memoize_or_mark_used():
    group = _group()
    group.choose(SelectionPolicy())
    assert not group.used
    assert group.selected is None
    picked = group.choose(SelectionPolicy(anchor_dir=Path("/ws/pkg_b")))
    assert str(picked.file_path) == "/ws/pkg_b/x.yaml"


def test_resolve_memoizes_and_flags_first_use_once():
    group = _group()
    first, first_use = group.resolve(SelectionPolicy())
    assert first_use and group.used
    again, first_use = group.resolve(SelectionPolicy(anchor_dir=Path("/ws/pkg_b")))
    assert not first_use
    assert again is first
    assert group.choose(SelectionPolicy(anchor_dir=Path("/ws/pkg_b"))) is first


def test_describe_marks_only_the_memoized_selection():
    group = _group()
    assert "->" not in group.describe()
    group.resolve(SelectionPolicy())
    report = format_duplicate_report([group])
    marked = [line for line in report.splitlines() if line.lstrip().startswith("->")]
    assert len(marked) == 1
    assert "pkg_a" in marked[0]
