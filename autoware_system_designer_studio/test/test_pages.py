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

"""The studio site rendered from exports, and pack writing it for file://."""

import json

import pytest

from autoware_system_designer_studio import exports
from autoware_system_designer_studio.pack import MARKER, PackError, pack
from autoware_system_designer_studio.pages import assets
from autoware_system_designer_studio.pages.render import Site, data_script_text

from .test_serve import PKG, SYSTEM, UNITS, _install_tree

SITE = f"systems/{PKG}/{SYSTEM}"
LATENCY = {"schema": "autoware_system_designer/latency/2", "run": {"window_s": 1.0}}


def _system(tmp_path):
    install = _install_tree(tmp_path, compute_units=UNITS)
    (system,), _ = exports.discover_systems(install)
    return install, system


def _script_object(text, global_name):
    head, _, body = text.partition(" = ")
    assert head == f"window.{global_name}"
    _, _, assignment = body.partition("\n")
    return json.loads(assignment.partition(" = ")[2].rstrip().rstrip(";"))


def test_site_lists_pages_and_the_data_of_modes_with_a_structure(tmp_path):
    _, system = _system(tmp_path)
    paths = list(Site([system], record_root=tmp_path / "records").paths())
    assert paths == [
        "index.html",
        f"{SITE}/index.html",
        f"{SITE}/launch.html",
        f"{SITE}/config.js",
        f"{SITE}/data/Runtime.js",
    ]


def test_data_script_carries_the_structure_with_vis_guides_and_units(tmp_path):
    _, system = _system(tmp_path)
    text = Site([system]).render(f"{SITE}/data/Runtime.js")
    data = _script_object(text, assets.DATA_GLOBAL)
    assert data["path"] == "/"
    assert data["mode"] == "Runtime"
    assert [u["name"] for u in data["compute_units"]] == ["main_ecu", "sub_ecu", "infra"]
    assert "vis_guide" in data


def test_data_script_keeps_declaration_key_order():
    text = data_script_text("w", "default", {"zeta": 1, "alpha": 2})
    assert '{"zeta": 1, "alpha": 2}' in text


def test_data_script_quotes_the_mode_as_json():
    hostile = '"]; alert(1); //'
    text = data_script_text("w", hostile, {})
    assert f"window.w[{json.dumps(hostile)}] = " in text


def test_latency_prefers_the_newest_record_over_the_exported_reference(tmp_path):
    _, system = _system(tmp_path)
    (system.export_dir / "latency").mkdir()
    (system.export_dir / "latency" / "Runtime_latency.json").write_text(json.dumps(LATENCY))
    site = Site([system], record_root=tmp_path / "records")
    assert '"window_s": 1.0' in site.render(f"{SITE}/data/Runtime_latency.js")
    assert 'latencyModes: ["Runtime"]' in site.render(f"{SITE}/config.js")

    record_dir = tmp_path / "records" / PKG / SYSTEM / "latency" / "Runtime"
    record_dir.mkdir(parents=True)
    (record_dir / "20261105-120000.json").write_text(json.dumps({**LATENCY, "run": {"window_s": 9.0}}))
    assert '"window_s": 9.0' in site.render(f"{SITE}/data/Runtime_latency.js")


def test_invalid_latency_is_not_served(tmp_path):
    _, system = _system(tmp_path)
    (system.export_dir / "latency").mkdir()
    (system.export_dir / "latency" / "Runtime_latency.json").write_text('{"schema": "other"}')
    assert Site([system], record_root=tmp_path / "records").render(f"{SITE}/data/Runtime_latency.js") is None


def test_config_quotes_strings_as_json(tmp_path):
    _, system = _system(tmp_path)
    hostile = 'x"; alert(1); //'
    system.manifest["system_file"] = hostile
    text = Site([system]).render(f"{SITE}/config.js")
    assert f"systemDefinitionFile: {json.dumps(hostile)}" in text
    assert "dataBase: \"data/\"" in text
    assert "latencyModes: []" in text
    assert '"node_diagram": "../../../assets/js/diagrams/node_diagram.js"' in text


def test_pages_link_the_bundle_relative_to_the_site_root(tmp_path):
    _, system = _system(tmp_path)
    site = Site([system])
    assert 'href="assets/css/tokens.css"' in site.render("index.html")
    assert f'href="{SITE}/index.html"' in site.render("index.html")
    workbench = site.render(f"{SITE}/index.html")
    assert 'src="../../../assets/js/core/sd.js"' in workbench
    assert 'src="../../../assets/js/pages/overview_page.js"' in workbench


def test_launch_page_lists_declared_onboard_units_and_escapes_names(tmp_path):
    _, system = _system(tmp_path)
    system.manifest["deploy_variants"] = []
    launch = system.export_dir / "launcher" / "Runtime"
    (launch / "sub_ecu").mkdir()
    (launch / "sub_ecu" / "sub_ecu.launch.xml").write_text("<launch/>")
    (launch / "infra").mkdir()
    (launch / "infra" / "infra.launch.xml").write_text("<launch/>")
    page = Site([system]).render(f"{SITE}/launch.html")
    assert f"ros2 launch install/{PKG}/share/{PKG}/exports/{SYSTEM}/launcher/Runtime/main_ecu/main_ecu.launch.xml" in page
    assert "--compute-unit sub_ecu" in page
    assert "infra.launch.xml" not in page

    hostile = "<script>alert(1)</script>"
    system.manifest["deploy_variants"] = [{"name": hostile, "arguments": []}]
    (system.export_dir / "launcher" / "deployments" / hostile / "Runtime" / "main_ecu").mkdir(parents=True)
    (system.export_dir / "launcher" / "deployments" / hostile / "Runtime" / "main_ecu" / "main_ecu.launch.xml").write_text("")
    page = Site([system]).render(f"{SITE}/launch.html")
    assert "--deploy" in page
    assert hostile not in page
    assert "&lt;script&gt;" in page


@pytest.mark.parametrize("path", ["systems", f"{SITE}", f"{SITE}/other.html", f"{SITE}/data/x/y.js", "systems/a/b/c.html"])
def test_unknown_site_paths_render_nothing(tmp_path, path):
    _, system = _system(tmp_path)
    assert Site([system]).render(path) is None


def test_pack_writes_the_site_and_the_bundle(tmp_path):
    install, _ = _system(tmp_path)
    out = tmp_path / "out"
    written, errors = pack(install, out, record_root=tmp_path / "records")
    assert errors == []
    assert f"{SITE}/data/Runtime.js" in written
    assert (out / "index.html").is_file()
    assert (out / SITE / "config.js").is_file()
    for relative in assets.bundle_files():
        assert (out / assets.BUNDLE_DIR / relative).is_file()
    assert not (out / assets.BUNDLE_DIR / "daemon").exists()
    assert (out / MARKER).is_file()


def test_pack_rewrites_its_own_directory_and_refuses_others(tmp_path):
    install, _ = _system(tmp_path)
    out = tmp_path / "out"
    pack(install, out)
    stale = out / "systems" / "gone" / "Old" / "index.html"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    pack(install, out)
    assert not stale.exists()

    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "notes.txt").write_text("keep")
    with pytest.raises(PackError):
        pack(install, foreign)
    assert (foreign / "notes.txt").read_text() == "keep"


def test_pack_selects_systems_by_name(tmp_path):
    install, _ = _system(tmp_path)
    written, _ = pack(install, tmp_path / "out", systems=[SYSTEM])
    assert f"{SITE}/index.html" in written
    with pytest.raises(PackError):
        pack(install, tmp_path / "out2", systems=["Missing"])
