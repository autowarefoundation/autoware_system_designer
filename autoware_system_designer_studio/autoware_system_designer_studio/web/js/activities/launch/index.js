// Launch activity: two views of the mode, chosen in the side panel. Control
// is the state map, with launch and shutdown of each compute unit in its
// section head; Logs is the member tree beside the selected member's log.
// The side panel also holds the deploy, the launch options, the launch
// commands of the mode and deploy in a popup, and the RViz start on the daemon.
(function () {
  const SD = window.SD;
  const {
    element,
    field,
    section,
    list,
    boundSelect,
    segmented,
    mainView,
    popup,
  } = SD.widgets;
  const { commandsTable, stateMonitor, stateMap, sessionLabel } =
    SD.activities.launch;

  // Main views by the store's `view`.
  const VIEWS = [
    ["control", "Control"],
    ["logs", "Logs"],
  ];
  const DEFAULT_VIEW = "control";

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

      store.define("view", DEFAULT_VIEW, (v) =>
        VIEWS.some(([value]) => value === v),
      );
      store.syncUrl();

      // -- side: the view list, options and the commands popup
      const views = list(VIEWS, (value) => store.set("view", value));
      side.appendChild(section("View", views.element));
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
        section("Commands", commandsButton, rvizButton, rvizError),
      );

      // -- main: the state map with the unit controls, and the member logs
      const controlView = mainView({ title: "Control" });
      const legend = SD.RunState.legend("control-legend");
      const { rateLabel, BUSY_LOG_BPS } = SD.activities.launch;
      [
        [
          element("span", "log-busy"),
          "busy log",
          `output growing by ${rateLabel(BUSY_LOG_BPS)} or more`,
        ],
        [
          element("span", "restart-glyph", "\u21bbn"),
          "restarts",
          "times the member was restarted or respawned",
        ],
      ].forEach(([glyph, label, meaning]) => {
        const item = element("span", "state-legend-item");
        item.title = meaning;
        item.append(glyph, element("span", "", label));
        legend.appendChild(item);
      });
      controlView.trailing.appendChild(legend);
      const undrawnUnits = element("div", "control-undrawn-units");
      controlView.body.appendChild(undrawnUnits);
      const map = stateMap(controlView.body, config, store, {
        unitControls,
        onOpen: () => store.set("view", "logs"),
        onLoad: () => render(),
      });
      const logsView = mainView({ title: "Logs" });
      const monitorPane = element("div", "split-h");
      logsView.body.appendChild(monitorPane);
      main.append(controlView.element, logsView.element);
      const monitor = stateMonitor(monitorPane, config, store);
      logsView.head.insertBefore(monitor.runSelector, logsView.trailing);
      // The activity's slots are visible from mount until another activity takes over.
      let shown = true;

      // Shows the store's view; the other one stops following and laying out.
      function showView() {
        const current = store.get("view");
        views.mark(current);
        controlView.element.hidden = current !== "control";
        logsView.element.hidden = current !== "logs";
        if (shown && current === "control") map.show();
        else map.hide();
        if (shown && current === "logs") monitor.show();
        else monitor.hide();
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

      // Controls of `unit` for its section head on the map: Launch and Shutdown,
      // always offered with the one that applies coloured, the measurement
      // window of an armed session, then the session or the unit's reach.
      // Null for a unit without a launcher of the mode and deploy, or a daemon
      // that launches nothing.
      function unitControls(unit) {
        if (!daemon.offers("launch.start")) return null;
        const row = launchRows(config, store).find((r) => r.unit === unit);
        if (!row) return null;
        const status = unitStatus.get(unit);
        const known = sessions.get(unit);
        const session =
          known && known.system === config.systemId && known.mode === row.mode
            ? known
            : null;
        const controls = element("div", "unit-controls");
        const active = !!session && session.state !== "exited";
        // The unit runs one launch at a time: a live session of another mode or system holds it.
        const holder = known && !session && known.state !== "exited" ? known : null;
        const canLaunch = status === "available" && !active && !holder;
        const launch = button("Launch", canLaunch ? "primary" : "", () => {
          const params = {
            system: config.systemId,
            mode: row.mode,
            compute_unit: unit,
            measure: measureOption.input.checked,
            respawn: respawnOption.input.checked,
          };
          if (row.deploy) params.deploy = row.deploy;
          errors.delete(unit);
          daemon
            .rpc("launch.start", params)
            .catch((error) => errors.set(unit, error.message))
            .then(render);
        });
        launch.disabled = !canLaunch;
        const canShutdown = active && !session.shutting_down;
        const shutdown = button("Shutdown", canShutdown ? "danger" : "", () =>
          control(unit, "launch.shutdown", {}),
        );
        shutdown.disabled = !canShutdown;
        controls.append(launch, shutdown);
        let measure = null;
        if (active && session.connected && session.control.measure_armed) {
          measure = measures.get(unit);
          const open = measure && MEASURE_OPEN.includes(measure.state);
          controls.appendChild(
            button(open ? "Stop measure" : "Start measure", "", () =>
              control(
                unit,
                open ? "measure.stop" : "measure.start",
                {},
                (result) => measures.set(unit, result),
              ),
            ),
          );
        }
        if (session)
          controls.appendChild(
            element(
              "span",
              `launch-session ${session.state}`,
              sessionLabel(session),
            ),
          );
        else if (holder)
          controls.appendChild(
            element("span", "unit-status", `in use · ${holder.mode}`),
          );
        else if (status)
          controls.appendChild(
            element("span", `unit-status ${status}`, status.replace(/_/g, " ")),
          );
        if (measure)
          controls.appendChild(
            element("span", "launch-measure", `measure: ${measure.message}`),
          );
        const error = errors.get(unit);
        if (error) {
          const note = element("span", "launch-error", error);
          note.title = error;
          controls.appendChild(note);
        }
        return controls;
      }

      // The map's unit controls, and above it the units with a launcher that
      // the map draws no section for.
      function renderUnits(rows) {
        options.hidden = !daemon.offers("launch.start");
        map.renderControls();
        const drawn = map.drawnUnits();
        undrawnUnits.replaceChildren();
        (drawn ? rows : []).forEach((row) => {
          if (drawn.includes(row.unit)) return;
          const controls = unitControls(row.unit);
          if (!controls) return;
          const entry = element("div", "control-undrawn-unit");
          entry.append(element("span", "launch-unit", row.unit), controls);
          undrawnUnits.appendChild(entry);
        });
        undrawnUnits.hidden = !undrawnUnits.childElementCount;
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
      store.onChange("view", showView);
      SD.on("daemon.health", loadUnitStatus);
      SD.on("daemon.systems.changed", loadUnitStatus);
      SD.on("launch.sessions", onSessions);
      SD.on("daemon.jobs", renderRviz);
      if (daemon.health) loadUnitStatus();
      render();
      showView();
      return {
        show() {
          shown = true;
          showView();
        },
        hide() {
          shown = false;
          showView();
          commands.close();
        },
      };
    },
  });
})();
