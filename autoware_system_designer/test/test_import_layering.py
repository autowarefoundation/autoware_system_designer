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

"""Architectural layering of the package's top-level subpackages.

Each subpackage may import only from the layers listed for it. Root-level
modules (deploy.py) are pipeline orchestrators and may import anything.
"""

import ast
from pathlib import Path

PACKAGE = "autoware_system_designer"
PACKAGE_DIR = Path(__file__).resolve().parents[1] / PACKAGE

# Subpackage -> the internal subpackages it may import (itself always allowed).
ALLOWED_IMPORTS = {
    "common": set(),
    "schema": {"common"},
    "model": {"common", "schema"},
    "parser": {"common", "schema", "model"},
    "builder": {"common", "schema", "model", "parser"},
    "generator": {"common", "schema", "model", "parser", "builder"},
    "visualizer": {"common", "schema", "model", "parser", "builder"},
    "linter": {"common", "schema", "model", "parser"},
    "runtime": set(),
}


def _iter_internal_imports(py_file: Path):
    tree = ast.parse(py_file.read_text(), filename=str(py_file))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(PACKAGE + ".") or alias.name == PACKAGE:
                    yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import: resolve against the file's package
                rel = py_file.relative_to(PACKAGE_DIR.parent)
                parts = list(rel.parts[:-1])
                base = parts if node.level == 1 else parts[: -(node.level - 1)]
                yield ".".join(base + ([node.module] if node.module else []))
            elif node.module and (node.module.startswith(PACKAGE + ".") or node.module == PACKAGE):
                yield node.module


def test_subpackage_layering():
    violations = []
    for py_file in sorted(PACKAGE_DIR.rglob("*.py")):
        rel = py_file.relative_to(PACKAGE_DIR)
        if "__pycache__" in rel.parts:
            continue
        if len(rel.parts) == 1:
            continue  # root-level orchestrator modules may import anything
        src = rel.parts[0]
        allowed = ALLOWED_IMPORTS.get(src)
        assert allowed is not None, f"unknown subpackage '{src}'; add it to ALLOWED_IMPORTS"
        for module in _iter_internal_imports(py_file):
            parts = module.split(".")
            if len(parts) < 2 or parts[0] != PACKAGE:
                continue  # package root (e.g. DESIGN_FORMAT_VERSION) is layer 0
            dst = parts[1]
            if dst not in ALLOWED_IMPORTS:
                continue  # root-level module import (orchestrator)
            if dst != src and dst not in allowed:
                violations.append(f"{rel}: {src} -> {dst} ({module})")
    assert not violations, "layering violations:\n" + "\n".join(violations)
