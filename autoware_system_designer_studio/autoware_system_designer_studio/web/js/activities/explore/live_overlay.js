// Explore: live state of the launched members over the diagrams. The states
// come from the launch session store (one entry per launched node of this
// system, with its container's state for a composable) and reach the canvas
// through the host, which hands them to the diagram module shown. The side
// panel, shown with a daemon, carries a switch for the overlay and the mark
// legend; a subsystem, which the studio does not launch, has none. The mode's
// latency record reloads after the daemon reported a new one.
(function () {
  const SD = window.SD;
  const { element, section } = SD.widgets;
  const explore = (SD.activities.explore ||= {});

  const OVERLAY_KEY = "workbench.liveOverlay";
  // Member events arrive in bursts at launch; one apply per burst.
  const APPLY_DELAY_MS = 80;

  // `isHidden` tells whether Explore is out of view; a change while hidden
  // applies on the next `show`.
  function liveOverlay(config, store, host, isHidden = () => false) {
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
    const panel = section(
      "Live state",
      toggleRow,
      SD.RunState.legend("live-legend"),
    );
    const launchable = config.kind !== "subsystem";
    panel.hidden = !launchable || !SD.daemon.health;

    // Entities of the shown mode, composables nested under their container.
    let entities = null;
    let entitiesMode = "";
    let timer = null;
    let stale = false;

    async function loadEntities() {
      const mode = store.get("mode");
      entities = await launch.modeEntities(config.dataBase, mode);
      entitiesMode = mode;
    }

    async function apply() {
      timer = null;
      if (!entities || entitiesMode !== store.get("mode")) await loadEntities();
      host.setLiveStates(
        toggle.checked
          ? launch.memberStates(entities, config.systemId, entitiesMode)
          : new Map(),
      );
    }

    function schedule() {
      if (isHidden()) {
        stale = true;
        return;
      }
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
    }

    SD.on("launch.member", schedule);
    SD.on("launch.sessions", schedule);
    SD.on("daemon.records.changed", onRecord);
    SD.on("daemon.health", () => (panel.hidden = !launchable));
    store.onChange("mode", schedule);
    schedule();
    function show() {
      if (!stale) return;
      stale = false;
      schedule();
    }

    return { element: panel, refresh: schedule, show };
  }

  explore.liveOverlay = liveOverlay;
})();
