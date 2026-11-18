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

"""The user's directory of the system designer tools, and the configuration files layered over it.

The user home is ``$AUTOWARE_SYSTEM_DESIGNER_HOME``, else ``~/.autoware/system_designer``::

    <user home>/studio.yaml          # studio settings
    <user home>/config/<name>.yaml   # overrides of the package's config/<name>.yaml
    <user home>/records/             # measurement records
    <user home>/replay_setups/       # saved replay setups

A configuration file is the package's file with each top-level key the user's file of the same name
sets replaced by the user's value; a key a configuration names as merged keeps the package's entries
the user's mapping does not set.

Plain Python with no ROS import.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, TypeVar

T = TypeVar("T")

HOME_ENV = "AUTOWARE_SYSTEM_DESIGNER_HOME"
DEFAULT_HOME = Path("~") / ".autoware" / "system_designer"
CONFIG_DIR = "config"
PACKAGE_CONFIG_DIR = Path(__file__).parent / CONFIG_DIR


class ConfigFileError(ValueError):
    pass


def home() -> Path:
    override = os.environ.get(HOME_ENV)
    return Path(override or DEFAULT_HOME).expanduser()


def user_config_dir() -> Path:
    return home() / CONFIG_DIR


def config_files(name: str) -> list[Path]:
    """Files the configuration *name* is read from, in layering order: the package's, then the user's
    when present."""
    files = [PACKAGE_CONFIG_DIR / name]
    user = user_config_dir() / name
    if user.is_file():
        files.append(user)
    return files


def user_overrides() -> list[Path]:
    """The user's files that override a configuration file of the package."""
    directory = user_config_dir()
    names = sorted(path.name for path in PACKAGE_CONFIG_DIR.glob("*.yaml"))
    return [directory / name for name in names if (directory / name).is_file()]


def load_yaml(path: Path) -> Any:
    """The content of one YAML file."""
    import yaml

    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigFileError(f"{path}: {exc}") from exc


def read_config(name: str, merged: Collection[str] = ()) -> tuple[dict[str, Any], list[Path]]:
    """The mapping of the configuration *name* with the files it was read from; the mappings of the
    *merged* keys are layered entry by entry."""
    files = config_files(name)
    layered: dict[str, Any] = {}
    for path in files:
        payload = load_yaml(path)
        if payload is None:
            continue
        if not isinstance(payload, dict):
            raise ConfigFileError(f"{path}: expected a mapping")
        for key, value in payload.items():
            if key in merged and isinstance(value, dict) and isinstance(layered.get(key), dict):
                value = {**layered[key], **value}
            layered[key] = value
    return layered, files


def _stamp(files: list[Path]) -> tuple[tuple[str, int, int], ...]:
    stamps = []
    for path in files:
        status = path.stat()
        stamps.append((str(path), status.st_mtime_ns, status.st_size))
    return tuple(stamps)


# Parsed configuration by name, with the stamp of the files it was read from.
_parsed: dict[str, tuple[tuple[tuple[str, int, int], ...], Any]] = {}


def cached_config(
    name: str, parse: Callable[[Mapping[str, Any]], T], error: type[ValueError], merged: Collection[str] = ()
) -> T:
    """The configuration *name* parsed by *parse*; read again once one of its files changed, appeared
    or went. A failure is raised as *error*, naming the files."""
    try:
        files = config_files(name)
        stamp = _stamp(files)
        cached = _parsed.get(name)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        payload, files = read_config(name, merged)
    except (ConfigFileError, OSError) as exc:
        raise error(str(exc)) from exc
    try:
        value = parse(payload)
    except ValueError as exc:
        raise error(f"{describe(files)}: {exc}") from exc
    _parsed[name] = (stamp, value)
    return value


def describe(files: list[Path]) -> str:
    """Names *files* in error messages."""
    return " + ".join(str(path) for path in files)
