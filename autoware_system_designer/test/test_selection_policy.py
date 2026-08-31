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
    _UNRANKED,
    EntityGroup,
    SelectionPolicy,
    _path_distance,
)


def _config(file_path: str, package: str = None):
    return SimpleNamespace(file_path=Path(file_path), package=package)


def test_path_distance_counts_steps_over_common_ancestor():
    assert _path_distance(Path("/ws/pkg_a"), Path("/ws/pkg_a/design.yaml")) == 1
    assert _path_distance(Path("/ws/pkg_a"), Path("/ws/pkg_b/design.yaml")) == 3
    assert _path_distance(Path("/ws"), Path("/ws")) == 0


def test_rank_without_anchor_is_unranked():
    policy = SelectionPolicy(anchor_dir=None, preferred_package=None)
    assert policy.rank(_config("/ws/pkg_a/x.yaml")) == (1, _UNRANKED)


def test_rank_prefers_deployment_package_over_proximity():
    policy = SelectionPolicy(anchor_dir=Path("/ws/pkg_b"), preferred_package="pkg_a")
    near = policy.rank(_config("/ws/pkg_b/x.yaml", package="pkg_b"))
    far_preferred = policy.rank(_config("/ws/pkg_a/x.yaml", package="pkg_a"))
    assert far_preferred < near


def test_rank_uses_anchor_proximity_within_a_tier():
    policy = SelectionPolicy(anchor_dir=Path("/ws/pkg_b"), preferred_package=None)
    near = policy.rank(_config("/ws/pkg_b/x.yaml"))
    far = policy.rank(_config("/ws/pkg_a/x.yaml"))
    assert near < far


def test_choose_breaks_ties_by_load_order():
    group = EntityGroup(
        full_name="Demo.node",
        name="Demo",
        entity_type="node",
        candidates=[_config("/ws/pkg_a/x.yaml"), _config("/ws/pkg_b/x.yaml")],
    )
    picked = group.choose(SelectionPolicy())
    assert str(picked.file_path) == "/ws/pkg_a/x.yaml"
