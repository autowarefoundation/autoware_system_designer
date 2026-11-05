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

"""Supervised jobs of the serve daemon, and builds planned from the generated build scripts."""

import asyncio
import os
import sys
import time

import pytest

from autoware_system_designer_studio import exports
from autoware_system_designer_studio.serve import api, build, jobs
from autoware_system_designer_studio.serve.events import EventHub

from .test_serve import PKG, SYSTEM, UNITS, _install_tree, _Running


def _python(code):
    return [sys.executable, "-c", code]


def _spec(code, key="k", **kwargs):
    return jobs.JobSpec(key=key, kind="test", title="t", argv=_python(code), cwd=os.getcwd(), **kwargs)


async def _run(runner, spec):
    job = await runner.start(spec)
    await asyncio.wait_for(job.done.wait(), 10)
    return job


class _Recorder(EventHub):
    def __init__(self):
        super().__init__(max_subscribers=1, heartbeat_s=60)
        self.published = []

    def publish(self, topic, data):
        self.published.append((topic, data))
        return super().publish(topic, data)


# -- log ring ------------------------------------------------------------------


def test_log_ring_returns_lines_after_a_sequence_number_and_skips_evicted_ones():
    ring = jobs.LogRing(capacity=3)
    for line in "abcde":
        ring.append(line)
    assert (ring.first_seq, ring.last_seq) == (3, 5)
    assert ring.since(0) == (3, ["c", "d", "e"])
    assert ring.since(3) == (4, ["d", "e"])
    assert ring.since(4, limit=1) == (5, ["e"])
    assert ring.since(5) == (6, [])


# -- runner --------------------------------------------------------------------


def test_job_captures_merged_output_and_exit_code():
    async def scenario():
        hub = _Recorder()
        runner = jobs.JobRunner(hub)
        seen = []
        exited = []
        spec = _spec(
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
            on_line=lambda job, line: seen.append(line),
            on_exit=lambda job: exited.append(job.state),
        )
        job = await _run(runner, spec)
        return hub, job, seen, exited

    hub, job, seen, exited = asyncio.run(scenario())
    assert job.state == jobs.STATE_FAILED
    assert job.exit_code == 3
    assert sorted(job.log.since(0)[1]) == ["err", "out"]
    assert sorted(seen) == ["err", "out"]
    assert exited == [jobs.STATE_FAILED]
    states = [data["state"] for topic, data in hub.published if topic == jobs.TOPIC_JOB]
    assert states == [jobs.STATE_RUNNING, jobs.STATE_FAILED]
    logged = [line for topic, data in hub.published if topic == jobs.TOPIC_JOB_LOG for line in data["lines"]]
    assert sorted(logged) == ["err", "out"]


def test_job_cuts_overlong_lines():
    async def scenario():
        runner = jobs.JobRunner(_Recorder())
        return await _run(runner, _spec(f"print('x' * {jobs.MAX_LINE_CHARS * 2})"))

    job = asyncio.run(scenario())
    assert job.state == jobs.STATE_SUCCEEDED
    assert job.log.since(0)[1] == ["x" * jobs.MAX_LINE_CHARS]


def test_one_active_job_per_key():
    async def scenario():
        runner = jobs.JobRunner(_Recorder())
        first = await runner.start(_spec("import time; time.sleep(30)"))
        with pytest.raises(api.ApiError) as conflict:
            await runner.start(_spec("pass"))
        other = await _run(runner, _spec("pass", key="other"))
        runner.cancel(first.id)
        await asyncio.wait_for(first.done.wait(), 10)
        again = await _run(runner, _spec("pass"))
        return conflict.value, other, again

    conflict, other, again = asyncio.run(scenario())
    assert conflict.code == api.CONFLICT
    assert other.state == again.state == jobs.STATE_SUCCEEDED


def test_cancel_interrupts_the_process_group():
    async def scenario():
        runner = jobs.JobRunner(_Recorder())
        # The child spawns a grandchild; both live in the job's process group.
        job = await runner.start(
            _spec(
                "import subprocess, sys, time; "
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
                "print(p.pid, flush=True); time.sleep(30)"
            )
        )
        while not job.log.last_seq:
            await asyncio.sleep(0.02)
        grandchild = int(job.log.since(0)[1][0])
        runner.cancel(job.id)
        await asyncio.wait_for(job.done.wait(), 10)
        return job, grandchild

    job, grandchild = asyncio.run(scenario())
    assert job.state == jobs.STATE_CANCELLED
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            os.kill(grandchild, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("grandchild survived the cancel")


def test_cancel_escalates_when_signals_are_ignored(monkeypatch):
    monkeypatch.setattr(jobs, "CANCEL_TERM_AFTER_S", 0.2)
    monkeypatch.setattr(jobs, "CANCEL_KILL_AFTER_S", 0.2)

    async def scenario():
        runner = jobs.JobRunner(_Recorder())
        job = await runner.start(
            _spec(
                "import signal, time; "
                "signal.signal(signal.SIGINT, signal.SIG_IGN); signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('ready', flush=True); time.sleep(30)"
            )
        )
        while not job.log.last_seq:
            await asyncio.sleep(0.02)
        runner.cancel(job.id)
        await asyncio.wait_for(job.done.wait(), 10)
        return job

    job = asyncio.run(scenario())
    assert job.state == jobs.STATE_CANCELLED
    assert job.exit_code == -9


def test_finished_jobs_are_evicted_oldest_first():
    async def scenario():
        runner = jobs.JobRunner(_Recorder(), finished_kept=2)
        for _ in range(3):
            await _run(runner, _spec("pass"))
        return [job.id for job in runner.list()]

    assert asyncio.run(scenario()) == ["test-3", "test-2"]


def test_shutdown_ends_active_jobs():
    async def scenario():
        runner = jobs.JobRunner(_Recorder())
        job = await runner.start(_spec("import time; time.sleep(30)"))
        await runner.shutdown(wait_s=5)
        return job

    assert asyncio.run(scenario()).state == jobs.STATE_CANCELLED


def test_unstartable_command_is_reported_unavailable():
    async def scenario():
        runner = jobs.JobRunner(_Recorder())
        spec = jobs.JobSpec(key="k", kind="test", title="t", argv=["/nonexistent/binary"], cwd=os.getcwd())
        with pytest.raises(api.ApiError) as error:
            await runner.start(spec)
        return error.value, runner.list()

    error, listed = asyncio.run(scenario())
    assert error.code == api.UNAVAILABLE
    assert listed == []


# -- build plans ---------------------------------------------------------------

GENERATED_SCRIPT = """#!/bin/bash
# Auto-generated build script for ECU: main_ecu
# Mode: Runtime

colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release --packages-up-to \\
  demo_deployment \\
  demo_nodes
"""


def _fake_colcon(root, output):
    bin_dir = root / "bin"
    bin_dir.mkdir()
    colcon = bin_dir / "colcon"
    lines = "".join(f"echo '{line}'\n" for line in output)
    colcon.write_text(f'#!/bin/sh\necho "args: $*"\n{lines}')
    colcon.chmod(0o755)
    return bin_dir


def _workspace(tmp_path, monkeypatch, output=()):
    install = _install_tree(tmp_path, compute_units=UNITS)
    (tmp_path / "src").mkdir()
    scripts = install / PKG / "share" / PKG / "exports" / SYSTEM / "build_scripts" / "Runtime"
    scripts.mkdir(parents=True)
    (scripts / "build_main_ecu.sh").write_text(GENERATED_SCRIPT)
    (scripts / "build_sub_ecu.sh").write_text(GENERATED_SCRIPT)
    monkeypatch.setenv("PATH", f"{_fake_colcon(tmp_path, output)}{os.pathsep}{os.environ['PATH']}")
    return install


def _system(install):
    systems, _ = exports.discover_systems(install)
    return systems[0]


def test_script_build_args_split_packages_from_options(tmp_path):
    script = tmp_path / "build.sh"
    script.write_text(GENERATED_SCRIPT)
    options, packages = build.script_build_args(script)
    assert packages == ["demo_deployment", "demo_nodes"]
    assert options == ["--symlink-install", "--cmake-args", "-DCMAKE_BUILD_TYPE=Release"]


def test_script_build_args_refuse_shell_in_package_names(tmp_path):
    script = tmp_path / "build.sh"
    script.write_text("colcon build --packages-up-to 'pkg;rm -rf /'\n")
    with pytest.raises(api.ApiError) as error:
        build.script_build_args(script)
    assert error.value.code == api.UNAVAILABLE


def test_plan_workspace_build_from_the_generated_script(tmp_path, monkeypatch):
    install = _workspace(tmp_path, monkeypatch)
    plan = build.plan_build(_system(install), install, build.TARGET_WORKSPACE, compute_unit="main_ecu")
    assert plan.packages == ("demo_deployment", "demo_nodes")
    assert plan.argv[1:] == (
        "build",
        "--install-base",
        str(install),
        "--packages-up-to",
        "demo_deployment",
        "demo_nodes",
        "--symlink-install",
        "--cmake-args",
        "-DCMAKE_BUILD_TYPE=Release",
    )
    assert plan.params == {
        "system": f"{PKG}:{SYSTEM}",
        "target": "workspace",
        "mode": "Runtime",
        "compute_unit": "main_ecu",
    }


def test_plan_deployment_build_follows_a_merged_install(tmp_path, monkeypatch):
    install = _workspace(tmp_path, monkeypatch)
    (install / build.INSTALL_LAYOUT_FILE).write_text("merged\n")
    plan = build.plan_build(_system(install), install, build.TARGET_DEPLOYMENT)
    assert plan.packages == (PKG,)
    assert "--merge-install" in plan.argv
    assert plan.argv[plan.argv.index("--packages-select") + 1] == PKG


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target": "everything"},
        {"target": "workspace"},
        {"target": "workspace", "compute_unit": "nope"},
        {"target": "workspace", "compute_unit": "main_ecu", "mode": "Nope"},
    ],
)
def test_plan_rejects_invalid_selections(tmp_path, monkeypatch, kwargs):
    install = _workspace(tmp_path, monkeypatch)
    with pytest.raises(api.ApiError) as error:
        build.plan_build(_system(install), install, **kwargs)
    assert error.value.code == api.INVALID_PARAMS


def test_resolve_workspace(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    assert build.resolve_workspace(install, None) is None
    (tmp_path / "src").mkdir()
    assert build.resolve_workspace(install, None) == tmp_path
    assert build.resolve_workspace(install, str(tmp_path / "other")) == tmp_path / "other"


# -- API -----------------------------------------------------------------------


def _wait_job(served, job_id):
    deadline = time.time() + 10
    while time.time() < deadline:
        _, body = served.rpc({"method": "jobs.get", "params": {"job": job_id}})
        if body["result"]["job"]["state"] != jobs.STATE_RUNNING:
            return body["result"]
        time.sleep(0.05)
    pytest.fail(f"{job_id} did not finish")


@pytest.fixture
def building(tmp_path, monkeypatch):
    install = _workspace(
        tmp_path,
        monkeypatch,
        output=["Starting >>> demo_nodes", "Starting >>> autoware_system_designer_runtime", f"Starting >>> {PKG}"],
    )
    running = _Running(install, served_units=["main_ecu"], workspace=tmp_path, heartbeat_s=60)
    running.daemon.serves_own_install = True
    yield running
    running.close()


def test_build_runs_in_the_workspace_and_flags_a_rebuilt_daemon(building):
    status, body = building.rpc(
        {
            "id": 1,
            "method": "build.start",
            "params": {"system": f"{PKG}:{SYSTEM}", "target": "workspace", "compute_unit": "main_ecu"},
        }
    )
    assert status == 200, body
    job = body["result"]
    assert job["state"] == jobs.STATE_RUNNING
    assert job["cwd"] == str(building.daemon.workspace)
    assert job["detail"]["packages"] == ["demo_deployment", "demo_nodes"]

    result = _wait_job(building, job["id"])
    assert result["job"]["state"] == jobs.STATE_SUCCEEDED
    assert result["job"]["detail"]["built_packages"] == ["demo_nodes", "autoware_system_designer_runtime", PKG]
    assert result["log"]["first_seq"] == 1
    assert result["log"]["lines"][0].startswith("args: build --install-base")

    _, health = building.json("GET", "/api/health")
    assert health["result"]["restart_required"] is True
    _, listed = building.json("GET", "/api/jobs")
    assert [j["id"] for j in listed["result"]["jobs"]] == [job["id"]]


def test_build_refuses_a_unit_served_elsewhere(building):
    status, body = building.rpc(
        {
            "method": "build.start",
            "params": {"system": f"{PKG}:{SYSTEM}", "target": "workspace", "compute_unit": "sub_ecu"},
        }
    )
    assert status == 400
    assert body["error"]["code"] == api.INVALID_PARAMS


def test_build_of_an_unknown_system_is_not_found(building):
    status, body = building.rpc({"method": "build.start", "params": {"system": "x:y", "target": "deployment"}})
    assert status == 404
    assert body["error"]["code"] == api.NOT_FOUND


def test_build_is_unavailable_without_a_workspace(tmp_path):
    running = _Running(_install_tree(tmp_path, compute_units=UNITS), heartbeat_s=60)
    try:
        status, body = running.rpc(
            {"method": "build.start", "params": {"system": f"{PKG}:{SYSTEM}", "target": "deployment"}}
        )
    finally:
        running.close()
    assert status == 503
    assert body["error"]["code"] == api.UNAVAILABLE


def test_jobs_cancel_and_get_of_unknown_job(building):
    for method in ("jobs.get", "jobs.cancel"):
        status, body = building.rpc({"method": method, "params": {"job": "build-99"}})
        assert status == 404, method
    status, body = building.rpc({"method": "jobs.get", "params": {"job": "build-1", "since": -1}})
    assert status == 400


@pytest.mark.parametrize(
    "name, content_type", [("build.html", "text/html"), ("build.js", "text/javascript"), ("daemon.css", "text/css")]
)
def test_daemon_pages_are_served(building, name, content_type):
    response, payload = building.request("GET", f"/daemon/{name}", headers={"Host": f"127.0.0.1:{building.port}"})
    assert response.status == 200
    assert response.getheader("Content-Type").startswith(content_type)
    assert payload


@pytest.mark.parametrize("path", ["/daemon/", "/daemon/__init__.py", "/daemon/../jobs.py", "/daemon/web/build.html"])
def test_other_daemon_paths_are_not_served(building, path):
    response, _ = building.request("GET", path, headers={"Host": f"127.0.0.1:{building.port}"})
    assert response.status == 404


def test_systems_list_reports_units_with_build_scripts(building):
    _, body = building.json("GET", "/api/systems")
    assert body["result"]["systems"][0]["build_scripts"] == {"Runtime": ["main_ecu", "sub_ecu"], "Simulation": []}
