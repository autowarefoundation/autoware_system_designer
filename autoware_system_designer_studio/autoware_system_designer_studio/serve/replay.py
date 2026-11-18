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

"""Deterministic replay of a scope as a job: the runtime's replay command on a mode's system
structure, with the request's scope, remap, accepted names, outputs and driver settings as its
configuration. `register` gives a daemon the replay methods.

One replay runs per daemon. Its run directory is under the daemon's replay logs; the job's detail
names it and, once the job ended, carries the summary of the
files the replay wrote there.

A setup is a replay saved by name as a YAML file in the daemon's setup directory: the system, mode, deploy and
bag it runs on, its configuration (the runtime's configuration file content), the source each boundary input
resolved to and the outputs recorded, and its run options.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Optional
from urllib.parse import unquote, urlsplit

from autoware_system_designer_runtime.replay_record import (
    BOUNDARY_FILE,
    REPLAY_COMMAND,
    REPLAY_CONFIG_FILE,
    RESULT_FILE,
    VERDICT_FILE,
    BagError,
    bag_topics,
    driver_settings,
    parse_progress,
    read_replay_file,
)
from autoware_system_designer_runtime.run_log import LOG_BASE, REPLAY_LOGS

from ..exports import SystemExport
from . import api, jobs

if TYPE_CHECKING:
    from .server import ServeDaemon

REPLAY_JOB_KIND = "replay"
RUNTIME_PACKAGE = "autoware_system_designer_runtime"
# ROS_DOMAIN_ID range of the default DDS port mapping.
DOMAIN_ID_MAX = 232
# Pre-flight problems and differing topics kept in a job's summary.
SUMMARY_ITEMS = 50
# Name of a saved setup: its file name without the suffix.
SETUP_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SETUP_SUFFIX = ".yaml"
SETUP_KEYS = ("system", "mode", "deploy", "bag", "config", "inputs", "recorded", "run")
SETUP_CONFIG_KEYS = ("scope", "remap", "accept", "outputs", "skip_outputs", "lead_in", "driver")
SETUP_RUN_KEYS = ("start", "duration", "domain_id", "save_reference")
# Levels below a bag directory a file search descends, and the directories it visits at most.
FIND_DEPTH = 4
FIND_DIRECTORIES = 20000


@dataclass(frozen=True)
class ReplayPlan:
    argv: tuple[str, ...]
    run_dir: Path
    config: dict[str, Any]
    title: str
    params: dict[str, Any]


def find_command(install_root: Path, env: Mapping[str, str]) -> Optional[Path]:
    """The replay command on the launch environment's PATH, else under the install tree."""
    found = shutil.which(REPLAY_COMMAND, path=env.get("PATH"))
    if found:
        return Path(found)
    for candidate in (install_root / RUNTIME_PACKAGE / "bin" / REPLAY_COMMAND, install_root / "bin" / REPLAY_COMMAND):
        if candidate.is_file():
            return candidate
    return None


def _path_of(text: str) -> Path:
    parsed = urlsplit(text)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return Path(text).expanduser()


def bag_path(text: str) -> Path:
    """The bag a request names, by its absolute path or its ``file://`` URI."""
    path = _path_of(text)
    if not path.is_absolute():
        raise api.ApiError(api.INVALID_PARAMS, f"the bag path must be absolute: {text}")
    return path


def find_bag_file(directories: Iterable[Path], name: str, size: int) -> Optional[Path]:
    """The newest file called *name* of *size* bytes under the *directories*; None when they hold none."""
    found: list[tuple[float, Path]] = []
    visited = 0
    for directory in directories:
        base = directory if directory.is_dir() else directory.parent
        for current, children, files in os.walk(base):
            visited += 1
            if visited > FIND_DIRECTORIES:
                break
            if len(Path(current).relative_to(base).parts) >= FIND_DEPTH:
                children.clear()
            if name not in files:
                continue
            candidate = Path(current) / name
            try:
                status = candidate.stat()
            except OSError:
                continue
            if status.st_size == size:
                found.append((status.st_mtime, candidate))
    return max(found)[1] if found else None


def read_bag(path: str) -> dict[str, Any]:
    """Topics of the bag at *path*, from its ``metadata.yaml``, else from its storage file."""
    target = bag_path(path)
    try:
        return bag_topics(target)
    except BagError as exc:
        raise api.ApiError(api.NOT_FOUND, str(exc)) from exc
    except ImportError as exc:
        raise api.ApiError(api.UNAVAILABLE, f"{path} has no metadata.yaml and rosbag2 is not importable") from exc


def _topic_names(params: Mapping[str, Any], name: str, *, required: bool = False) -> list[str]:
    value = params.get(name) or []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.startswith("/") for item in value):
        raise api.ApiError(api.INVALID_PARAMS, f"{name} must be a list of absolute names")
    if required and not value:
        raise api.ApiError(api.INVALID_PARAMS, f"{name} is required")
    return list(value)


def _remap(params: Mapping[str, Any]) -> dict[str, str]:
    value = params.get("remap") or {}
    if not isinstance(value, dict) or not all(
        isinstance(source, str) and isinstance(target, str) and source.startswith("/") and target.startswith("/")
        for source, target in value.items()
    ):
        raise api.ApiError(api.INVALID_PARAMS, "remap must map recorded topics to topics, by absolute name")
    return dict(value)


def _seconds(params: Mapping[str, Any], name: str, *, positive: bool) -> Optional[float]:
    value = params.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0 or (positive and value == 0):
        raise api.ApiError(
            api.INVALID_PARAMS,
            f"{name} must be a {'positive number of seconds' if positive else 'number of seconds, zero or more'}",
        )
    return value


def _domain_id(params: Mapping[str, Any]) -> Optional[int]:
    value = params.get("domain_id")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= DOMAIN_ID_MAX):
        raise api.ApiError(api.INVALID_PARAMS, f"domain_id must be an integer within 0..{DOMAIN_ID_MAX}")
    return value


def _driver(params: Mapping[str, Any]) -> dict[str, Any]:
    """The driver settings a request or a setup sets, each of the runtime's names and of its unit."""
    value = params.get("driver") or {}
    units = {setting["name"]: setting["unit"] for setting in driver_settings()}
    if not isinstance(value, dict) or set(value) - set(units):
        raise api.ApiError(api.INVALID_PARAMS, f"driver must map settings of {', '.join(units)} to values")
    for name, item in value.items():
        number = not isinstance(item, bool) and isinstance(item, (int, float))
        if units[name] == "count":
            if not number or not isinstance(item, int) or item < 1:
                raise api.ApiError(api.INVALID_PARAMS, f"driver.{name} must be a count, one or more")
        elif not number or item < 0:
            raise api.ApiError(api.INVALID_PARAMS, f"driver.{name} must be a number of seconds, zero or more")
    return dict(value)


def replay_config(params: Mapping[str, Any]) -> dict[str, Any]:
    """The runtime configuration a request or a setup gives."""
    config = {
        "scope": _topic_names(params, "scope", required=True),
        "remap": _remap(params),
        "accept": _topic_names(params, "accept"),
        "outputs": _topic_names(params, "outputs"),
        "skip_outputs": _topic_names(params, "skip_outputs"),
    }
    lead_in = _seconds(params, "lead_in", positive=False)
    if lead_in is not None:
        config["lead_in"] = lead_in
    driver = _driver(params)
    if driver:
        config["driver"] = driver
    return config


def _new_run_dir(base: Path) -> Path:
    """A timestamped directory name under *base* that no run holds; numbered within the second of another."""
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for name in itertools.chain([stamp], (f"{stamp}_{n}" for n in itertools.count(2))):
        if not (base / name).exists():
            return base / name
    raise AssertionError("unreachable")


def plan_replay(
    system: SystemExport,
    command: Path,
    params: Mapping[str, Any],
    *,
    mode: str,
    bag: str,
    deploy: Optional[str],
    log_base: Optional[Path] = None,
) -> ReplayPlan:
    """The command of one replay request, in a new run directory under *log_base*."""
    config = replay_config(params)
    check = params.get("check", False)
    save_reference = params.get("save_reference", False)
    if not isinstance(check, bool) or not isinstance(save_reference, bool):
        raise api.ApiError(api.INVALID_PARAMS, "check and save_reference must be booleans")
    start = _seconds(params, "start", positive=False)
    duration = _seconds(params, "duration", positive=True)
    domain_id = _domain_id(params)
    run_dir = _new_run_dir(log_base or LOG_BASE / REPLAY_LOGS)
    argv = [
        str(command),
        str(system.structure_path(mode)),
        "--config",
        str(run_dir / REPLAY_CONFIG_FILE),
        "--bag",
        str(bag_path(bag)),
        "--log-dir",
        str(run_dir),
    ]
    if deploy:
        argv += ["--deploy", deploy]
    if check:
        argv.append("--check")
    if save_reference:
        argv.append("--save-reference")
    if start:
        argv += ["--start", f"{start:g}"]
    if duration is not None:
        argv += ["--duration", f"{duration:g}"]
    if domain_id is not None:
        argv += ["--domain-id", str(domain_id)]
    return ReplayPlan(
        argv=tuple(argv),
        run_dir=run_dir,
        config=config,
        title=f"{'check' if check else 'replay'} {system.name} {mode}: {', '.join(config['scope'])}",
        params={
            "system": system.id,
            "mode": mode,
            "bag": bag,
            "deploy": deploy,
            **config,
            "check": check,
            "save_reference": save_reference,
            "start": start,
            "duration": duration,
            "domain_id": domain_id,
        },
    )


def write_config(plan: ReplayPlan) -> None:
    """The plan's configuration in its run directory."""
    import yaml

    plan.run_dir.mkdir(parents=True, exist_ok=True)
    (plan.run_dir / REPLAY_CONFIG_FILE).write_text(yaml.safe_dump(plan.config, sort_keys=False))


def discard_config(plan: ReplayPlan) -> None:
    """Removes the run directory of a plan whose job did not start."""
    shutil.rmtree(plan.run_dir, ignore_errors=True)


def update_progress(detail: dict[str, Any], line: str) -> bool:
    """The progress a log line reports: the detail's ``progress`` is the last one, its ``phases`` the
    last fraction of each phase reached; true when the line carries one."""
    progress = parse_progress(line)
    if progress is None:
        return False
    detail["progress"] = progress
    detail.setdefault("phases", {})[progress["phase"]] = progress["fraction"]
    return True


def summary(run_dir: Path) -> dict[str, Any]:
    """What a finished replay wrote: pre-flight problems, the run's counts and its verdict."""
    found: dict[str, Any] = {}
    boundary = read_replay_file(run_dir, BOUNDARY_FILE)
    if boundary is not None:
        found["problems"] = list(boundary.get("problems") or [])[:SUMMARY_ITEMS]
        found["fed"] = sorted(boundary.get("fed") or {})
        found["recorded"] = sorted(boundary.get("recorded") or {})
    result = read_replay_file(run_dir, RESULT_FILE)
    if result is not None:
        found["result"] = {
            key: result.get(key)
            for key in (
                "completed",
                "error",
                "wall_s",
                "start_ns",
                "end_ns",
                "messages",
                "callbacks",
                "timer_fires",
                "outputs",
                "lost_outputs",
                "outputs_digest",
                "reference",
            )
        }
        found["result"]["violations"] = list(result.get("violations") or [])[:SUMMARY_ITEMS]
        found["result"]["endpoint_problems"] = list(result.get("endpoint_problems") or [])[:SUMMARY_ITEMS]
    verdict = read_replay_file(run_dir, VERDICT_FILE)
    if verdict is not None:
        found["verdict"] = {
            "passed": verdict.get("passed"),
            "reference": verdict.get("reference"),
            "topics": list(verdict.get("topics") or [])[:SUMMARY_ITEMS],
        }
    return found


# -- saved setups -------------------------------------------------------------


def _setup_file(directory: Path, name: Any) -> Path:
    if not isinstance(name, str) or not SETUP_NAME.match(name):
        raise api.ApiError(
            api.INVALID_PARAMS, "name must start with a letter or digit and hold letters, digits, '_', '.' or '-'"
        )
    return directory / f"{name}{SETUP_SUFFIX}"


def check_setup(setup: Any) -> dict[str, Any]:
    """A setup as stored: known keys only, its configuration and run options valid."""
    if not isinstance(setup, Mapping):
        raise api.ApiError(api.INVALID_PARAMS, "a setup is a mapping")
    unknown = set(setup) - set(SETUP_KEYS)
    if unknown:
        raise api.ApiError(api.INVALID_PARAMS, f"setup: unknown key(s) {sorted(unknown)}")
    for key in ("system", "mode", "deploy", "bag"):
        if setup.get(key) is not None and not isinstance(setup[key], str):
            raise api.ApiError(api.INVALID_PARAMS, f"setup: {key} must be a string")
    config = setup.get("config")
    if not isinstance(config, Mapping):
        raise api.ApiError(api.INVALID_PARAMS, "setup: config must be a mapping")
    unknown = set(config) - set(SETUP_CONFIG_KEYS)
    if unknown:
        raise api.ApiError(api.INVALID_PARAMS, f"setup: unknown config key(s) {sorted(unknown)}")
    run = setup.get("run") or {}
    if not isinstance(run, Mapping) or set(run) - set(SETUP_RUN_KEYS):
        raise api.ApiError(api.INVALID_PARAMS, f"setup: run takes the keys {', '.join(SETUP_RUN_KEYS)}")
    _seconds(run, "start", positive=False)
    _seconds(run, "duration", positive=True)
    _domain_id(run)
    if not isinstance(run.get("save_reference", False), bool):
        raise api.ApiError(api.INVALID_PARAMS, "setup: run.save_reference must be a boolean")
    checked = {key: setup[key] for key in ("system", "mode", "deploy", "bag") if setup.get(key)}
    checked["config"] = replay_config(config)
    inputs = setup.get("inputs") or {}
    if not isinstance(inputs, Mapping) or not all(
        isinstance(topic, str)
        and topic.startswith("/")
        and (source is None or (isinstance(source, str) and source.startswith("/")))
        for topic, source in inputs.items()
    ):
        raise api.ApiError(
            api.INVALID_PARAMS, "setup: inputs maps each input to its bag topic, or to null when accepted unfed"
        )
    checked["inputs"] = dict(inputs)
    checked["recorded"] = _topic_names(setup, "recorded")
    checked["run"] = {key: run[key] for key in SETUP_RUN_KEYS if run.get(key) is not None}
    return checked


def list_setups(directory: Path) -> list[dict[str, Any]]:
    """The setups saved in *directory*, by name; one that does not read names its error."""
    import yaml

    found = []
    for path in sorted(directory.glob(f"*{SETUP_SUFFIX}")) if directory.is_dir() else ():
        name = path.name[: -len(SETUP_SUFFIX)]
        if not SETUP_NAME.match(name):
            continue
        entry: dict[str, Any] = {"name": name, "modified": path.stat().st_mtime}
        try:
            setup = check_setup(yaml.safe_load(path.read_text()))
            entry.update({key: setup.get(key) for key in ("system", "mode")})
            entry["scope"] = setup["config"]["scope"]
        except (OSError, yaml.YAMLError, api.ApiError) as exc:
            entry["error"] = str(exc)
        found.append(entry)
    return found


def read_setup(directory: Path, name: Any) -> dict[str, Any]:
    import yaml

    path = _setup_file(directory, name)
    try:
        text = path.read_text()
    except FileNotFoundError as exc:
        raise api.ApiError(api.NOT_FOUND, f"no setup {name!r} in {directory}") from exc
    try:
        return check_setup(yaml.safe_load(text))
    except yaml.YAMLError as exc:
        raise api.ApiError(api.INVALID_PARAMS, f"{path}: {exc}") from exc


def write_setup(directory: Path, name: Any, setup: Any) -> Path:
    import yaml

    checked = check_setup(setup)
    path = _setup_file(directory, name)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(checked, sort_keys=False))
    return path


def delete_setup(directory: Path, name: Any) -> None:
    path = _setup_file(directory, name)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise api.ApiError(api.NOT_FOUND, f"no setup {name!r} in {directory}") from exc


# -- daemon methods -----------------------------------------------------------


def register(daemon: "ServeDaemon") -> None:
    """Registers the replay methods on *daemon*."""

    async def read(params: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(read_bag, api.str_param(params, "path", required=True))

    async def find(params: dict[str, Any]) -> dict[str, Any]:
        name = api.str_param(params, "name", required=True)
        size = params.get("size")
        directories = params.get("directories") or []
        if "/" in name or name in (".", ".."):
            raise api.ApiError(api.INVALID_PARAMS, "name must be a file name")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise api.ApiError(api.INVALID_PARAMS, "size must be a number of bytes")
        if not isinstance(directories, list) or not all(isinstance(item, str) and item for item in directories):
            raise api.ApiError(api.INVALID_PARAMS, "directories must be a list of paths")
        searched = [path for path in map(_path_of, directories) if path.is_absolute()] + daemon.bag_dirs
        found = await asyncio.to_thread(find_bag_file, searched, name, size)
        if found is None:
            raise api.ApiError(api.NOT_FOUND, f"no {name} of {size} bytes under the bag directories")
        return {"path": str(found)}

    async def settings(params: dict[str, Any]) -> dict[str, Any]:
        return {"driver": await asyncio.to_thread(driver_settings)}

    async def start(params: dict[str, Any]) -> dict[str, Any]:
        system = await daemon.find_system(api.str_param(params, "system", required=True))
        mode = api.str_param(params, "mode") or system.manifest.get("default_mode")
        if not isinstance(mode, str) or mode not in system.mode_keys():
            raise api.ApiError(api.INVALID_PARAMS, f"unknown mode {mode!r} of {system.id}")
        if not system.structure_path(mode).is_file():
            raise api.ApiError(api.INVALID_PARAMS, f"mode {mode!r} of {system.id} has no system structure")
        deploy = api.str_param(params, "deploy")
        if deploy is not None and deploy not in {v["name"] for v in system.deploy_variants()}:
            raise api.ApiError(api.INVALID_PARAMS, f"unknown deploy variant {deploy!r} of {system.id}")
        env = daemon.launch.env
        if not env.get("ROS_DISTRO"):
            raise api.ApiError(api.UNAVAILABLE, "the launch environment has no ROS_DISTRO (see --env-script)")
        command = find_command(daemon.install_root, env)
        if command is None:
            raise api.ApiError(
                api.UNAVAILABLE, f"{REPLAY_COMMAND} not found on the launch PATH or under the install tree"
            )
        plan = plan_replay(
            system,
            command,
            params,
            mode=mode,
            bag=api.str_param(params, "bag", required=True),
            deploy=deploy,
            log_base=daemon.storage.replay_logs,
        )
        # A launched unit on the replay's domain answers the scope's requests and feeds its topics.
        launched = sorted(s.compute_unit for s in daemon.launch.sessions.values() if s.active)
        if launched and not plan.params["check"] and plan.params["domain_id"] is None:
            raise api.ApiError(
                api.CONFLICT,
                f"compute unit(s) {', '.join(launched)} are launched on this daemon; "
                "shut them down or give the replay a domain_id of its own",
            )

        def on_line(job: jobs.Job, line: str) -> bool:
            return update_progress(job.detail, line)

        def on_exit(job: jobs.Job) -> None:
            job.detail.update(summary(plan.run_dir))

        spec = jobs.JobSpec(
            key=REPLAY_JOB_KIND,
            kind=REPLAY_JOB_KIND,
            title=plan.title,
            argv=list(plan.argv),
            cwd=daemon.launch_cwd,
            env=env,
            params=plan.params,
            detail={"run_dir": str(plan.run_dir)},
            on_line=on_line,
            on_exit=on_exit,
        )
        await asyncio.to_thread(write_config, plan)
        try:
            return (await daemon.jobs.start(spec)).to_view()
        except api.ApiError:
            await asyncio.to_thread(discard_config, plan)
            raise

    async def setups(params: dict[str, Any]) -> dict[str, Any]:
        directory = daemon.storage.replay_setups
        return {"dir": str(directory), "setups": await asyncio.to_thread(list_setups, directory)}

    async def setup_get(params: dict[str, Any]) -> dict[str, Any]:
        setup = await asyncio.to_thread(read_setup, daemon.storage.replay_setups, params.get("name"))
        return {"name": params["name"], "setup": setup}

    async def setup_save(params: dict[str, Any]) -> dict[str, Any]:
        directory = daemon.storage.replay_setups
        try:
            path = await asyncio.to_thread(write_setup, directory, params.get("name"), params.get("setup"))
        except OSError as exc:
            raise api.ApiError(api.UNAVAILABLE, f"cannot write to {directory}: {exc}") from exc
        return {"name": params["name"], "path": str(path)}

    async def setup_delete(params: dict[str, Any]) -> dict[str, Any]:
        await asyncio.to_thread(delete_setup, daemon.storage.replay_setups, params.get("name"))
        return {"name": params["name"]}

    daemon.methods.register("replay.bag", read)
    daemon.methods.register("replay.find", find)
    daemon.methods.register("replay.settings", settings)
    daemon.methods.register("replay.start", start)
    daemon.methods.register("replay.setups", setups)
    daemon.methods.register("replay.setup.get", setup_get)
    daemon.methods.register("replay.setup.save", setup_save)
    daemon.methods.register("replay.setup.delete", setup_delete)
