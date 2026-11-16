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
structure, with the request's scope, remap, accepted names and outputs as its configuration.

One replay runs per daemon. Its run directory is under the runtime's log base; the job's detail
names it and, once the job ended, carries the summary of the files the replay wrote there.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from autoware_system_designer_runtime.replay_record import (
    BOUNDARY_FILE,
    REPLAY_COMMAND,
    RESULT_FILE,
    VERDICT_FILE,
    BagError,
    bag_topics,
    read_replay_file,
)
from autoware_system_designer_runtime.run_log import LOG_BASE

from ..exports import SystemExport
from . import api

REPLAY_JOB_KIND = "replay"
RUNTIME_PACKAGE = "autoware_system_designer_runtime"
CONFIG_FILE = "replay_config.json"
# ROS_DOMAIN_ID range of the default DDS port mapping.
DOMAIN_ID_MAX = 232
# Pre-flight problems and differing topics kept in a job's summary.
SUMMARY_ITEMS = 50


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


def read_bag(path: str) -> dict[str, Any]:
    try:
        return bag_topics(Path(path).expanduser())
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
    """The command of one replay request, in a new run directory under the log base."""
    config = {
        "scope": _topic_names(params, "scope", required=True),
        "remap": _remap(params),
        "accept": _topic_names(params, "accept"),
        "outputs": _topic_names(params, "outputs"),
    }
    check = params.get("check", False)
    save_reference = params.get("save_reference", False)
    if not isinstance(check, bool) or not isinstance(save_reference, bool):
        raise api.ApiError(api.INVALID_PARAMS, "check and save_reference must be booleans")
    duration = params.get("duration")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0):
        raise api.ApiError(api.INVALID_PARAMS, "duration must be a positive number of seconds")
    domain_id = params.get("domain_id")
    if domain_id is not None and (
        isinstance(domain_id, bool) or not isinstance(domain_id, int) or not 0 <= domain_id <= DOMAIN_ID_MAX
    ):
        raise api.ApiError(api.INVALID_PARAMS, f"domain_id must be an integer within 0..{DOMAIN_ID_MAX}")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = (log_base or LOG_BASE) / f"{stamp}_replay"
    argv = [
        str(command),
        str(system.structure_path(mode)),
        "--config",
        str(run_dir / CONFIG_FILE),
        "--bag",
        str(Path(bag).expanduser()),
        "--log-dir",
        str(run_dir),
    ]
    if deploy:
        argv += ["--deploy", deploy]
    if check:
        argv.append("--check")
    if save_reference:
        argv.append("--save-reference")
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
            "duration": duration,
            "domain_id": domain_id,
        },
    )


def write_config(plan: ReplayPlan) -> None:
    """The plan's configuration in its run directory; JSON, which the runtime reads as YAML."""
    plan.run_dir.mkdir(parents=True, exist_ok=True)
    (plan.run_dir / CONFIG_FILE).write_text(json.dumps(plan.config, indent=2) + "\n")


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
