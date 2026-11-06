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

"""Bind the studio and the runtime it reads exports with to this source tree, ahead of any installed copy."""

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for _package_dir in (_REPO / "autoware_system_designer_runtime", _REPO / "autoware_system_designer_studio"):
    sys.path.insert(0, str(_package_dir))
