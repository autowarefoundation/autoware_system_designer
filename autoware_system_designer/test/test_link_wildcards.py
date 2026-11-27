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

"""Kind-aware connection and wildcard resolution tests."""

import pytest
from pipeline_harness import run_pipeline, stage_case

from autoware_system_designer.builder.graph.link_manager import (
    LinkManager,
    _PortInfo,
    match_and_pair_wildcard_ports,
)
from autoware_system_designer.common.exceptions import SystemDesignerError
from autoware_system_designer.model.links import Connection


def _info(name: str, role: str, external: bool = False) -> _PortInfo:
    instance = None if external else object()
    return _PortInfo(port_name=name, instance=instance, port=None, role=role)


# ---------------------------------------------------------------------------
# Connection parsing
# ---------------------------------------------------------------------------


def test_connection_stores_declared_kinds():
    conn = Connection(["a.publisher.foo", "b.subscriber.foo"])
    assert (conn.from_port_type, conn.to_port_type) == ("publisher", "subscriber")

    conn = Connection(["b.client.bar", "a.server.bar"])
    assert (conn.from_instance, conn.from_port_type) == ("a", "server")
    assert (conn.to_instance, conn.to_port_type) == ("b", "client")

    conn = Connection(["a.server.baz", "server.baz"])  # boundary export
    assert conn.to_is_external
    assert (conn.from_port_type, conn.to_port_type) == ("server", "server")


def test_connection_rejects_invalid_kind_token():
    with pytest.raises(SystemDesignerError) as excinfo:
        Connection(["a.output.foo", "b.subscriber.foo"])
    assert "E_CONN_PORT_KIND" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Candidate filtering
# ---------------------------------------------------------------------------


def test_filter_candidates_by_role():
    ports = {
        "a.status": _info("status", "publisher"),
        "a.set_mode": _info("set_mode", "server"),
    }
    assert set(LinkManager._filter_candidates(ports, "publisher", external=False)) == {"a.status"}
    assert set(LinkManager._filter_candidates(ports, "server", external=False)) == {"a.set_mode"}


def test_filter_candidates_by_side():
    ports = {
        "a.status": _info("status", "publisher"),
        ".status": _info("status", "subscriber", external=True),
    }
    assert set(LinkManager._filter_candidates(ports, "publisher", external=False)) == {"a.status"}
    assert set(LinkManager._filter_candidates(ports, "subscriber", external=True)) == {".status"}
    # boundary entries never match an internal-side pattern even when kind-compatible
    assert LinkManager._filter_candidates(ports, "subscriber", external=False) == {}


def test_filter_candidates_keeps_roleless_ports():
    ports = {"a.status": _info("status", None)}
    assert set(LinkManager._filter_candidates(ports, "publisher", external=False)) == {"a.status"}


# ---------------------------------------------------------------------------
# Wildcard pairing over filtered candidates
# ---------------------------------------------------------------------------


def test_wildcard_substitution_across_slashes():
    sources = {"localization.state": _info("state", "publisher")}
    targets = {".api/localization/state": _info("api/localization/state", "publisher", external=True)}
    pairs = match_and_pair_wildcard_ports("localization.^", ".api/localization/^", sources, targets)
    assert pairs == [("localization.state", ".api/localization/state")]


def test_one_sided_wildcard_skips_the_port_of_the_other_side():
    sources = {"a.diag": _info("diag", "publisher"), "aggregator.diag": _info("diag", "publisher")}
    targets = {"aggregator.diag": _info("diag", "subscriber")}
    pairs = match_and_pair_wildcard_ports("*.diag", "aggregator.diag", sources, targets)
    assert pairs == [("a.diag", "aggregator.diag")]


def test_wildcard_pairing_per_kind():
    sources = {
        "provider.status": _info("status", "publisher"),
        "provider.set_mode": _info("set_mode", "server"),
    }
    targets = {
        ".status": _info("status", "publisher", external=True),
        ".set_mode": _info("set_mode", "server", external=True),
    }
    pub_pairs = match_and_pair_wildcard_ports(
        "provider.^",
        ".^",
        LinkManager._filter_candidates(sources, "publisher", external=False),
        LinkManager._filter_candidates(targets, "publisher", external=True),
    )
    assert pub_pairs == [("provider.status", ".status")]
    srv_pairs = match_and_pair_wildcard_ports(
        "provider.^",
        ".^",
        LinkManager._filter_candidates(sources, "server", external=False),
        LinkManager._filter_candidates(targets, "server", external=True),
    )
    assert srv_pairs == [("provider.set_mode", ".set_mode")]


# ---------------------------------------------------------------------------
# End-to-end fixture cases
# ---------------------------------------------------------------------------


def test_parallel_kind_wildcards_build(tmp_path):
    """Publisher and server wildcard exports coexist, each matching only its kind."""
    workspace = stage_case("module_services", tmp_path)
    run = run_pipeline(workspace, "services_pkg/Hub.system.yaml", tmp_path)
    structure = run.structure("default")
    api = next(child for child in structure["data"]["children"] if child["name"] == "api")
    assert sorted(p["name"] for p in api["out_ports"]) == ["reset", "set_mode", "status"]
    provider = next(child for child in api["children"] if child["name"] == "provider")
    assert sorted(p["name"] for p in provider["out_ports"]) == ["reset", "set_mode", "status"]


def test_kind_mismatch_fails(tmp_path):
    workspace = stage_case("errors_port_kind", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "kind_pkg/Mismatch.system.yaml", tmp_path)
    assert "E_PORT_KIND" in str(excinfo.value)


def test_duplicate_connection_still_fails(tmp_path):
    workspace = stage_case("errors_port_kind", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "kind_pkg/Dup.system.yaml", tmp_path)
    assert "E_DUPLICATE_CONNECTION" in str(excinfo.value)


def test_wrong_kind_wildcard_is_empty(tmp_path):
    workspace = stage_case("errors_port_kind", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "kind_pkg/EmptyWildcard.system.yaml", tmp_path)
    assert "E_WILDCARD_EMPTY" in str(excinfo.value)


def test_remap_kind_mismatch_fails(tmp_path):
    workspace = stage_case("errors_port_kind", tmp_path)
    with pytest.raises(SystemDesignerError) as excinfo:
        run_pipeline(workspace, "kind_pkg/RemapKind.system.yaml", tmp_path)
    assert "E_REMAP_PORT_TYPE" in str(excinfo.value)
