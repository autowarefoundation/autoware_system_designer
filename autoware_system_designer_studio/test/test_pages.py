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
from autoware_system_designer_studio.pages import assets, system_icon
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
        f"{SITE}/config.js",
        f"{SITE}/icon.svg",
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
    assert 'dataBase: "data/"' in text
    assert "latencyModes: []" in text
    diagrams = json.loads(text.partition("diagrams: ")[2].partition(",\n")[0])
    assert diagrams[0] == {
        "type": "node_diagram",
        "label": "Node Diagram",
        "short": "Nodes",
        "icon": assets.DIAGRAMS["node_diagram"]["icon"],
        "script": "../../../assets/js/diagrams/node_diagram.js",
        "searchable": True,
        "layered": True,
    }
    assert [d["type"] for d in diagrams] == list(assets.DIAGRAMS)
    assert "treemap" not in assets.DIAGRAMS
    assert "js/diagrams/treemap.js" in assets.WORKBENCH_SCRIPTS


def test_config_carries_the_selection_scope_and_launch_commands(tmp_path):
    _, system = _system(tmp_path)
    system.manifest["deploy_variants"] = []
    text = Site([system]).render(f"{SITE}/config.js")
    assert f"systemId: {json.dumps(system.id)}" in text
    assert 'siteRoot: "../../../"' in text
    assert "deployNames: []" in text
    assert 'processTopics: ["/clock", "/parameter_events", "/rosout"]' in text
    assert 'ambientTopics: ["/tf", "/tf_static"]' in text
    replay = json.loads(text.partition("replayRuntime: ")[2].partition(",\n")[0])
    assert replay["command"] == "autoware-system-designer-replay" and replay["configFile"] == "replay.yaml"
    assert replay["structurePaths"]["Runtime"].endswith(f"exports/{system.name}/system_structure/Runtime.json")
    units = json.loads(text.partition("computeUnits: ")[2].partition(",\n")[0])
    assert [u["name"] for u in units] == [u["name"] for u in system.compute_units()]
    commands = json.loads(text.partition("launchCommands: ")[2].rpartition("\n};")[0])
    main = next(c for c in commands if c["unit"] == "main_ecu")
    assert main["mode"] == "Runtime" and main["deploy"] == ""
    assert main["xml"].endswith("launcher/Runtime/main_ecu/main_ecu.launch.xml")
    assert "--compute-unit main_ecu" in main["native"]


def test_workbench_loads_the_shell_before_its_activities(tmp_path):
    _, system = _system(tmp_path)
    workbench = Site([system]).render(f"{SITE}/index.html")
    for element_id in ("topbar", "rail", "side-panel", "main-area", "inspector-body", "statusbar"):
        assert f'id="{element_id}"' in workbench
    shell = workbench.index("js/shell/shell.js")
    assert workbench.index("js/core/store.js") < shell
    assert workbench.index("js/shell/layout.js") < shell
    assert workbench.index("js/shell/statusbar.js") < shell
    widgets = workbench.index("js/widgets/controls.js")
    assert shell < widgets
    for activity, files in assets.ACTIVITIES.items():
        entry = workbench.index(f"js/activities/{activity}/index.js")
        assert widgets < entry
        for script in files["scripts"]:
            assert workbench.index(script) <= entry
        for style in files["styles"]:
            assert style in workbench


def test_every_workbench_asset_is_in_the_bundle():
    for relative in assets.WORKBENCH_SCRIPTS + assets.WORKBENCH_STYLESHEETS + (assets.WORKBENCH_ENTRY,):
        assert assets.web_file(relative) is not None, relative


def test_pages_link_the_bundle_relative_to_the_site_root(tmp_path):
    _, system = _system(tmp_path)
    site = Site([system])
    assert 'href="assets/css/tokens.css"' in site.render("index.html")
    index = site.render("index.html")
    assert f'href="{SITE}/index.html"' in index
    assert f'src="{SITE}/icon.svg"' in index
    assert '<link rel="icon" type="image/svg+xml" href="assets/img/favicon.svg">' in index
    assert 'src="assets/img/awsd_icon.svg"' in index
    workbench = site.render(f"{SITE}/index.html")
    assert '<link rel="icon" type="image/svg+xml" href="../../../assets/img/favicon.svg" />' in workbench
    assert 'src="../../../assets/js/core/sd.js"' in workbench
    assert 'src="../../../assets/img/awsd_mark.svg"' in workbench
    assert 'src="../../../assets/js/pages/workbench_page.js"' in workbench


def test_config_lists_the_systems_of_the_site_for_the_selector(tmp_path):
    _, system = _system(tmp_path)
    text = Site([system]).render(f"{SITE}/config.js")
    systems = json.loads(text.partition("systems: ")[2].partition(",\n")[0])
    assert systems == [
        {
            "id": system.id,
            "name": SYSTEM,
            "package": PKG,
            "page": f"{SITE}/index.html",
            "icon": f"{SITE}/icon.svg",
        }
    ]


def test_system_icon_is_its_own_file_else_a_default_per_system(tmp_path):
    _, system = _system(tmp_path)
    site = Site([system])
    default = site.render(f"{SITE}/icon.svg")
    assert default.startswith("<svg ") and f">{system_icon.monogram(SYSTEM)}</text>" in default
    assert default == system_icon.default_icon(system.id, SYSTEM)
    assert default != system_icon.default_icon("other:System", SYSTEM)

    design = tmp_path / "design"
    design.mkdir()
    system.manifest["system_file"] = str(design / f"{SYSTEM}.system.yaml")
    (design / f"{SYSTEM}.icon.svg").write_text("<svg id='own'/>")
    assert site.render(f"{SITE}/icon.svg") == "<svg id='own'/>"


def test_oversized_system_icon_falls_back_to_the_default_with_a_warning(tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(exports.logger, "warning", lambda *args: warnings.append(args[0] % args[1:]))
    _, system = _system(tmp_path)
    design = tmp_path / "design"
    design.mkdir()
    system.manifest["system_file"] = str(design / f"{SYSTEM}.system.yaml")
    (design / f"{SYSTEM}.icon.svg").write_text("<svg>" + " " * exports.ICON_MAX_BYTES + "</svg>")
    assert Site([system]).render(f"{SITE}/icon.svg") == system_icon.default_icon(system.id, SYSTEM)
    assert len(warnings) == 1 and "system icon ignored" in warnings[0]


@pytest.mark.parametrize(
    ("name", "letters"),
    [("PilotAutoX2Gen2", "PA"), ("AutowareSample", "AS"), ("demo_system", "DS"), ("Solo", "S"), ("", "?")],
)
def test_default_icon_monogram_takes_the_first_two_words(name, letters):
    assert system_icon.monogram(name) == letters


def _launch_commands(site):
    text = site.render(f"{SITE}/config.js")
    return json.loads(text.partition("launchCommands: ")[2].rpartition("\n};")[0])


def test_launch_commands_list_declared_onboard_units_per_deploy(tmp_path):
    _, system = _system(tmp_path)
    system.manifest["deploy_variants"] = []
    launch = system.export_dir / "launcher" / "Runtime"
    (launch / "sub_ecu").mkdir()
    (launch / "sub_ecu" / "sub_ecu.launch.xml").write_text("<launch/>")
    (launch / "infra").mkdir()
    (launch / "infra" / "infra.launch.xml").write_text("<launch/>")
    commands = _launch_commands(Site([system]))
    assert [c["unit"] for c in commands] == ["main_ecu", "sub_ecu"]
    assert commands[0]["xml"] == (
        f"ros2 launch install/{PKG}/share/{PKG}/exports/{SYSTEM}/launcher/Runtime/main_ecu/main_ecu.launch.xml"
    )

    hostile = "<script>alert(1)"
    system.manifest["deploy_variants"] = [{"name": hostile, "arguments": []}]
    (system.export_dir / "launcher" / "deployments" / hostile / "Runtime" / "main_ecu").mkdir(parents=True)
    (
        system.export_dir / "launcher" / "deployments" / hostile / "Runtime" / "main_ecu" / "main_ecu.launch.xml"
    ).write_text("")
    site = Site([system])
    assert "<script>" not in site.render(f"{SITE}/config.js")
    (command,) = _launch_commands(site)
    assert command["deploy"] == hostile
    assert command["native"].endswith(f"--deploy {hostile}")


def test_launch_commands_keep_the_declared_unit_order(tmp_path):
    _, system = _system(tmp_path)
    system.manifest["deploy_variants"] = []
    system.manifest["compute_units"] = [
        {"name": "sub_ecu", "kind": "onboard"},
        {"name": "main_ecu", "kind": "onboard"},
    ]
    launch = system.export_dir / "launcher" / "Runtime"
    (launch / "sub_ecu").mkdir()
    (launch / "sub_ecu" / "sub_ecu.launch.xml").write_text("<launch/>")
    assert [c["unit"] for c in _launch_commands(Site([system]))] == ["sub_ecu", "main_ecu"]


@pytest.mark.parametrize(
    "path",
    ["systems", f"{SITE}", f"{SITE}/other.html", f"{SITE}/launch.html", f"{SITE}/data/x/y.js", "systems/a/b/c.html"],
)
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
