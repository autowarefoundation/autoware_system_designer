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

"""Diagnostics published for documents the designer parser rejects."""

import pytest
from document_processor import DocumentProcessor
from registry_manager import RegistryManager

from autoware_system_designer.parser.data_parser import ConfigParser


class RecordingServer:
    """Stands in for the pygls server, keeping the last published diagnostics."""

    def __init__(self):
        self.diagnostics = []

    def publish_diagnostics(self, uri, diagnostics):
        self.diagnostics = diagnostics


@pytest.fixture
def processor():
    return DocumentProcessor(ConfigParser(strict_mode=False), RegistryManager())


def publish(processor, tmp_path, file_name, content):
    path = tmp_path / file_name
    server = RecordingServer()
    processor.process_document(f"file://{path}", content, server)
    return server.diagnostics


def test_schema_violation_is_anchored_at_its_yaml_path(processor, tmp_path):
    content = "\n".join(
        [
            "autoware_system_design_format: 0.4.0",
            "name: Broken.node",
            "package:",
            "  name: demo_pkg",
            "launch:",
            "  executable: broken",
            "subscribers: []",
            "publishers: []",
            "param_files: []",
            "param_values: []",
            "processes: []",
            "",
        ]
    )
    diagnostics = publish(processor, tmp_path, "Broken.node.yaml", content)

    assert len(diagnostics) == 1
    assert "'provider' is a required property" in diagnostics[0].message
    # The 'package' mapping starts on the fourth line.
    assert diagnostics[0].range.start.line == 3


def test_incompatible_format_version_is_reported(processor, tmp_path):
    content = "autoware_system_design_format: 9.0.0\nname: Future.node\n"
    diagnostics = publish(processor, tmp_path, "Future.node.yaml", content)

    assert any("Incompatible format version" in diagnostic.message for diagnostic in diagnostics)


def test_yaml_syntax_error_suppresses_parser_noise(processor, tmp_path):
    diagnostics = publish(processor, tmp_path, "Bad.node.yaml", "name: Bad.node\nsubscribers: [\n")

    assert len(diagnostics) == 1
    assert diagnostics[0].message.startswith("YAML syntax error")
