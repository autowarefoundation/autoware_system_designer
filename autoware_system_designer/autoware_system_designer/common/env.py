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

"""Environment variable parsing shared across the package."""

import os

# Accepted set is mirrored by the stdlib-only script/system_designer_runner.py.
_TRUTHY_VALUES = {"1", "true", "on", "yes", "y"}


def env_flag(name: str, default: bool) -> bool:
    """Boolean environment variable; any value outside 1/true/on/yes/y (case-insensitive) is false."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in _TRUTHY_VALUES
