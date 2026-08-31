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

"""Directory contract of one exported system under <output_root>/exports/<name>/."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ExportLayout:
    """Path scheme shared by the build, the generators, and export consumers."""

    output_root_dir: str
    system_name: str

    @property
    def exports_dir(self) -> str:
        return os.path.join(self.output_root_dir, "exports", self.system_name)

    @property
    def launcher_dir(self) -> str:
        return os.path.join(self.exports_dir, "launcher/")

    @property
    def system_monitor_dir(self) -> str:
        return os.path.join(self.exports_dir, "system_monitor/")

    @property
    def visualization_dir(self) -> str:
        return os.path.join(self.exports_dir, "visualization/")

    @property
    def parameter_set_dir(self) -> str:
        return os.path.join(self.exports_dir, "parameter_set/")

    @property
    def system_structure_dir(self) -> str:
        return os.path.join(self.exports_dir, "system_structure/")
