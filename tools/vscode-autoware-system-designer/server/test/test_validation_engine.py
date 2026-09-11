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

"""Connection diagnostics of the language server, pinned to the designer's link rules."""

import textwrap

import pytest
from lsprotocol import types as lsp
from registry_manager import RegistryManager
from validation_engine import ValidationEngine

TALKER = """
autoware_system_design_format: 0.4.0
name: Talker.node
package:
  name: demo_pkg
  provider: autoware
launch:
  executable: talker
subscribers: []
clients: []
publishers:
  - name: chatter
    message_type: std_msgs/msg/String
  - name: status
    message_type: std_msgs/msg/String
servers:
  - name: reset
    message_type: std_srvs/srv/Trigger
param_files: []
param_values: []
processes: []
"""

LISTENER = """
autoware_system_design_format: 0.4.0
name: Listener.node
package:
  name: demo_pkg
  provider: autoware
launch:
  executable: listener
publishers: []
servers: []
subscribers:
  - name: chatter
    message_type: std_msgs/msg/Int32
  - name: status
    message_type: std_msgs/msg/String
clients:
  - name: reset
    message_type: std_srvs/srv/Trigger
param_files: []
param_values: []
processes: []
"""

CHAIN_HEADER = """
autoware_system_design_format: 0.4.0
name: Chain.module
instances:
  - name: talker
    entity: Talker.node
  - name: listener
    entity: Listener.node
subscribers:
  - name: external_in
publishers:
  - name: external_out
connections:
"""


@pytest.fixture
def workspace(tmp_path):
    """A module with one talker and one listener, ready for connection cases."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "Talker.node.yaml").write_text(TALKER.lstrip())
    (package / "Listener.node.yaml").write_text(LISTENER.lstrip())

    registry = RegistryManager()
    registry.scan_workspace(f"file://{tmp_path}")
    return package, registry


def diagnose(workspace, connections: str):
    """Validate a Chain.module holding *connections* and return its diagnostics."""
    package, registry = workspace
    content = CHAIN_HEADER.lstrip() + textwrap.dedent(connections).lstrip("\n")
    path = package / "Chain.module.yaml"
    path.write_text(content)

    config = registry.config_parser.parse_entity_from_content(content, str(path))
    return ValidationEngine(registry).validate_connections(config, content)


def messages(diagnostics):
    return [diagnostic.message for diagnostic in diagnostics]


def test_matching_ports_produce_no_diagnostics(workspace):
    assert (
        diagnose(
            workspace,
            """
            - - talker.publisher.status
              - listener.subscriber.status
            - - talker.server.reset
              - listener.client.reset
            """,
        )
        == []
    )


def test_external_declaration_is_required(workspace):
    diagnostics = diagnose(
        workspace,
        """
        - - subscriber.missing_ext
          - listener.subscriber.status
        """,
    )
    assert len(diagnostics) == 1
    assert "External input 'missing_ext' is not declared" in diagnostics[0].message


def test_unknown_instance_is_reported_on_its_own_side(workspace):
    diagnostics = diagnose(
        workspace,
        """
        - - talker.publisher.status
          - ghost.subscriber.status
        """,
    )
    assert len(diagnostics) == 1
    assert "Instance 'ghost' not found" in diagnostics[0].message
    # The offending reference sits on the second line of the connection entry.
    assert diagnostics[0].range.start.line == 13


def test_unknown_port_lists_the_available_ones(workspace):
    diagnostics = diagnose(
        workspace,
        """
        - - talker.publisher.nope
          - listener.subscriber.status
        """,
    )
    assert len(diagnostics) == 1
    assert "Output port 'nope' not found in instance 'talker'" in diagnostics[0].message
    assert "chatter" in diagnostics[0].message


@pytest.mark.parametrize("wildcard", ["*", "^", "+"])
def test_wildcards_resolve_against_the_port_key_space(workspace, wildcard):
    assert (
        diagnose(
            workspace,
            f"""
            - - talker.publisher.{wildcard}
              - listener.subscriber.{wildcard}
            """,
        )
        == []
    )


def test_wildcard_without_any_match_is_reported(workspace):
    diagnostics = diagnose(
        workspace,
        """
        - - talker.publisher.zz*
          - listener.subscriber.zz*
        """,
    )
    assert len(diagnostics) == 1
    assert "No ports match the wildcard connection" in diagnostics[0].message


def test_instance_wildcard_matches_every_instance(workspace):
    assert (
        diagnose(
            workspace,
            """
            - - subscriber.external_in
              - ^.subscriber.status
            """,
        )
        == []
    )


def test_mismatched_port_roles_are_rejected(workspace):
    diagnostics = diagnose(
        workspace,
        """
        - - talker.publisher.status
          - listener.publisher.status
        """,
    )
    assert len(diagnostics) == 1
    assert "Invalid internal connection type" in diagnostics[0].message


def test_message_type_mismatch_is_a_warning(workspace):
    diagnostics = diagnose(
        workspace,
        """
        - - talker.publisher.chatter
          - listener.subscriber.chatter
        """,
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].severity == lsp.DiagnosticSeverity.Warning
    assert "std_msgs/msg/String" in diagnostics[0].message


def test_service_connections_pair_server_with_client(workspace):
    diagnostics = diagnose(
        workspace,
        """
        - - talker.server.reset
          - listener.subscriber.reset
        """,
    )
    assert len(diagnostics) == 1
    assert "Invalid internal connection type" in diagnostics[0].message
