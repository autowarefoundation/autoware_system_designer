// Explore: live state of the launched members over the diagrams. The states
// come from the launch session store (one entry per launched node of this
// system, with its container's state for a composable) and reach the canvas
// through the host, which hands them to the diagram module shown. The side
// panel sums up the system's sessions, carries the mark legend and a switch
// for the overlay, and notes when the mode's latency record was reloaded
// after the daemon reported a new one.
(function () {
  const SD = window.SD;
  const { element, section } = SD.widgets;
  const explore = (SD.activities.explore ||= {});

  const OVERLAY_KEY = "workbench.liveOverlay";
  // Member events arrive in bursts at launch; one apply per burst.
  const APPLY_DELAY_MS = 80;

  function clock(seconds) {
    const d = new Date(seconds * 1000);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function liveOverlay(config, store, host) {
    const launch = SD.activities.launch;
    const toggleRow = element("label", "panel-check");
    const toggle = element("input");
    toggle.type = "checkbox";
    toggle.checked = (SD.storage.get(OVERLAY_KEY) ?? "true") === "true";
    toggle.addEventListener("change", () => {
      SD.storage.set(OVERLAY_KEY, String(toggle.checked));
      schedule();
    });
    toggleRow.append(toggle, element("span", "", "Show on diagrams"));
    const summary = element("div", "live-sessions");
    const legend = SD.RunState.legend("live-legend");
    const latencyNote = element("p", "panel-note live-latency");
    latencyNote.hidden = true;
    const panel = section(
      "Live state",
      toggleRow,
      summary,
      legend,
      latencyNote,
    );
    panel.hidden = !SD.daemon.health;

    // Entities of the shown mode, composables nested under their container.
    let entities = null;
    let entitiesMode = "";
    let timer = null;

    async function loadEntities() {
      const mode = store.get("mode");
      const data = await SD.NamespaceTree.loadStructure(config.dataBase, mode);
      entities = launch.nestComposables(SD.NamespaceTree.entities(data));
      entitiesMode = mode;
    }

    function sessionsOfSystem() {
      return Array.from(launch.sessions.values())
        .filter((session) => session.system === config.systemId)
        .sort(
          (a, b) =>
            a.compute_unit.localeCompare(b.compute_unit) ||
            a.mode.localeCompare(b.mode),
        );
    }

    // One row per session of this system: its unit, mode, state and member counts.
    function renderSummary() {
      summary.replaceChildren();
      const sessions = sessionsOfSystem();
      if (!sessions.length) {
        summary.appendChild(
          element("p", "panel-note", "No launch of this system."),
        );
        return;
      }
      sessions.forEach((session) => {
        const row = element("div", "live-session");
        row.append(
          SD.RunState.mark(launch.sessionToken(session)),
          element(
            "span",
            "live-session-unit",
            `${session.compute_unit} · ${session.mode}`,
          ),
          element("span", "live-session-state", launch.sessionLabel(session)),
        );
        summary.appendChild(row);
      });
    }

    async function apply() {
      timer = null;
      if (!entities || entitiesMode !== store.get("mode")) await loadEntities();
      const states = toggle.checked
        ? launch.memberStates(entities, config.systemId, entitiesMode)
        : new Map();
      host.setLiveStates(states);
      renderSummary();
    }

    function schedule() {
      if (timer) return;
      timer = setTimeout(() => {
        apply().catch((error) => console.warn("live overlay:", error));
      }, APPLY_DELAY_MS);
    }

    // A new record of the shown mode replaces the loaded measurement; a mode
    // that had none now has latency for the diagrams loaded later.
    function onRecord(change) {
      if (change.system !== config.systemId) return;
      if (!config.latencyModes.includes(change.mode))
        config.latencyModes.push(change.mode);
      if (change.mode !== store.get("mode")) return;
      host.reloadMeasurement();
      latencyNote.textContent = `Latency record updated at ${clock(Date.now() / 1000)}.`;
      latencyNote.hidden = false;
    }

    SD.on("launch.member", schedule);
    SD.on("launch.sessions", schedule);
    SD.on("daemon.records.changed", onRecord);
    SD.on("daemon.connection", ({ connected }) => {
      panel.hidden = !connected && !SD.daemon.health;
    });
    store.onChange("mode", () => {
      latencyNote.hidden = true;
      schedule();
    });
    schedule();
    return { element: panel, refresh: schedule };
  }

  explore.liveOverlay = liveOverlay;
})();
