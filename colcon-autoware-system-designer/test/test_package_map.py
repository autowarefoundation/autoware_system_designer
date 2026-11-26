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

import json
from pathlib import Path
from types import SimpleNamespace

from colcon_autoware_system_designer.package_map import PACKAGE_MAP_FILENAME, PackageMapPackageSelection
from colcon_core.package_decorator import PackageDecorator
from colcon_core.package_descriptor import PackageDescriptor


def _decorator(path: Path, name: str, selected: bool = True) -> PackageDecorator:
    descriptor = PackageDescriptor(path)
    descriptor.name = name
    descriptor.type = "ros.ament_cmake"
    decorator = PackageDecorator(descriptor)
    decorator.selected = selected
    return decorator


def _select(args, decorators):
    PackageMapPackageSelection().select_packages(args=args, decorators=decorators)


def test_build_writes_every_discovered_package(tmp_path):
    src = tmp_path / "src"
    decorators = [_decorator(src / "pkg_a", "pkg_a"), _decorator(src / "pkg_b", "pkg_b", selected=False)]
    build_base = tmp_path / "build"

    _select(SimpleNamespace(verb_name="build", build_base=str(build_base)), decorators)

    data = json.loads((build_base / PACKAGE_MAP_FILENAME).read_text())
    assert data == {"format_version": 1, "packages": {"pkg_a": str(src / "pkg_a"), "pkg_b": str(src / "pkg_b")}}
    assert [d.selected for d in decorators] == [True, False]


def test_relative_package_paths_are_written_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    build_base = tmp_path / "build"

    _select(SimpleNamespace(verb_name="build", build_base="build"), [_decorator(Path("src/pkg_a"), "pkg_a")])

    data = json.loads((build_base / PACKAGE_MAP_FILENAME).read_text())
    assert data["packages"] == {"pkg_a": str(tmp_path / "src" / "pkg_a")}


def test_first_package_of_a_duplicate_name_wins(tmp_path):
    build_base = tmp_path / "build"
    decorators = [_decorator(tmp_path / "first", "pkg"), _decorator(tmp_path / "second", "pkg")]

    _select(SimpleNamespace(verb_name="build", build_base=str(build_base)), decorators)

    data = json.loads((build_base / PACKAGE_MAP_FILENAME).read_text())
    assert data["packages"] == {"pkg": str(tmp_path / "first")}


def test_other_verbs_write_nothing(tmp_path):
    build_base = tmp_path / "build"

    _select(SimpleNamespace(verb_name="test", build_base=str(build_base)), [_decorator(tmp_path / "pkg", "pkg")])
    _select(SimpleNamespace(verb_name="list"), [_decorator(tmp_path / "pkg", "pkg")])

    assert not build_base.exists()


def test_unwritable_build_base_leaves_the_build_running(tmp_path):
    blocker = tmp_path / "build"
    blocker.write_text("")

    _select(SimpleNamespace(verb_name="build", build_base=str(blocker / "sub")), [_decorator(tmp_path / "pkg", "pkg")])

    assert blocker.read_text() == ""
