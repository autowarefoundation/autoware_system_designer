// Launch activity: launch options and per-unit launch controls beside the
// state monitor of the mode; the launch commands of the mode and deploy open
// in a popup from the side panel, which also starts RViz on the daemon.
(function () {
  const SD = window.SD;
  const { element, field, section, boundSelect, segmented, mainView, popup } =
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
  const COMMANDS_ICON =
    "M2 4a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4zm2 1v10h12V5H4zm2.3 2.3L9 10l-2.7 2.7-1-1L7 10 5.3 8.3l1-1zM10 12h4v1.5h-4V12z";
  const RVIZ_ICON =
    "M10 4C5.5 4 2 10 2 10s3.5 6 8 6 8-6 8-6-3.5-6-8-6zm0 9.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7zM10 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4z";
  const RVIZ_KIND = "rviz";
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
      const hasNative = config.launchCommands.some((record) => record.native);
      let method = SD.storage.get(METHOD_KEY);
      if (method !== "xml" && !(method === "native" && hasNative))
        method = hasNative ? "native" : "xml";
      let unitStatus = new Map();
      const { sessions } = SD.activities.launch;
      // Per unit: the last error of a control, and the last measurement result.
      const errors = new Map();
      const measures = new Map();

      // -- side: options, launch controls and the commands popup
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

      const commands = popup("Launch commands");
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
      const commandsPane = element("div", "launch-commands");
      commands.body.append(methodRow, methodNote, commandsPane);
      const table = commandsTable(commandsPane);
      const commandsButton = button(
        "Launch commands",
        "panel-icon-button",
        () => commands.open(),
      );
      commandsButton.prepend(SD.workbench.svgIcon(COMMANDS_ICON));
      const rvizButton = button("Launch RViz", "panel-icon-button", () => {
        rvizError.hidden = true;
        rvizButton.disabled = true;
        daemon
          .rpc("rviz.start")
          .then((job) => daemon.upsertJob(job))
          .catch((error) => {
            rvizError.textContent = error.message;
            rvizError.hidden = false;
          })
          .then(renderRviz);
      });
      rvizButton.prepend(SD.workbench.svgIcon(RVIZ_ICON));
      const rvizError = element("div", "launch-error");
      rvizError.hidden = true;
      side.append(
        options,
        units,
        section("Commands", commandsButton, rvizButton, rvizError),
      );

      // -- main: the state monitor
      const view = mainView({ title: "State monitor" });
      const monitorPane = element("div", "split-h");
      view.body.appendChild(monitorPane);
      main.appendChild(view.element);
      const monitor = stateMonitor(monitorPane, config, store);

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

      // Launch and Shutdown are always offered; the one that applies is coloured.
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
        const active = !!session && session.state !== "exited";
        const canLaunch = status === "available" && !active;
        const launch = button("Launch", canLaunch ? "primary" : "", () => {
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
            .catch((error) => errors.set(row.unit, error.message))
            .then(render);
        });
        launch.disabled = !canLaunch;
        const canShutdown = active && !session.shutting_down;
        const shutdown = button("Shutdown", canShutdown ? "danger" : "", () =>
          control(row.unit, "launch.shutdown", {}),
        );
        shutdown.disabled = !canShutdown;
        actions.append(launch, shutdown);
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
        card.appendChild(actions);
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

      // RViz is one job per daemon; the button waits while it runs.
      function renderRviz() {
        const offered = daemon.offers("rviz.start");
        rvizButton.hidden = !offered;
        if (!offered) {
          rvizError.hidden = true;
          return;
        }
        const running = daemon
          .jobs()
          .some((job) => job.kind === RVIZ_KIND && job.state === "running");
        rvizButton.disabled = running;
        const rvizConfig = daemon.health?.launch?.rviz_config;
        rvizButton.title = running
          ? "RViz is running"
          : rvizConfig
            ? `rviz2 -d ${rvizConfig}`
            : "rviz2";
      }

      function render() {
        const rows = launchRows(config, store);
        const mode = store.get("mode");
        const deploy = store.get("deploy");
        renderUnits(rows);
        methodSwitch.mark(method);
        methodNote.textContent = METHOD_NOTES[method];
        commandsPane.replaceChildren();
        if (rows.length) commandsPane.appendChild(table.element);
        else
          commandsPane.appendChild(
            element("p", "main-empty", `No launchers for mode ${mode}.`),
          );
        table.render(rows, method, unitStatus);
        commandsButton.title = `Launch commands · ${mode}${deploy ? ` · ${deploy}` : ""}`;
        renderRviz();
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

      ["mode", "deploy"].forEach((key) => store.onChange(key, render));
      SD.on("daemon.health", loadUnitStatus);
      SD.on("daemon.systems.changed", loadUnitStatus);
      SD.on("launch.sessions", onSessions);
      SD.on("daemon.jobs", renderRviz);
      if (daemon.health) loadUnitStatus();
      render();
      return {
        show: () => monitor.show(),
        hide: () => {
          monitor.hide();
          commands.close();
        },
      };
    },
  });
})();
