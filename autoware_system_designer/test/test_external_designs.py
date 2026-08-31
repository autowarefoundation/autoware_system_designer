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

"""Optional smoke test over an external design package (e.g. autoware_sample_designs).

Skipped unless AWSD_EXTERNAL_DESIGNS_DIR points at a package directory and
AWSD_EXTERNAL_DESIGNS_TARGET names a deployment target inside it. The repo's
own test suite must pass without this; it exists to catch drift against
real-world designs living outside this repository.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pipeline_harness import REPO_DIR

EXTERNAL_DIR = os.environ.get("AWSD_EXTERNAL_DESIGNS_DIR")
EXTERNAL_TARGET = os.environ.get("AWSD_EXTERNAL_DESIGNS_TARGET")


@pytest.mark.skipif(
    not (EXTERNAL_DIR and EXTERNAL_TARGET),
    reason="set AWSD_EXTERNAL_DESIGNS_DIR and AWSD_EXTERNAL_DESIGNS_TARGET to run",
)
def test_external_design_package_builds(tmp_path):
    external = Path(EXTERNAL_DIR)
    assert external.is_dir(), f"AWSD_EXTERNAL_DESIGNS_DIR is not a directory: {external}"

    # The external package's manifests come from the real collector, not the
    # fixture fabricator, so package attribution matches a workspace build.
    manifest_dir = tmp_path / "manifests"
    collector = REPO_DIR / "script" / "collect_system_design_manifests.py"
    subprocess.run(
        [
            sys.executable,
            str(collector),
            str(external),
            str(manifest_dir),
            str(tmp_path / "install"),
            "--package-map-mode",
            "source",
        ],
        check=True,
        capture_output=True,
    )

    from pipeline_harness import _load_deployment_process

    from autoware_system_designer.parser.yaml_parser import yaml_parser

    yaml_parser.clear_cache()
    out_root = tmp_path / "out"
    workspace_yaml = external / "workspace.yaml"
    _load_deployment_process().build(
        str(external / EXTERNAL_TARGET),
        str(manifest_dir),
        str(out_root),
        str(workspace_yaml) if workspace_yaml.is_file() else None,
    )
    assert any((out_root / "exports").rglob("system_structure/*.json"))
