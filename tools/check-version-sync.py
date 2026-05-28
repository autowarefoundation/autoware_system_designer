#!/usr/bin/env python3
# Checks that the version is identical across all three sources of truth:
#   [1] autoware_system_designer/pyproject.toml   — Python package metadata
#   [2] autoware_system_designer/package.xml      — ROS 2 package metadata
#   [3] pyproject.toml (repo root)                — pre-commit linter wrapper
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
inner_root = repo_root / "autoware_system_designer"

errors = []


def extract_toml_version(path: Path) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(), re.MULTILINE)
    if not match:
        print(f"ERROR: version not found in {path.relative_to(repo_root)}")
        sys.exit(1)
    return match.group(1)


inner_version = extract_toml_version(inner_root / "pyproject.toml")  # [1]
wrapper_version = extract_toml_version(repo_root / "pyproject.toml")  # [3]

package_xml = inner_root / "package.xml"
xml_version = ET.parse(package_xml).getroot().findtext("version")  # [2]
if not xml_version:
    print("ERROR: <version> not found in package.xml")
    sys.exit(1)

sources = [
    ("[1] autoware_system_designer/pyproject.toml ", inner_version),
    ("[2] autoware_system_designer/package.xml    ", xml_version),
    ("[3] pyproject.toml (wrapper)                ", wrapper_version),
]

versions = {v for _, v in sources}
if len(versions) > 1:
    print("ERROR: version mismatch detected\n")
    for label, ver in sources:
        print(f"  {label}  {ver}")
    sys.exit(1)

print(f"OK: versions match ({inner_version})")
