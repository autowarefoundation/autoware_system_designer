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

"""Path setup shared by the language server tests."""

import os
import sys

_SERVER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BUNDLED = os.path.join(_SERVER_DIR, "bundled")
_DEV = os.path.normpath(os.path.join(_SERVER_DIR, "..", "..", "..", "autoware_system_designer"))

# The designer package resolves the same way the language server resolves it.
for _package_root in (_DEV, _BUNDLED):
    if os.path.isdir(_package_root):
        if _package_root not in sys.path:
            sys.path.insert(0, _package_root)
        break

if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)
