// Workbench shell: the top bar, the rail and the activity registry. Each rail
// item is an activity owning one view per slot: its options in the side panel,
// its content in the main area and, when it selects elements, the inspector.
// An activity's slots show and hide together. Daemon activities appear only on
// a page a daemon served, and stay disabled until it offers their methods.
(function () {
  const SD = window.SD;

  const DEFAULT_ACTIVITY = "explore";
  const SLOT_HOSTS = {
    side: "side-panel-body",
    main: "main-area",
    inspector: "inspector-body",
  };
  const SLOT_CLASSES = {
    side: "side-panel-section",
    main: "main-view",
    inspector: "inspector-section",
  };

  // id -> { def, button, slots: { side, main, inspector? }, handle }
  const activities = new Map();
  let config = null;
  let daemonHealth = null;
  let probeDone = false;
  let shownId = null;

  const el = (id) => document.getElementById(id);

  // Parts an activity's scripts share before its entry registers it, keyed by activity id.
  SD.activities = {};

  /**
   * Activity definition:
   *   id, title, icon (SVG path data), order
   *   daemon        needs a daemon; `requires` lists the daemon methods it calls
   *   inspector     owns an inspector view beside its main view
   *   status(store) strings shown after the mode in the status bar
   *   mount(slots, ctx) -> optional { show(), hide() }, called on first show.
   *     slots: { side, main, inspector? }; ctx: { config, store, daemon }.
   *     `show` runs on every later show, after the slots are visible; `hide`
   *     runs when another activity takes over.
   */
  function registerActivity(def) {
    activities.set(def.id, { def, button: null, slots: null, handle: null });
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

  // -- rail and activity slots ------------------------------------------

  function orderedActivities() {
    return Array.from(activities.values()).sort(
      (a, b) => a.def.order - b.def.order,
    );
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
      reason: missing.length
        ? `Not offered by this daemon (needs ${missing.join(", ")})`
        : "",
    };
  }

  function slotElement(name) {
    const slot = document.createElement("section");
    slot.className = SLOT_CLASSES[name];
    slot.hidden = true;
    el(SLOT_HOSTS[name]).appendChild(slot);
    return slot;
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

      activity.slots = { side: slotElement("side"), main: slotElement("main") };
      if (def.inspector) activity.slots.inspector = slotElement("inspector");
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
    if (SD.store.get("activity") === id && !SD.layout.isSidePanelCollapsed()) {
      SD.layout.setSidePanelCollapsed(true);
      return;
    }
    SD.layout.setSidePanelCollapsed(false);
    SD.store.set("activity", id);
  }

  function showActivity(id) {
    const activity = activities.get(id);
    if (!activity || !availability(activity.def).enabled || shownId === id)
      return;
    const previous = activities.get(shownId);
    shownId = id;
    activities.forEach((other) => {
      const active = other === activity;
      Object.values(other.slots).forEach((slot) => {
        slot.hidden = !active;
      });
      other.button.classList.toggle("active", active);
      other.button.setAttribute("aria-pressed", String(active));
    });
    el("side-panel-title").textContent = activity.def.title;
    SD.layout.setInspectorAvailable(!!activity.def.inspector);
    renderSelectionStatus();
    previous?.handle?.hide?.();
    if (!activity.handle) {
      activity.handle = activity.def.mount(activity.slots, context()) || {};
    } else {
      activity.handle.show?.();
    }
  }

  function context() {
    return { config, store: SD.store, daemon: SD.daemon };
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
    editorSelect.addEventListener("change", (e) =>
      SD.editor.setScheme(e.target.value),
    );
  }

  function renderConnection(connected) {
    const chip = el("connection-status");
    chip.hidden = !daemonHealth;
    chip.textContent = connected ? "Connected" : "Disconnected, retrying";
    chip.className = `connection-status ${connected ? "connected" : "disconnected"}`;
  }

  // The mode, then what the shown activity reports.
  function renderSelectionStatus() {
    const status = activities.get(shownId)?.def.status;
    SD.statusbar.selection([
      SD.store.get("mode"),
      ...(status ? status(SD.store) : []),
    ]);
  }

  // -- startup ----------------------------------------------------------

  async function init(workbenchConfig) {
    config = workbenchConfig;
    const modes = config.availableModes;
    const unitNames = config.computeUnits.map((unit) => unit.name);
    SD.store.define("mode", config.defaultMode, (v) => modes.includes(v));
    SD.store.define("activity", DEFAULT_ACTIVITY, (v) => activities.has(v));
    SD.store.define("unit", "", (v) => unitNames.includes(v));
    SD.store.define("deploy", config.deployNames[0] || "", (v) =>
      config.deployNames.includes(v),
    );
    // Selected node or module, by entity key, shared by the activities.
    SD.store.define("entity", "");
    // Selected topic, by name, shared by the activities.
    SD.store.define("topic", "");
    SD.store.syncUrl();

    document.title = `${config.deploymentName} - Autoware System Designer Studio`;
    initTopBar();
    initSettings();
    SD.layout.init();

    SD.store.onChange("activity", showActivity);
    ["mode", "unit", "deploy"].forEach((key) =>
      SD.store.onChange(key, renderSelectionStatus),
    );
    SD.on("daemon.health", (health) => {
      daemonHealth = health;
      refreshRail();
      SD.statusbar.source(daemonHealth, probeDone);
    });
    SD.on("daemon.connection", ({ connected }) => renderConnection(connected));
    SD.on("daemon.jobs", SD.statusbar.jobs);

    buildRail();
    refreshRail();
    SD.statusbar.source(daemonHealth, probeDone);

    daemonHealth = await SD.daemon.probe(config.siteRoot);
    probeDone = true;
    refreshRail();
    SD.statusbar.source(daemonHealth, probeDone);
    if (daemonHealth) renderConnection(SD.daemon.connected);
  }

  SD.workbench = { registerActivity, init, svgIcon };
})();
