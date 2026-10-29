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

"""Registry updates driven by file-watcher events read the file as it is on disk."""

from pathlib import Path

from registry_manager import RegistryManager

from autoware_system_designer.parser.data_parser import ConfigParser

NODE = "\n".join(
    [
        "autoware_system_design_format: 0.4.0",
        "name: Talker.node",
        "package:",
        "  name: demo_pkg",
        "  provider: demo",
        "launch:",
        "  executable: {executable}",
        "subscribers: []",
        "publishers:",
        "  - name: {port}",
        "    message_type: std_msgs/msg/String",
        "param_files: []",
        "param_values: []",
        "processes: []",
        "",
    ]
)


def test_reparse_after_disk_change_sees_new_content(tmp_path):
    path = tmp_path / "Talker.node.yaml"
    path.write_text(NODE.format(executable="talker", port="out"))
    parser = ConfigParser(strict_mode=False)
    registry = RegistryManager()

    registry.register_entity(parser.parse_entity_file(str(path)))
    path.write_text(NODE.format(executable="talker2", port="renamed"))
    registry.register_entity(parser.parse_entity_file(str(path)))

    config = registry.get_entity("Talker.node")
    assert config.launch["executable"] == "talker2"
    assert [p.name for p in config.outputs] == ["renamed"]
