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

from pathlib import Path

import ament_index_python


class AmentPackage:
    def __init__(self, name: str, path: Path):
        self.name = name
        self.path = path
        self.designs = {path.stem: path for path in self.__list_design_files()}

    def __list_design_files(self):
        base = self.get_design_directory()
        for path in base.glob("**/*.yaml"):
            yield path.relative_to(base)

    def get_share_directory(self):
        return self.path / "share" / self.name

    def get_design_directory(self):
        return self.get_share_directory() / "design"

    def get_design_file(self, name: str):
        return self.get_design_directory() / self.designs[name]


class AmentWorkspace:
    def __init__(self):
        self.packages = {package.name: package for package in self.__list_packages()}
        self.lookup = self.__init_lookup(self.packages)

    @staticmethod
    def __list_packages():
        packages = ament_index_python.get_packages_with_prefixes()
        for name, path in packages.items():
            yield AmentPackage(name, Path(path))

    @staticmethod
    def __init_lookup(packages):
        lookup = {}
        for package in packages.values():
            for design in package.designs:
                lookup.setdefault(design, []).append(package)
        return lookup

    def search_design_file(self, name: str):
        package = self.lookup.get(name)
        if package is None:
            raise ValueError(f"Design file '{name}' is not found in any package")
        if len(package) < 1:
            raise ValueError(f"Design file '{name}' is not found in any package")
        if len(package) > 1:
            raise ValueError(f"Design file '{name}' is found in multiple packages")
        return package[0].get_design_file(name)
