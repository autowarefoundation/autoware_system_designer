// Workbench shell: top bar, rail, side panel, main area, inspector and status
// bar. Each rail item is an activity owning a pair of views: its options in the
// side panel and its content in the main area, shown and hidden together.
// Daemon activities appear only on a page a daemon served, and stay disabled
// until it offers their methods.
(function () {
  const SD = window.SD;

  const SIDE_PANEL_WIDTH = { key: "workbench.sidePanelWidth", min: 180, max: 640 };
  const INSPECTOR_WIDTH = { key: "workbench.inspectorWidth", min: 220, max: 720 };
  const INSPECTOR_HIDDEN_KEY = "workbench.inspectorHidden";
  const DEFAULT_ACTIVITY = "explore";

  // id -> { def, button, side, main, handle }
  const activities = new Map();
  let config = null;
  let daemonHealth = null;
  let probeDone = false;
  let shownId = null;

  const el = (id) => document.getElementById(id);

  /**
   * Activity definition:
   *   id, title, icon (SVG path data), order
   *   daemon     needs a daemon; `requires` lists the daemon methods it calls
   *   inspector  shows the inspector beside its main view
   *   scope      selection keys besides the mode it reads, shown in the status bar
   *   mount({ side, main }, ctx) -> optional { show() }, called on first show;
   *     `show` runs on every later show, after the views are visible.
   */
  function registerActivity(def) {
    activities.set(def.id, { def, button: null, side: null, main: null, handle: null });
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

  // -- rail and activity views --------------------------------------------

  function orderedActivities() {
    return Array.from(activities.values()).sort((a, b) => a.def.order - b.def.order);
  }

  function missingMethods(def) {
    return (def.requires || []).filter((method) => !SD.daemon.offers(method));
  }

  // Static pages hide daemon activities; daemon pages disable those whose methods are missing.
  function availability(def) {
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
    orderedActivities().forEach((activity) => {
      const { def } = activity;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "rail-item";
      button.setAttribute("aria-label", def.title);
      button.appendChild(svgIcon(def.icon));
      const label = document.createElement("span");
      label.className = "rail-label";
      label.textContent = def.title;
      button.appendChild(label);
      button.addEventListener("click", () => onRailClick(def.id));
      const groupClass = def.daemon ? "rail-daemon-group" : "rail-static-group";
      rail.querySelector(`.${groupClass}`).appendChild(button);
      activity.button = button;

      activity.side = document.createElement("section");
      activity.side.className = "side-panel-section";
      activity.side.hidden = true;
      el("side-panel-body").appendChild(activity.side);

      activity.main = document.createElement("div");
      activity.main.className = "main-view";
      activity.main.hidden = true;
      el("main-area").appendChild(activity.main);
    });
  }

  function refreshRail() {
    let anyDaemonActivity = false;
    activities.forEach((activity) => {
      const { visible, enabled, reason } = availability(activity.def);
      activity.button.hidden = !visible;
      activity.button.disabled = !enabled;
      activity.button.title = reason || activity.def.title;
      if (activity.def.daemon && visible) anyDaemonActivity = true;
    });
    el("rail").querySelector(".rail-daemon-group").hidden = !anyDaemonActivity;
    const requested = activities.get(SD.store.get("activity"));
    if (!requested || !availability(requested.def).enabled) {
      // The URL may name a daemon activity before the probe answers; fall back once it is known.
      if (requested && requested.def.daemon && !probeDone) return;
      SD.store.set("activity", DEFAULT_ACTIVITY);
    }
    showActivity(SD.store.get("activity"));
  }

  function onRailClick(id) {
    const collapsed = document.body.classList.contains("side-panel-collapsed");
    if (SD.store.get("activity") === id && !collapsed) {
      setSidePanelCollapsed(true);
      return;
    }
    setSidePanelCollapsed(false);
    SD.store.set("activity", id);
  }

  function showActivity(id) {
    const activity = activities.get(id);
    if (!activity || !availability(activity.def).enabled || shownId === id) return;
    shownId = id;
    activities.forEach((other) => {
      const active = other === activity;
      other.side.hidden = !active;
      other.main.hidden = !active;
      other.button.classList.toggle("active", active);
      other.button.setAttribute("aria-pressed", String(active));
    });
    el("side-panel-title").textContent = activity.def.title;
    document.body.classList.toggle("no-inspector", !activity.def.inspector);
    el("inspector-toggle").hidden = !activity.def.inspector;
    renderSelectionStatus();
    if (!activity.handle) {
      activity.handle = activity.def.mount({ side: activity.side, main: activity.main }, context()) || {};
    } else {
      activity.handle.show?.();
    }
    refitCanvas();
  }

  function context() {
    return { config, store: SD.store, daemon: SD.daemon };
  }

  function setSidePanelCollapsed(collapsed) {
    document.body.classList.toggle("side-panel-collapsed", collapsed);
    refitCanvas();
  }

  // Diagram modules re-fit on window resize.
  function refitCanvas() {
    window.dispatchEvent(new Event("resize"));
  }

  // -- top bar ----------------------------------------------------------

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

    const modeSelect = SD.widgets.boundSelect(
      "mode",
      config.availableModes.map((mode) => [mode, mode]),
    );
    modeSelect.id = "mode-selector";
    el("mode-field").appendChild(modeSelect);

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

  // The mode, then the selection keys the shown activity reads.
  function renderSelectionStatus() {
    const scope = activities.get(shownId)?.def.scope || [];
    const parts = [SD.store.get("mode")];
    if (scope.includes("unit")) parts.push(SD.store.get("unit") || "no unit");
    if (scope.includes("deploy") && SD.store.get("deploy")) parts.push(SD.store.get("deploy"));
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
    SD.store.define("activity", DEFAULT_ACTIVITY, (v) => activities.has(v));
    SD.store.define("unit", "", (v) => unitNames.includes(v));
    SD.store.define("deploy", config.deployNames[0] || "", (v) => config.deployNames.includes(v));
    SD.store.syncUrl();

    document.title = `${config.deploymentName} - Autoware System Designer Studio`;
    initTopBar();
    initSettings();
    initResizer(el("side-panel-resizer"), el("side-panel"), SIDE_PANEL_WIDTH, false);
    initResizer(el("inspector-resizer"), el("inspector"), INSPECTOR_WIDTH, true);
    if (SD.storage.get(INSPECTOR_HIDDEN_KEY)) setInspectorHidden(true);

    SD.store.onChange("activity", showActivity);
    ["mode", "unit", "deploy"].forEach((key) => SD.store.onChange(key, renderSelectionStatus));
    SD.on("daemon.health", (health) => {
      daemonHealth = health;
      refreshRail();
      renderSourceStatus();
    });
    SD.on("daemon.connection", ({ connected }) => renderConnection(connected));
    SD.on("daemon.jobs", renderJobsStatus);

    buildRail();
    refreshRail();
    renderSourceStatus();

    daemonHealth = await SD.daemon.probe(config.siteRoot);
    probeDone = true;
    refreshRail();
    renderSourceStatus();
    if (daemonHealth) renderConnection(SD.daemon.connected);
  }

  SD.workbench = { registerActivity, init, svgIcon, refitCanvas };
})();
