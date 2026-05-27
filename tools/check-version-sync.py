#!/usr/bin/env python3
# Checks that version in pyproject.toml and package.xml are identical.
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parent.parent / "autoware_system_designer"

pyproject = root / "pyproject.toml"
match = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.MULTILINE)
if not match:
    print("ERROR: version not found in pyproject.toml")
    sys.exit(1)
toml_version = match.group(1)

package_xml = root / "package.xml"
xml_version = ET.parse(package_xml).getroot().findtext("version")
if not xml_version:
    print("ERROR: <version> not found in package.xml")
    sys.exit(1)

if toml_version != xml_version:
    print(f"ERROR: version mismatch — pyproject.toml={toml_version!r}, package.xml={xml_version!r}")
    sys.exit(1)

print(f"OK: versions match ({toml_version})")
