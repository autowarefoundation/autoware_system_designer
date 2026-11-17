// Workbench shell: the top bar, the rail and the activity registry. An
// activity owns one view per slot: its options in the side panel, its content
// in the main area and, when it selects elements, the inspector; its slots
// show and hide together. The rail lists every view of every activity in
// captioned groups. An activity may also own a part of the top bar, mounted
// at startup and shown whatever activity is. Daemon activities appear only on
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

  // id -> { def, views, buttons, slots: { side, main, inspector? }, handle }
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
   *   group         caption of its rail group
   *   views(config) its main views as rail items: { key, fallback, items:
   *                 [{ value, label, title, icon, order?, group? }] }, the shown
   *                 one held by the store key `key`; an item's `order` and
   *                 `group` place it apart from its activity's. Without views
   *                 the activity is one rail item
   *   daemon        needs a daemon; `requires` lists the daemon methods it calls
   *   side          false for an activity without side-panel options
   *   inspector     owns an inspector view beside its main view
   *   status(store) strings shown after the mode in the status bar
   *   bar(slots, ctx) fills its part of the top bar at startup.
   *     slots: { bar, drawer }, the drawer a popover below the bar.
   *   mount(slots, ctx) -> optional { show(), hide() }, called on first show.
   *     slots: { side, main, inspector? }; ctx: { config, store, daemon }.
   *     `show` runs on every later show, after the slots are visible; `hide`
   *     runs when another activity takes over.
   */
  function registerActivity(def) {
    activities.set(def.id, {
      def,
      views: null,
      buttons: [],
      slots: null,
      handle: null,
    });
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

  function railItem(activity, item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rail-item";
    button.dataset.value = item.value;
    button.dataset.title = item.title || item.label;
    button.setAttribute("aria-label", button.dataset.title);
    button.appendChild(svgIcon(item.icon));
    const label = document.createElement("span");
    label.className = "rail-label";
    label.textContent = item.label;
    button.appendChild(label);
    button.addEventListener("click", () => onRailClick(activity, item.value));
    return button;
  }

  // Rail items in rail order, each view at its own order and under its own
  // group caption when it names one, else at its activity's.
  function railEntries() {
    const entries = [];
    activities.forEach((activity) => {
      const { def } = activity;
      const items = activity.views
        ? activity.views.items
        : [{ value: "", label: def.title, icon: def.icon }];
      items.forEach((item) =>
        entries.push({
          activity,
          item,
          order: item.order ?? def.order,
          caption: item.group ?? def.group ?? "",
        }),
      );
    });
    return entries.sort((a, b) => a.order - b.order);
  }

  function railGroup(caption) {
    const group = document.createElement("div");
    group.className = "rail-group";
    if (caption) {
      const label = document.createElement("span");
      label.className = "rail-caption";
      label.textContent = caption;
      group.appendChild(label);
    }
    return group;
  }

  // Neighbouring items of one caption share a group, those without a
  // caption only within one activity. A group hides with all its items.
  const railGroups = [];

  function buildRail() {
    const rail = el("rail");
    let last = null;
    let group = null;
    railEntries().forEach((entry) => {
      const { activity, item, caption } = entry;
      const joins =
        last &&
        last.caption === caption &&
        (caption || last.activity === activity);
      if (!joins) {
        group = railGroup(caption);
        rail.appendChild(group);
        railGroups.push(group);
      }
      last = entry;
      const button = railItem(activity, item);
      group.appendChild(button);
      activity.buttons.push(button);
    });
    activities.forEach((activity) => {
      activity.slots = { side: slotElement("side"), main: slotElement("main") };
      if (activity.def.inspector)
        activity.slots.inspector = slotElement("inspector");
    });
  }

  // The rail item of the activity's shown view.
  function shownItem(activity) {
    if (!activity.views) return activity.buttons[0];
    const value = SD.store.get(activity.views.key);
    return activity.buttons.find((button) => button.dataset.value === value);
  }

  function markRail() {
    const shown = activities.get(shownId);
    const current = shown && shownItem(shown);
    activities.forEach((activity) => {
      activity.buttons.forEach((button) => {
        const active = button === current;
        button.classList.toggle("active", active);
        button.setAttribute("aria-pressed", String(active));
      });
    });
    if (shown)
      el("side-panel-title").textContent =
        current?.dataset.title || shown.def.title;
  }

  function refreshRail() {
    activities.forEach((activity) => {
      const { visible, enabled, reason } = availability(activity.def);
      activity.buttons.forEach((button) => {
        button.hidden = !visible;
        button.disabled = !enabled;
        button.title = reason || button.dataset.title;
      });
    });
    railGroups.forEach((group) => {
      group.hidden = ![...group.querySelectorAll(".rail-item")].some(
        (button) => !button.hidden,
      );
    });
    const requested = activities.get(SD.store.get("activity"));
    if (!requested || !availability(requested.def).enabled) {
      // The URL may name a daemon activity before the probe answers; fall back once it is known.
      if (requested && requested.def.daemon && !probeDone) return;
      SD.store.set("activity", DEFAULT_ACTIVITY);
    }
    showActivity(SD.store.get("activity"));
  }

  // A click on the shown view folds its side panel; any other shows that view.
  function onRailClick(activity, value) {
    const { def, views } = activity;
    const shown =
      SD.store.get("activity") === def.id &&
      (!views || SD.store.get(views.key) === value);
    if (shown) {
      if (def.side !== false)
        SD.layout.setSidePanelCollapsed(!SD.layout.isSidePanelCollapsed());
      return;
    }
    SD.layout.setSidePanelCollapsed(false);
    if (views) SD.store.set(views.key, value);
    SD.store.set("activity", def.id);
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
    });
    markRail();
    SD.layout.setSidePanelAvailable(activity.def.side !== false);
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
    // The top bar grows as its parts wrap; its popovers hang below its height.
    const topbar = el("topbar");
    new ResizeObserver(() => {
      document.documentElement.style.setProperty(
        "--topbar-height",
        `${topbar.getBoundingClientRect().height}px`,
      );
    }).observe(topbar);
    SD.systemMenu.init(config);
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

    const slots = {
      bar: el("topbar-activity-bar"),
      drawer: el("topbar-drawer"),
    };
    orderedActivities().forEach((activity) =>
      activity.def.bar?.(slots, context()),
    );
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

    initStorageSettings();
  }

  // The daemon's log and config roots, each chosen with a path selector or
  // set back to its default; the subdirectories each function writes show
  // below them.
  function initStorageSettings() {
    const section = el("settings-storage");
    const shown = {
      log_root: el("settings-log-root"),
      config_root: el("settings-config-root"),
    };
    const labels = { log_root: "Log root", config_root: "Config root" };
    const dirs = el("settings-storage-dirs");
    const error = el("settings-storage-error");
    let storage = null;

    function show(next) {
      storage = next;
      section.hidden = !storage || !SD.daemon.offers("settings.set");
      if (section.hidden) return;
      Object.entries(shown).forEach(([name, code]) => {
        code.textContent = storage[name];
        code.title = storage.set[name] ? "Set" : "Default";
        code.classList.toggle("default", !storage.set[name]);
        section.querySelector(`[data-reset="${name}"]`).disabled =
          !storage.set[name];
      });
      const d = storage.dirs;
      dirs.replaceChildren(
        ...[
          ["Launch logs", d.launch_logs],
          ["Replay runs", d.replay_logs],
          [storage.records_fixed ? "Records (fixed)" : "Records", d.records],
          ["Replay setups", d.replay_setups],
        ].map(([label, path]) => {
          const row = document.createElement("div");
          const name = document.createElement("span");
          name.textContent = label;
          const value = document.createElement("code");
          value.textContent = path;
          row.append(name, value);
          return row;
        }),
      );
    }

    function set(name, value) {
      SD.daemon.rpc("settings.set", { [name]: value }).then(
        (next) => {
          error.hidden = true;
          show(next);
          SD.emit("settings.storage", next);
        },
        (err) => {
          error.textContent = err.message;
          error.hidden = false;
        },
      );
    }

    Object.keys(shown).forEach((name) => {
      const picker = SD.widgets.pathPicker({ title: labels[name] });
      section
        .querySelector(`[data-browse="${name}"]`)
        .addEventListener("click", () =>
          picker.open(storage?.[name] || "", (path) => set(name, path)),
        );
      section
        .querySelector(`[data-reset="${name}"]`)
        .addEventListener("click", () => set(name, ""));
    });
    SD.on("daemon.health", (health) => show(health?.storage));
    show(SD.daemon.health?.storage || null);
  }

  function renderConnection(connected) {
    const chip = el("connection-status");
    chip.hidden = !daemonHealth;
    chip.textContent = connected ? "Connected" : "Disconnected, retrying";
    chip.className = `connection-status ${connected ? "connected" : "disconnected"}`;
  }

  // The mode and deploy, then what the shown activity reports.
  function renderSelectionStatus() {
    const status = activities.get(shownId)?.def.status;
    SD.statusbar.selection([
      SD.store.get("mode"),
      SD.store.get("deploy"),
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
    activities.forEach((activity) => {
      const views = activity.def.views?.(config);
      if (!views) return;
      activity.views = views;
      SD.store.define(views.key, views.fallback, (v) =>
        views.items.some((item) => item.value === v),
      );
      SD.store.onChange(views.key, markRail);
    });
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
