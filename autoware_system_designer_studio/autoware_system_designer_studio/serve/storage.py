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

"""Where the daemon writes: two roots, each function in a subdirectory of its own.

The log root holds the run directories, which a later run need not find again (launch logs, replay
runs); the config root holds the files kept across runs (measurement records, saved replay setups).
The roots set through the daemon are kept in a settings file and win over the defaults at the next
start; a root given on the command line wins over the settings file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from autoware_system_designer_runtime import records, user_home
from autoware_system_designer_runtime.run_log import LAUNCH_LOGS, LOG_BASE, REPLAY_LOGS

from . import api

SETTINGS_ENV = "AUTOWARE_SYSTEM_DESIGNER_STUDIO_SETTINGS"
SETTINGS_FILE = "studio.yaml"
ROOTS = ("log_root", "config_root")
REPLAY_SETUPS = "replay_setups"


def settings_file() -> Path:
    """The daemon's settings file: ``$AUTOWARE_SYSTEM_DESIGNER_STUDIO_SETTINGS``, else in the user home."""
    override = os.environ.get(SETTINGS_ENV)
    if override:
        return Path(override).expanduser()
    return user_home.home() / SETTINGS_FILE


def defaults() -> dict[str, Path]:
    return {"log_root": LOG_BASE, "config_root": records.config_root()}


def _root(value: Any, name: str) -> Optional[Path]:
    """The absolute root *value* names; None for an empty one."""
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise api.ApiError(api.INVALID_PARAMS, f"{name} must be a path")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise api.ApiError(api.INVALID_PARAMS, f"{name} must be an absolute path")
    return path


def _read_settings(path: Path) -> dict[str, Path]:
    import yaml

    try:
        payload = yaml.safe_load(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError(f"{path}: expected a mapping")
    found = {}
    for name in ROOTS:
        try:
            root = _root(payload.get(name), name)
        except api.ApiError as exc:
            raise ValueError(f"{path}: {exc.message}") from exc
        if root is not None:
            found[name] = root
    return found


@dataclass
class Storage:
    """The roots in use, the ones set (None leaves the default) and a fixed record root, if any."""

    settings_path: Path
    log_root_set: Optional[Path] = None
    config_root_set: Optional[Path] = None
    record_override: Optional[Path] = None

    @classmethod
    def load(
        cls,
        settings_path: Optional[Path] = None,
        *,
        log_root: Optional[Path] = None,
        config_root: Optional[Path] = None,
        record_root: Optional[Path] = None,
    ) -> "Storage":
        """The roots of the command line, else of the settings file. The record root is the one given,
        else ``$AUTOWARE_SYSTEM_DESIGNER_RECORD_DIR``, else ``records`` of the config root."""
        path = settings_path or settings_file()
        saved = _read_settings(path)
        env_records = os.environ.get(records.RECORD_DIR_ENV)
        return cls(
            settings_path=path,
            log_root_set=log_root or saved.get("log_root"),
            config_root_set=config_root or saved.get("config_root"),
            record_override=record_root or (Path(env_records).expanduser() if env_records else None),
        )

    @property
    def log_root(self) -> Path:
        return self.log_root_set or LOG_BASE

    @property
    def config_root(self) -> Path:
        return self.config_root_set or records.config_root()

    @property
    def launch_logs(self) -> Path:
        return self.log_root / LAUNCH_LOGS

    @property
    def replay_logs(self) -> Path:
        return self.log_root / REPLAY_LOGS

    @property
    def records(self) -> Path:
        return self.record_override or self.config_root / records.RECORDS_DIR

    @property
    def replay_setups(self) -> Path:
        return self.config_root / REPLAY_SETUPS

    def set(self, params: Mapping[str, Any]) -> None:
        """Sets the roots *params* names, an empty one back to its default, and keeps them in the
        settings file."""
        import yaml

        changes = {name: _root(params[name], name) for name in ROOTS if name in params}
        if not changes:
            raise api.ApiError(api.INVALID_PARAMS, f"name one of {', '.join(ROOTS)}")
        kept = {
            "log_root": changes.get("log_root", self.log_root_set),
            "config_root": changes.get("config_root", self.config_root_set),
        }
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                yaml.safe_dump({name: str(root) for name, root in kept.items() if root is not None})
            )
        except OSError as exc:
            raise api.ApiError(api.UNAVAILABLE, f"cannot write {self.settings_path}: {exc}") from exc
        self.log_root_set = kept["log_root"]
        self.config_root_set = kept["config_root"]

    def view(self) -> dict[str, Any]:
        return {
            "settings_file": str(self.settings_path),
            "log_root": str(self.log_root),
            "config_root": str(self.config_root),
            "defaults": {name: str(root) for name, root in defaults().items()},
            "set": {name: getattr(self, f"{name}_set") is not None for name in ROOTS},
            "dirs": {
                "launch_logs": str(self.launch_logs),
                "replay_logs": str(self.replay_logs),
                "records": str(self.records),
                "replay_setups": str(self.replay_setups),
            },
            "records_fixed": self.record_override is not None,
            "user_config_dir": str(user_home.user_config_dir()),
            "config_overrides": [str(path) for path in user_home.user_overrides()],
        }
