// Launch activity: launch options and per-unit launch controls beside the
// launch commands table of the mode and deploy, or the state monitor of the
// launched system when the daemon reports one.
(function () {
  const SD = window.SD;
  const { element, field, section, boundSelect, segmented, mainView } = SD.widgets;
  const { commandsTable, stateMonitor } = SD.activities.launch;

  const METHOD_KEY = "workbench.launchMethod";
  const METHODS = [
    ["native", "Runtime"],
    ["xml", "XML"],
  ];
  const METHOD_NOTES = {
    native:
      "Runs the system structure under the designer runtime supervisor, one supervised process per node.",
    xml: "Runs the exported XML launcher through ros2 launch.",
  };
  const VIEWS = ["commands", "monitor"];

  function launchRows(config, store) {
    const mode = store.get("mode");
    const deploy = store.get("deploy");
    return config.launchCommands.filter((r) => r.mode === mode && r.deploy === deploy);
  }

  SD.workbench.registerActivity({
    id: "launch",
    title: "Launch",
    order: 20,
    daemon: false,
    inspector: false,
    icon: "M6 4l10 6-10 6z",
    status: (store) => [store.get("deploy")],
    mount({ side, main }, { config, store, daemon }) {
      // An empty view selection follows the daemon: the monitor when it reports launch state.
      store.define("view", "", (v) => VIEWS.includes(v));
      store.syncUrl();
      const hasNative = config.launchCommands.some((record) => record.native);
      let method = SD.storage.get(METHOD_KEY);
      if (method !== "xml" && !(method === "native" && hasNative))
        method = hasNative ? "native" : "xml";
      let unitStatus = new Map();

      // -- side: options and launch controls
      if (config.deployNames.length) {
        side.appendChild(
          field(
            "Deploy",
            boundSelect(
              "deploy",
              config.deployNames.map((name) => [name, name]),
            ),
          ),
        );
      }
      const methodSwitch = segmented(
        METHODS.filter(([value]) => value !== "native" || hasNative),
        (value) => {
          method = value;
          SD.storage.set(METHOD_KEY, value);
          render();
        },
      );
      const methodRow = element("div", "panel-row");
      methodRow.append(element("span", "panel-row-label", "Command"), methodSwitch.element);
      const methodNote = element("p", "panel-note");
      side.append(methodRow, methodNote);

      const unitList = element("div", "launch-list");
      const units = section("Compute units", unitList);
      side.appendChild(units);

      // -- main: commands table or state monitor
      const view = mainView();
      const viewSwitch = segmented(
        [
          ["monitor", "Monitor"],
          ["commands", "Commands"],
        ],
        (value) => store.set("view", value),
      );
      view.trailing.appendChild(viewSwitch.element);
      const commandsPane = element("div", "pane launch-commands");
      const monitorPane = element("div", "split-h");
      view.body.append(commandsPane, monitorPane);
      main.appendChild(view.element);
      const table = commandsTable(commandsPane);
      let monitor = null;

      function canMonitor() {
        return daemon.offers("launch.status");
      }

      function shownView() {
        if (!canMonitor()) return "commands";
        return store.get("view") || "monitor";
      }

      function renderUnits(rows) {
        const canLaunch = daemon.offers("launch.start");
        units.hidden = !canLaunch;
        unitList.replaceChildren();
        if (!canLaunch) return;
        rows.forEach((row) => {
          const card = element("div", "launch-card");
          const status = unitStatus.get(row.unit);
          const head = element("div", "launch-card-head");
          head.appendChild(element("span", "launch-unit", row.unit));
          if (status)
            head.appendChild(element("span", `unit-status ${status}`, status.replace(/_/g, " ")));
          const button = element("button", "panel-button primary", "Launch");
          button.type = "button";
          button.disabled = status !== "available";
          button.addEventListener("click", () => {
            const params = { system: config.systemId, mode: row.mode, compute_unit: row.unit };
            if (row.deploy) params.deploy = row.deploy;
            button.disabled = true;
            daemon
              .rpc("launch.start", params)
              .then(() => store.set("view", "monitor"))
              .catch((error) => {
                button.disabled = false;
                button.title = error.message;
              });
          });
          card.append(head, button);
          unitList.appendChild(card);
        });
      }

      function render() {
        methodSwitch.mark(method);
        methodNote.textContent = METHOD_NOTES[method];
        const rows = launchRows(config, store);
        const mode = store.get("mode");
        const deploy = store.get("deploy");
        renderUnits(rows);

        const current = shownView();
        viewSwitch.element.hidden = !canMonitor();
        viewSwitch.mark(current);
        commandsPane.hidden = current !== "commands";
        monitorPane.hidden = current !== "monitor";
        if (current === "monitor") {
          view.setTitle("State monitor");
          view.setEmpty("");
          if (!monitor) monitor = stateMonitor(monitorPane, config, store);
        } else {
          view.setTitle(`Launch commands · ${mode}${deploy ? ` · ${deploy}` : ""}`);
          view.setEmpty(rows.length ? "" : `No launchers for mode ${mode}.`);
          table.render(rows, method, unitStatus);
        }
      }

      async function loadUnitStatus() {
        try {
          const system = await daemon.system(config.systemId);
          unitStatus = new Map((system?.compute_units || []).map((u) => [u.name, u.status]));
        } catch (error) {
          console.warn("launch:", error);
        }
        render();
      }

      ["mode", "deploy", "view"].forEach((key) => store.onChange(key, render));
      SD.on("daemon.health", loadUnitStatus);
      SD.on("daemon.systems.changed", loadUnitStatus);
      if (daemon.health) loadUnitStatus();
      render();
      return {
        show: () => monitor?.show(),
        hide: () => monitor?.hide(),
      };
    },
  });
})();
