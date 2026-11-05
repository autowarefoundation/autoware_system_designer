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

"""The serve daemon serves the studio site drawn from the exports, the web bundle and the request API."""

import asyncio
import http.client
import json
import socket
import sys
import threading

import pytest

from autoware_system_designer_studio import exports
from autoware_system_designer_studio.serve import build, jobs
from autoware_system_designer_studio.serve.server import CLIENT_HEADER, ServeDaemon

PKG = "demo_deployment"
SYSTEM = "Demo"


def _install_tree(root, *, compute_units):
    """An isolated install tree with one export and an unrelated package file."""
    install = root / "install"
    export = install / PKG / "share" / PKG / "exports" / SYSTEM
    (export / "system_structure").mkdir(parents=True)
    structure = {"schema_version": "1.2", "metadata": {"compute_units": compute_units}, "data": {"path": "/"}}
    (export / "system_structure" / "Runtime.json").write_text(json.dumps(structure))
    (export / "launcher" / "Runtime" / "main_ecu").mkdir(parents=True)
    (export / "launcher" / "Runtime" / "main_ecu" / "main_ecu.launch.xml").write_text("<launch/>")
    manifest = {
        "schema_version": "1.2",
        "default_mode": "Runtime",
        "mode_keys": ["Runtime", "Simulation"],
        "compute_units": compute_units,
        "deploy_variants": [{"name": "car_a", "arguments": [{"name": "vehicle_id", "value": "a"}]}],
    }
    (export / "deployment.json").write_text(json.dumps(manifest))
    (install / "setup.bash").write_text("secret")
    (root / "outside.txt").write_text("outside")
    return install


UNITS = [
    {"name": "main_ecu", "kind": "onboard", "description": "main", "future_field": 1},
    {"name": "sub_ecu", "kind": "onboard", "description": "sub"},
    {"name": "infra", "kind": "offboard", "description": "roadside"},
]


class _Running:
    """A daemon on its own event loop thread, reachable through http.client."""

    def __init__(self, install, **kwargs):
        self.loop = asyncio.new_event_loop()
        self.daemon = ServeDaemon(install, port=0, **kwargs)
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.port = asyncio.run_coroutine_threadsafe(self.daemon.start(), self.loop).result(5)

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response, payload

    def json(self, method, path, body=None, headers=None):
        response, payload = self.request(method, path, body, headers)
        return response.status, json.loads(payload)

    def rpc(self, envelope, headers=None):
        base = {"Content-Type": "application/json", CLIENT_HEADER: "test"}
        return self.json("POST", "/api/rpc", json.dumps(envelope), {**base, **(headers or {})})

    def close(self):
        asyncio.run_coroutine_threadsafe(self.daemon.stop(), self.loop).result(5)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()


@pytest.fixture
def served(tmp_path):
    running = _Running(
        _install_tree(tmp_path, compute_units=UNITS),
        served_units=["main_ecu"],
        record_root=tmp_path / "records",
        heartbeat_s=0.05,
    )
    yield running
    running.close()


SITE = f"/systems/{PKG}/{SYSTEM}"


def test_root_is_the_systems_index(served):
    response, payload = served.request("GET", "/")
    assert response.status == 200
    assert f"{SITE[1:]}/index.html".encode() in payload


@pytest.mark.parametrize(
    "path, content_type",
    [
        ("/index.html", "text/html; charset=utf-8"),
        ("/assets/js/core/sd.js", "text/javascript; charset=utf-8"),
        ("/assets/css/tokens.css", "text/css; charset=utf-8"),
        (f"{SITE}/index.html", "text/html; charset=utf-8"),
        (f"{SITE}/launch.html", "text/html; charset=utf-8"),
        (f"{SITE}/config.js", "text/javascript; charset=utf-8"),
        (f"{SITE}/data/Runtime.js", "text/javascript; charset=utf-8"),
    ],
)
def test_site_and_bundle_are_served(served, path, content_type):
    response, payload = served.request("GET", path)
    assert response.status == 200
    assert response.getheader("Content-Type") == content_type
    assert response.getheader("X-Content-Type-Options") == "nosniff"
    assert payload


@pytest.mark.parametrize(
    "path",
    [
        "/setup.bash",
        f"/{PKG}/share/{PKG}/exports/{SYSTEM}/deployment.json",
        f"/{PKG}/share/{PKG}/exports/{SYSTEM}/system_structure/Runtime.json",
        f"{SITE}/../../deployment.json",
        f"{SITE}/%2e%2e/%2e%2e/deployment.json",
        "/../outside.txt",
        f"{SITE}/data/Simulation.js",
        f"{SITE}/data/Runtime_latency.js",
        f"{SITE}/data/Unknown.js",
        "/assets/",
        "/assets/daemon/build.html",
        "/assets/../setup.bash",
        "/assets/js/../../pages/render.py",
        "/assets/.hidden",
    ],
)
def test_paths_outside_the_site_are_not_found(served, path):
    status, body = served.json("GET", path)
    assert status == 404
    assert body["ok"] is False and body["error"]["code"] == "not_found"


def test_directory_addresses_serve_their_index(served):
    response, payload = served.request("GET", f"{SITE}/")
    assert response.status == 200 and b"overview_page.js" in payload
    response, _ = served.request("GET", SITE)
    assert response.status == 302 and response.getheader("Location") == f"{SITE}/"


def test_workbench_links_the_launch_page_the_site_serves(served):
    _, config = served.request("GET", f"{SITE}/config.js")
    assert b'launchCommandsPath: "launch.html"' in config
    assert served.request("GET", f"{SITE}/launch.html")[0].status == 200


def test_missing_page_answers_with_an_html_page(served):
    response, payload = served.request("GET", f"/systems/other/{SYSTEM}/index.html")
    assert response.status == 404
    assert response.getheader("Content-Type") == "text/html; charset=utf-8"
    assert b"Page not found" in payload and b'href="/"' in payload
    assert b"refresh" not in payload
    assert served.request("GET", "/systems.html")[0].status == 404


def test_page_of_an_export_mid_rewrite_reloads_until_readable(served):
    structure = served.daemon.install_root / PKG / "share" / PKG / "exports" / SYSTEM / "system_structure" / "Runtime.json"
    complete = structure.read_text()
    structure.write_text(complete[: len(complete) // 2])

    status, body = served.json("GET", f"{SITE}/data/Runtime.js")
    assert status == 503 and body["error"]["code"] == "unavailable"

    manifest = structure.parent.parent / "deployment.json"
    saved = manifest.read_text()
    manifest.write_text("{")
    response, payload = served.request("GET", f"{SITE}/index.html")
    assert response.status == 404 and b"Page not found" in payload

    manifest.write_text(saved)
    structure.write_text(complete)
    assert served.request("GET", f"{SITE}/data/Runtime.js")[0].status == 200


def test_latency_script_serves_the_newest_record(served, tmp_path):
    records = tmp_path / "records" / PKG / SYSTEM / "latency" / "Runtime"
    records.mkdir(parents=True)
    for stamp, window in (("20261104-090000", 1.0), ("20261105-090000", 2.0)):
        record = {"schema": "autoware_system_designer/latency/2", "run": {"window_s": window}}
        (records / f"{stamp}.json").write_text(json.dumps(record))
    response, payload = served.request("GET", f"{SITE}/data/Runtime_latency.js")
    assert response.status == 200
    assert b'"window_s": 2.0' in payload


def test_foreign_host_header_is_refused(served):
    status, body = served.json("GET", "/api/health", headers={"Host": f"attacker.example:{served.port}"})
    assert status == 403 and body["error"]["code"] == "forbidden"


def test_cross_site_fetch_of_the_api_is_refused(served):
    status, _ = served.json("GET", "/api/systems", headers={"Sec-Fetch-Site": "cross-site"})
    assert status == 403


def test_health_alias_and_rpc_share_the_result(served):
    status, body = served.json("GET", "/api/health")
    assert status == 200 and body["ok"] is True and "id" not in body
    assert body["result"]["served_units"] == ["main_ecu"]

    status, body = served.rpc({"id": "h1", "method": "health"})
    assert status == 200 and body["id"] == "h1" and body["ok"] is True
    assert body["result"]["status"] == "ok"


def test_systems_lists_modes_units_and_deploy_variants(served):
    status, body = served.json("GET", "/api/systems")
    assert status == 200
    (system,) = body["result"]["systems"]
    assert system["id"] == f"{PKG}:{SYSTEM}"
    assert system["default_mode"] == "Runtime"
    assert system["modes"] == [
        {"name": "Runtime", "has_structure": True},
        {"name": "Simulation", "has_structure": False},
    ]
    assert [(u["name"], u["status"]) for u in system["compute_units"]] == [
        ("main_ecu", "available"),
        ("sub_ecu", "remote_unsupported"),
        ("infra", "offboard"),
    ]
    assert set(system["compute_units"][0]) == {"name", "kind", "description", "status"}
    assert system["deploy_variants"] == [{"name": "car_a", "arguments": {"vehicle_id": "a"}}]
    assert system["pages"]["launch_commands"] == f"{SITE}/launch.html"
    assert served.request("GET", system["pages"]["overview"])[0].status == 200


def test_undeclared_units_fall_back_to_launcher_directories(tmp_path):
    install = _install_tree(tmp_path, compute_units=[])
    (system,), errors = exports.discover_systems(install)
    assert not errors
    view = system.to_view(None, {})
    assert [(u["name"], u["kind"], u["status"]) for u in view["compute_units"]] == [
        ("main_ecu", "onboard", "available")
    ]


def test_unreadable_manifest_is_reported(tmp_path):
    install = _install_tree(tmp_path, compute_units=UNITS)
    broken = install / "other_pkg" / "share" / "other_pkg" / "exports" / "Broken"
    broken.mkdir(parents=True)
    (broken / "deployment.json").write_text("{")
    systems, errors = exports.discover_systems(install)
    assert [s.name for s in systems] == [SYSTEM]
    assert len(errors) == 1 and errors[0].path.endswith("Broken/deployment.json")


@pytest.mark.parametrize(
    "headers, code, status",
    [
        ({CLIENT_HEADER: ""}, "forbidden", 403),
        ({"Content-Type": "text/plain"}, "unsupported_media_type", 415),
        ({"Origin": "http://attacker.example"}, "forbidden", 403),
    ],
)
def test_rpc_requires_json_client_header_and_same_origin(served, headers, code, status):
    got_status, body = served.rpc({"id": 1, "method": "health"}, headers)
    assert got_status == status and body["error"]["code"] == code


def test_rpc_accepts_same_origin(served):
    status, body = served.rpc({"id": 1, "method": "health"}, {"Origin": f"http://localhost:{served.port}"})
    assert status == 200 and body["ok"] is True


@pytest.mark.parametrize(
    "envelope, code",
    [
        ({"id": 2, "method": "no.such"}, "unknown_method"),
        ({"id": 3}, "bad_request"),
        ({"id": 4, "method": "health", "params": [1]}, "invalid_params"),
        ({"id": True, "method": "health"}, "bad_request"),
    ],
)
def test_rpc_errors_use_the_error_envelope(served, envelope, code):
    _, body = served.rpc(envelope)
    assert body["ok"] is False and body["error"]["code"] == code
    assert set(body) == {"id", "ok", "error"}


def test_rpc_rejects_get_and_unknown_api_paths(served):
    assert served.json("GET", "/api/rpc")[1]["error"]["code"] == "method_not_allowed"
    assert served.json("GET", "/api/nothing")[1]["error"]["code"] == "not_found"
    assert served.json("POST", "/systems.html")[1]["error"]["code"] == "method_not_allowed"


def test_oversized_body_is_refused(served):
    status, body = served.rpc({"id": 1, "method": "health", "params": {"pad": "x" * 70000}})
    assert status == 413 and body["error"]["code"] == "payload_too_large"


def _read_events(port, count):
    """Raw SSE frames off a socket, as (field -> value) dicts, up to ``count`` frames."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(f"GET /api/events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n".encode())
    buffer = b""
    while buffer.count(b"\n\n") < count + 2:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buffer += chunk
    sock.close()
    head, _, stream = buffer.partition(b"\r\n\r\n")
    frames = []
    for block in stream.decode().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if fields:
            frames.append(fields)
    return head.decode(), frames


def test_event_stream_says_hello_then_heartbeats(served):
    head, frames = _read_events(served.port, 3)
    assert "Content-Type: text/event-stream" in head
    assert frames[0]["retry"] == "3000"
    assert frames[1]["event"] == "hello"
    heartbeats = [f for f in frames[2:] if f.get("event") == "heartbeat"]
    assert heartbeats
    seqs = [int(f["id"]) for f in heartbeats]
    assert seqs == sorted(seqs) and all(b - a == 1 for a, b in zip(seqs, seqs[1:]))


def test_closed_event_stream_is_unsubscribed(served):
    _read_events(served.port, 1)
    for _ in range(100):
        if served.daemon.hub.subscriber_count == 0:
            break
        threading.Event().wait(0.02)
    assert served.daemon.hub.subscriber_count == 0


def test_missing_page_reloads_while_a_build_runs(served):
    spec = jobs.JobSpec(
        key=build.BUILD_JOB_KIND,
        kind=build.BUILD_JOB_KIND,
        title="build",
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=served.daemon.install_root,
    )
    # The fixture's daemon shutdown ends the job.
    asyncio.run_coroutine_threadsafe(served.daemon.jobs.start(spec), served.loop).result(5)
    response, payload = served.request("GET", f"/systems/other/{SYSTEM}/index.html")
    assert response.status == 404
    assert b'http-equiv="refresh"' in payload and b"A build is running" in payload
