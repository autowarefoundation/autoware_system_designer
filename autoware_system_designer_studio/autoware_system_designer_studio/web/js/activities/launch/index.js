// Launch activity: launch options and per-unit launch controls beside the
// launch commands table of the mode and deploy, or the state monitor of the
// launched system when the daemon reports one.
(function () {
  const SD = window.SD;
  const { element, field, section, boundSelect, segmented, mainView } =
    SD.widgets;
  const { commandsTable, stateMonitor, sessionLabel } = SD.activities.launch;

  const METHOD_KEY = "workbench.launchMethod";
  const MEASURE_KEY = "workbench.launchMeasure";
  const RESPAWN_KEY = "workbench.launchRespawn";
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
  // Measurement window states in which the next verb is stop.
  const MEASURE_OPEN = ["running"];

  function launchRows(config, store) {
    const mode = store.get("mode");
    const deploy = store.get("deploy");
    return config.launchCommands.filter(
      (r) => r.mode === mode && r.deploy === deploy,
    );
  }

  function checkbox(label, key, fallback) {
    const wrapper = element("label", "panel-check");
    const input = element("input");
    input.type = "checkbox";
    input.checked = (SD.storage.get(key) ?? String(fallback)) === "true";
    input.addEventListener("change", () =>
      SD.storage.set(key, String(input.checked)),
    );
    wrapper.append(input, element("span", "", label));
    return { element: wrapper, input };
  }

  function button(label, className, onClick) {
    const node = element("button", `panel-button ${className}`.trim(), label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
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
      const { sessions } = SD.activities.launch;
      // Per unit: the last error of a control, and the last measurement result.
      const errors = new Map();
      const measures = new Map();

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
      methodRow.append(
        element("span", "panel-row-label", "Command"),
        methodSwitch.element,
      );
      const methodNote = element("p", "panel-note");
      side.append(methodRow, methodNote);

      const measureOption = checkbox(
        "Arm latency measurement",
        MEASURE_KEY,
        false,
      );
      const respawnOption = checkbox(
        "Respawn exited processes",
        RESPAWN_KEY,
        false,
      );
      const options = section(
        "Launch options",
        measureOption.element,
        respawnOption.element,
      );
      const unitList = element("div", "launch-list");
      const units = section("Compute units", unitList);
      side.append(options, units);

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

      function control(unit, method, params, then) {
        errors.delete(unit);
        return daemon
          .rpc(method, { compute_unit: unit, ...params })
          .then((result) => {
            then?.(result);
            render();
          })
          .catch((error) => {
            errors.set(unit, error.message);
            render();
          });
      }

      function launchCard(row, status, session) {
        const card = element("div", "launch-card");
        const head = element("div", "launch-card-head");
        head.appendChild(element("span", "launch-unit", row.unit));
        if (status)
          head.appendChild(
            element("span", `unit-status ${status}`, status.replace(/_/g, " ")),
          );
        card.appendChild(head);
        if (session)
          card.appendChild(
            element(
              "div",
              `launch-session ${session.state}`,
              sessionLabel(session),
            ),
          );
        const actions = element("div", "launch-actions");
        const active = session && session.state !== "exited";
        if (status === "available") {
          actions.appendChild(
            button("Launch", "primary", () => {
              const params = {
                system: config.systemId,
                mode: row.mode,
                compute_unit: row.unit,
                measure: measureOption.input.checked,
                respawn: respawnOption.input.checked,
              };
              if (row.deploy) params.deploy = row.deploy;
              errors.delete(row.unit);
              daemon
                .rpc("launch.start", params)
                .then(() => store.set("view", "monitor"))
                .catch((error) => errors.set(row.unit, error.message))
                .then(render);
            }),
          );
        }
        if (active) {
          const shutdown = button("Shutdown", "", () =>
            control(row.unit, "launch.shutdown", {}),
          );
          shutdown.disabled = !!session.shutting_down;
          actions.appendChild(shutdown);
        }
        if (active && session.connected && session.control.measure_armed) {
          const measure = measures.get(row.unit);
          const open = measure && MEASURE_OPEN.includes(measure.state);
          actions.appendChild(
            button(open ? "Stop measure" : "Start measure", "", () =>
              control(
                row.unit,
                open ? "measure.stop" : "measure.start",
                {},
                (result) => measures.set(row.unit, result),
              ),
            ),
          );
          if (measure)
            card.appendChild(
              element("div", "launch-measure", `measure: ${measure.message}`),
            );
        }
        if (actions.childElementCount) card.appendChild(actions);
        const error = errors.get(row.unit);
        if (error) card.appendChild(element("div", "launch-error", error));
        return card;
      }

      function renderUnits(rows) {
        const canLaunch = daemon.offers("launch.start");
        units.hidden = !canLaunch;
        options.hidden = !canLaunch;
        unitList.replaceChildren();
        if (!canLaunch) return;
        rows.forEach((row) => {
          const session = sessions.get(row.unit);
          const forThisSystem =
            session &&
            session.system === config.systemId &&
            session.mode === row.mode;
          unitList.appendChild(
            launchCard(
              row,
              unitStatus.get(row.unit),
              forThisSystem ? session : null,
            ),
          );
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
          view.setTitle(
            `Launch commands · ${mode}${deploy ? ` · ${deploy}` : ""}`,
          );
          view.setEmpty(rows.length ? "" : `No launchers for mode ${mode}.`);
          table.render(rows, method, unitStatus);
        }
      }

      async function loadUnitStatus() {
        try {
          const system = await daemon.system(config.systemId);
          unitStatus = new Map(
            (system?.compute_units || []).map((u) => [u.name, u.status]),
          );
        } catch (error) {
          console.warn("launch:", error);
        }
        render();
      }

      function onSessions() {
        sessions.forEach((session, unit) => {
          if (session.state === "exited") measures.delete(unit);
          else if (
            session.connected &&
            session.control.measure_armed &&
            !measures.has(unit)
          ) {
            measures.set(unit, { state: "armed", message: "armed" });
            control(unit, "measure.status", {}, (result) =>
              measures.set(unit, result),
            );
          }
        });
        loadUnitStatus();
      }

      ["mode", "deploy", "view"].forEach((key) => store.onChange(key, render));
      SD.on("daemon.health", loadUnitStatus);
      SD.on("daemon.systems.changed", loadUnitStatus);
      SD.on("launch.sessions", onSessions);
      if (daemon.health) loadUnitStatus();
      render();
      return {
        show: () => monitor?.show(),
        hide: () => monitor?.hide(),
      };
    },
  });
})();
