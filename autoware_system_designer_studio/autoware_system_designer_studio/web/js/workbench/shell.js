// Workbench shell: top bar, rail, side panel, inspector and status bar around
// the main area, whose tabs hold the diagram canvas and the views panels open.
// Panels register themselves; daemon panels appear only on a page a daemon
// served, and stay disabled until it offers their methods.
(function () {
  const SD = window.SD;

  const SIDE_PANEL_WIDTH = { key: "workbench.sidePanelWidth", min: 180, max: 640 };
  const INSPECTOR_WIDTH = { key: "workbench.inspectorWidth", min: 220, max: 720 };
  const INSPECTOR_HIDDEN_KEY = "workbench.inspectorHidden";
  const DEFAULT_PANEL = "explore";

  // id -> { def, button, body, mounted }
  const panels = new Map();
  let config = null;
  let daemonHealth = null;
  let probeDone = false;

  const el = (id) => document.getElementById(id);

  // Panel definition: { id, title, icon (SVG path data), order, daemon, requires, mount(body, ctx) }.
  // `daemon` panels need a daemon; `requires` lists the daemon methods they call.
  function registerPanel(def) {
    panels.set(def.id, { def, button: null, body: null, mounted: false });
  }

  function svgIcon(pathData) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 20 20");
    svg.setAttribute("width", "20");
    svg.setAttribute("height", "20");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", pathData);
    svg.appendChild(path);
    return svg;
  }

  // -- rail and side panel -----------------------------------------------

  function orderedPanels() {
    return Array.from(panels.values()).sort((a, b) => a.def.order - b.def.order);
  }

  function missingMethods(def) {
    return (def.requires || []).filter((method) => !SD.daemon.offers(method));
  }

  // Static pages hide daemon panels; daemon pages disable those whose methods are missing.
  function panelAvailability(def) {
    if (!def.daemon) return { visible: true, enabled: true, reason: "" };
    if (!daemonHealth) return { visible: false, enabled: false, reason: "" };
    const missing = missingMethods(def);
    return {
      visible: true,
      enabled: missing.length === 0,
      reason: missing.length ? `Not offered by this daemon (needs ${missing.join(", ")})` : "",
    };
  }

  function buildRail() {
    const rail = el("rail");
    const body = el("side-panel-body");
    orderedPanels().forEach((panel) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "rail-item";
      button.dataset.panel = panel.def.id;
      button.setAttribute("aria-label", panel.def.title);
      button.appendChild(svgIcon(panel.def.icon));
      const label = document.createElement("span");
      label.className = "rail-label";
      label.textContent = panel.def.title;
      button.appendChild(label);
      button.addEventListener("click", () => onRailClick(panel.def.id));
      if (panel.def.daemon) {
        const group = rail.querySelector(".rail-daemon-group") || rail.appendChild(railGroup());
        group.appendChild(button);
      } else {
        rail.querySelector(".rail-static-group").appendChild(button);
      }
      panel.button = button;

      const container = document.createElement("section");
      container.className = "side-panel-section";
      container.dataset.panel = panel.def.id;
      container.hidden = true;
      body.appendChild(container);
      panel.body = container;
    });
    refreshRail();
  }

  function railGroup() {
    const group = document.createElement("div");
    group.className = "rail-group rail-daemon-group";
    return group;
  }

  function refreshRail() {
    let anyDaemonPanel = false;
    panels.forEach((panel) => {
      const { visible, enabled, reason } = panelAvailability(panel.def);
      panel.button.hidden = !visible;
      panel.button.disabled = !enabled;
      panel.button.title = reason || panel.def.title;
      if (panel.def.daemon && visible) anyDaemonPanel = true;
    });
    const group = el("rail").querySelector(".rail-daemon-group");
    if (group) group.hidden = !anyDaemonPanel;
    const active = panels.get(SD.store.get("panel"));
    if (!active || !panelAvailability(active.def).enabled) {
      // The URL may name a daemon panel before the probe answers; fall back once it is known.
      if (active && active.def.daemon && daemonHealth === null && !probeDone) return;
      SD.store.set("panel", DEFAULT_PANEL);
    }
    showPanel(SD.store.get("panel"));
  }

  function onRailClick(id) {
    const collapsed = document.body.classList.contains("side-panel-collapsed");
    if (SD.store.get("panel") === id && !collapsed) {
      setSidePanelCollapsed(true);
      return;
    }
    setSidePanelCollapsed(false);
    SD.store.set("panel", id);
  }

  function showPanel(id) {
    const panel = panels.get(id);
    if (!panel || !panelAvailability(panel.def).enabled) return;
    panels.forEach((other) => {
      const active = other === panel;
      other.body.hidden = !active;
      other.button.classList.toggle("active", active);
      other.button.setAttribute("aria-pressed", String(active));
    });
    el("side-panel-title").textContent = panel.def.title;
    if (!panel.mounted) {
      panel.mounted = true;
      panel.def.mount(panel.body, { config, store: SD.store, daemon: SD.daemon });
    }
  }

  function setSidePanelCollapsed(collapsed) {
    document.body.classList.toggle("side-panel-collapsed", collapsed);
    refitCanvas();
  }

  // Diagram modules re-fit on window resize.
  function refitCanvas() {
    window.dispatchEvent(new Event("resize"));
  }

  // -- main area tabs ---------------------------------------------------

  // The diagram canvas is the first tab and always open; panels open further
  // views (a member's log) beside it. id -> { tab, view }
  const DIAGRAM_TAB = "diagram";
  const mainTabs = new Map();
  let activeMainTab = DIAGRAM_TAB;

  function mainTabButton(id, title, closable) {
    const tab = document.createElement("div");
    tab.className = "main-tab";
    tab.setAttribute("role", "tab");
    tab.dataset.tab = id;
    const label = document.createElement("span");
    label.className = "main-tab-label";
    // Marks keep a path's slashes in place under the right-to-left ellipsis.
    label.textContent = `\u200E${title}\u200E`;
    label.title = title;
    tab.appendChild(label);
    tab.addEventListener("click", () => showMainTab(id));
    if (closable) {
      const close = document.createElement("button");
      close.type = "button";
      close.className = "main-tab-close";
      close.setAttribute("aria-label", `Close ${title}`);
      close.textContent = "\u2715";
      close.addEventListener("click", (e) => {
        e.stopPropagation();
        closeMainTab(id);
      });
      tab.appendChild(close);
    }
    el("main-tabs").appendChild(tab);
    return tab;
  }

  function initMainTabs() {
    mainTabs.set(DIAGRAM_TAB, { tab: mainTabButton(DIAGRAM_TAB, "Diagram", false), view: el("canvas-container") });
    showMainTab(DIAGRAM_TAB);
  }

  // Opens (or re-shows) a view in the main area; `view` is used on first open only.
  function openMainTab(id, title, view) {
    if (!mainTabs.has(id)) {
      view.classList.add("main-view");
      el("main-views").appendChild(view);
      mainTabs.set(id, { tab: mainTabButton(id, title, true), view });
    }
    showMainTab(id);
    return mainTabs.get(id).view;
  }

  function closeMainTab(id) {
    const entry = mainTabs.get(id);
    if (!entry || id === DIAGRAM_TAB) return;
    entry.tab.remove();
    entry.view.remove();
    mainTabs.delete(id);
    SD.emit("maintabclose", { id });
    if (activeMainTab === id) showMainTab(DIAGRAM_TAB);
    else refreshMainTabStrip();
  }

  function showMainTab(id) {
    if (!mainTabs.has(id)) return;
    activeMainTab = id;
    mainTabs.forEach((entry, key) => {
      const active = key === id;
      entry.view.hidden = !active;
      entry.tab.classList.toggle("active", active);
      entry.tab.setAttribute("aria-selected", String(active));
    });
    refreshMainTabStrip();
    if (id === DIAGRAM_TAB) refitCanvas();
  }

  // The strip shows once a view besides the diagram is open.
  function refreshMainTabStrip() {
    el("main-tabs").hidden = mainTabs.size < 2;
  }

  // -- top bar ----------------------------------------------------------

  function option(value, label) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    return opt;
  }

  function bindSelect(select, key) {
    select.value = SD.store.get(key);
    select.addEventListener("change", () => SD.store.set(key, select.value));
    SD.store.onChange(key, (value) => {
      select.value = value;
    });
  }

  function initTopBar() {
    el("system-name").textContent = config.deploymentName;
    el("system-package").textContent = config.packageName;
    el("home-link").href = config.systemsIndexPath;
    if (config.systemDefinitionFile) {
      const link = SD.editor.sourceLink(config.systemDefinitionFile, 1);
      link.classList.add("topbar-source-link");
      link.title = config.systemDefinitionFile;
      link.textContent = "source";
      el("system-identity").appendChild(link);
    }

    const modeSelect = el("mode-selector");
    config.availableModes.forEach((mode) => modeSelect.appendChild(option(mode, mode)));
    bindSelect(modeSelect, "mode");

    const unitSelect = el("unit-selector");
    unitSelect.appendChild(option("", "All units"));
    config.computeUnits.forEach((unit) => {
      const label = unit.kind === "onboard" ? unit.name : `${unit.name} (${unit.kind})`;
      unitSelect.appendChild(option(unit.name, label));
    });
    el("unit-field").hidden = config.computeUnits.length === 0;
    bindSelect(unitSelect, "unit");

    const deploySelect = el("deploy-selector");
    config.deployNames.forEach((name) => deploySelect.appendChild(option(name, name)));
    el("deploy-field").hidden = config.deployNames.length === 0;
    bindSelect(deploySelect, "deploy");

    el("inspector-toggle").addEventListener("click", () => {
      setInspectorHidden(!document.body.classList.contains("inspector-hidden"));
    });
  }

  function setInspectorHidden(hidden) {
    document.body.classList.toggle("inspector-hidden", hidden);
    el("inspector-toggle").setAttribute("aria-pressed", String(!hidden));
    SD.storage.set(INSPECTOR_HIDDEN_KEY, hidden ? "1" : "");
    refitCanvas();
  }

  function initSettings() {
    const overlay = el("settings-overlay");
    const popup = el("settings-popup");
    const setOpen = (open) => {
      popup.classList.toggle("open", open);
      overlay.classList.toggle("open", open);
    };
    el("settings-btn").addEventListener("click", () => setOpen(true));
    el("settings-close").addEventListener("click", () => setOpen(false));
    overlay.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });

    const editorSelect = el("editor-selector");
    editorSelect.value = SD.editor.currentScheme();
    editorSelect.addEventListener("change", (e) => SD.editor.setScheme(e.target.value));
  }

  // -- resizers ---------------------------------------------------------

  function clampWidth(spec, value) {
    return Math.min(Math.max(value, spec.min), spec.max);
  }

  // Dragging a divider sets the width of `target`; `fromRight` measures it from the window's right edge.
  function initResizer(resizer, target, spec, fromRight) {
    const stored = parseInt(SD.storage.get(spec.key), 10);
    if (stored) target.style.width = `${clampWidth(spec, stored)}px`;
    let start = null;

    resizer.addEventListener("mousedown", (e) => {
      start = { x: e.clientX, width: target.getBoundingClientRect().width };
      resizer.classList.add("resizing");
      document.body.classList.add("resizing-panel");
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!start) return;
      const delta = fromRight ? start.x - e.clientX : e.clientX - start.x;
      target.style.width = `${clampWidth(spec, start.width + delta)}px`;
    });
    document.addEventListener("mouseup", () => {
      if (!start) return;
      start = null;
      resizer.classList.remove("resizing");
      document.body.classList.remove("resizing-panel");
      SD.storage.set(spec.key, String(parseInt(target.style.width, 10)));
      refitCanvas();
    });
  }

  // -- status bar -------------------------------------------------------

  function statusItem(id, text, className = "") {
    const item = el(id);
    item.textContent = text;
    item.className = `status-item ${className}`.trim();
    item.hidden = !text;
  }

  function renderSelectionStatus() {
    const parts = [SD.store.get("mode")];
    const unit = SD.store.get("unit");
    const deploy = SD.store.get("deploy");
    parts.push(unit || "all units");
    if (deploy) parts.push(deploy);
    statusItem("status-selection", parts.join(" · "));
  }

  function renderSourceStatus() {
    if (!daemonHealth) {
      statusItem("status-source", probeDone ? "Static export" : "Checking for daemon…");
      statusItem("status-workspace", "");
      statusItem("status-restart", "");
      return;
    }
    const version = daemonHealth.version ? ` ${daemonHealth.version}` : "";
    statusItem("status-source", `Daemon${version} · pid ${daemonHealth.pid}`);
    statusItem("status-workspace", daemonHealth.workspace ? `Workspace ${daemonHealth.workspace}` : "No build workspace");
    statusItem(
      "status-restart",
      daemonHealth.restart_required ? "Restart required: the daemon's package was rebuilt" : "",
      "status-warning",
    );
  }

  function renderJobsStatus(jobs) {
    const running = jobs.filter((job) => job.state === "running");
    statusItem(
      "status-jobs",
      running.length ? `${running.length} job${running.length > 1 ? "s" : ""} running` : "",
      "status-running",
    );
  }

  function renderConnection(connected) {
    const chip = el("connection-status");
    chip.hidden = !daemonHealth;
    chip.textContent = connected ? "Connected" : "Disconnected, retrying";
    chip.className = `connection-status ${connected ? "connected" : "disconnected"}`;
  }

  // -- startup ----------------------------------------------------------

  async function init(workbenchConfig) {
    config = workbenchConfig;
    const modes = config.availableModes;
    const unitNames = config.computeUnits.map((unit) => unit.name);
    SD.store.define("mode", config.defaultMode, (v) => modes.includes(v));
    SD.store.define("diagram", config.defaultDiagramType, (v) => v in config.diagramScripts);
    SD.store.define("unit", "", (v) => unitNames.includes(v));
    SD.store.define("deploy", config.deployNames[0] || "", (v) => config.deployNames.includes(v));
    SD.store.define("panel", DEFAULT_PANEL, (v) => panels.has(v));
    SD.store.syncUrl();

    document.title = `${config.deploymentName} - Autoware System Designer Studio`;
    initTopBar();
    initSettings();
    initMainTabs();
    initResizer(el("side-panel-resizer"), el("side-panel"), SIDE_PANEL_WIDTH, false);
    initResizer(el("inspector-resizer"), el("inspector"), INSPECTOR_WIDTH, true);
    if (SD.storage.get(INSPECTOR_HIDDEN_KEY)) setInspectorHidden(true);

    SD.store.onChange("panel", showPanel);
    ["mode", "unit", "deploy"].forEach((key) => SD.store.onChange(key, renderSelectionStatus));
    SD.on("daemon.health", (health) => {
      daemonHealth = health;
      refreshRail();
      renderSourceStatus();
    });
    SD.on("daemon.connection", ({ connected }) => renderConnection(connected));
    SD.on("daemon.jobs", renderJobsStatus);

    buildRail();
    renderSelectionStatus();
    renderSourceStatus();

    daemonHealth = await SD.daemon.probe(config.siteRoot);
    probeDone = true;
    refreshRail();
    renderSourceStatus();
    if (daemonHealth) renderConnection(SD.daemon.connected);
  }

  SD.workbench = { registerPanel, init, svgIcon, refitCanvas, openMainTab, closeMainTab, showMainTab };
})();
