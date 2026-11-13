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

"""Vocabulary of the runtime's control surfaces: the stdin console and the control channel.

Plain Python with no ROS import: the studio names the same actions, verbs and policies.
"""

from __future__ import annotations

# Actions on the members matching a name.
MEMBER_ACTIONS = ("stop", "restart", "kill")
# Verbs of the latency measurement window.
MEASURE_VERBS = ("start", "stop", "status")
# Safe-state interlock of the actions that end a process; the first is the default.
POLICIES = ("guard", "open")
