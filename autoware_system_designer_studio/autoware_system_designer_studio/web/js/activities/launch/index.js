// Launch activity: three views of the mode, chosen in the side panel. Control
// is the state map, with launch and shutdown of each compute unit in its
// section head, of every unit in the view head, and the actions on the
// selected member above it; Logs is the
// member tree beside the selected member's log, read only; Topics is the
// live rate of every topic against the design. Escape drops a
// pending confirmation, else the selection, also on the other activities'
// diagrams through `selection.clear`.
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
  const {
    commandsTable,
    stateMonitor,
    stateMap,
    memberActions,
    sessionLabel,
    topicRates,
    unitActions,
    peerLabel,
    peerTitle,
  } = SD.activities.launch;

  // Main views by the store's `view`.
  const VIEWS = [
    ["control", "Control"],
    ["logs", "Logs"],
    ["topics", "Topics"],
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
  // Button label while a measurement request awaits its answer.
  const MEASURE_PENDING = {
    "measure.start": "Starting measure…",
    "measure.stop": "Stopping measure…",
  };

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
      // Unit -> record of the peer daemon serving it; no entry for a unit of this daemon.
      let unitPeers = new Map();
      const { sessions } = SD.activities.launch;
      const ofMode = SD.activities.launch.scope(config.systemId, () =>
        store.get("mode"),
      );
      // Per unit: the last error of a control, and the last measurement result.
      const errors = new Map();
      const measures = new Map();
      // Unit -> the measurement request awaiting its answer; its button takes no click meanwhile.
      const measuring = new Map();

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
      const measureOption = checkbox("Measure from launch", MEASURE_KEY, false);
      measureOption.element.title =
        "Open the latency measurement window once every member started; " +
        "without it a window opens on Start measure.";
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
      const launchAll = button("Launch all", "", () =>
        launchable().forEach(launchUnit),
      );
      const shutdownAll = button("Shutdown all", "", () =>
        stoppable().forEach((unit) => control(unit, "launch.shutdown", {})),
      );
      const allControls = element("div", "unit-controls control-all");
      allControls.append(launchAll, shutdownAll);
      controlView.trailing.append(allControls, legend);
      const undrawnUnits = element("div", "control-undrawn-units");
      controlView.body.appendChild(undrawnUnits);
      const map = stateMap(controlView.body, config, store, {
        unitControls,
        onOpen: () => store.set("view", "logs"),
        onLoad: () => {
          render();
          memberBar.render();
        },
      });
      const memberBar = memberActions(config, store, {
        entityOf: (key) => map.entityOf(key),
        containerOf: (entity) => map.containerOf(entity),
      });
      controlView.element.insertBefore(memberBar.element, controlView.body);
      const logsView = mainView({ title: "Logs" });
      const monitorPane = element("div", "split-h");
      logsView.body.appendChild(monitorPane);
      const topicsView = mainView({ title: "Topics" });
      const topicsPane = element("div", "topic-pane");
      topicsView.body.appendChild(topicsPane);
      main.append(controlView.element, logsView.element, topicsView.element);
      const monitor = stateMonitor(monitorPane, config, store);
      const topics = topicRates(topicsPane, config, store);
      topicsView.head.insertBefore(topics.runSelector, topicsView.trailing);
      logsView.head.insertBefore(monitor.runSelector, logsView.trailing);
      // The activity's slots are visible from mount until another activity takes over.
      let shown = true;

      // Shows the store's view; the other one stops following and laying out.
      function showView() {
        const current = store.get("view");
        views.mark(current);
        controlView.element.hidden = current !== "control";
        logsView.element.hidden = current !== "logs";
        topicsView.element.hidden = current !== "topics";
        if (shown && current === "control") map.show();
        else map.hide();
        if (shown && current === "logs") monitor.show();
        else monitor.hide();
        if (shown && current === "topics") topics.show();
        else topics.hide();
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

      // The live session a launch of `unit` in the shown mode waits for: a unit
      // runs one launch at a time, a system one mode at a time across its units.
      function holderOf(unit) {
        const mode = store.get("mode");
        let holder = null;
        sessions.forEach((session) => {
          if (session.state === "exited") return;
          const own = session.system === config.systemId;
          if (own && session.mode === mode) return;
          if (session.compute_unit === unit || own) holder = session;
        });
        return holder;
      }

      // The launcher of `unit` in the shown mode and deploy with what the unit
      // offers now; null without one.
      function unitState(unit) {
        const row = launchRows(config, store).find((r) => r.unit === unit);
        if (!row) return null;
        const status = unitStatus.get(unit);
        const session = ofMode.sessions.get(unit) || null;
        const live = !!session && session.state !== "exited";
        const holder = live ? null : holderOf(unit);
        return {
          row,
          status,
          session,
          holder,
          ...unitActions({ status, session, holder }),
        };
      }

      function unitsWhere(offered) {
        if (!daemon.offers("launch.start")) return [];
        return launchRows(config, store)
          .map((row) => row.unit)
          .filter((unit) => unitState(unit)?.[offered]);
      }

      const launchable = () => unitsWhere("canLaunch");
      const stoppable = () => unitsWhere("canShutdown");

      function launchUnit(unit) {
        const { row } = unitState(unit);
        const params = {
          system: config.systemId,
          mode: row.mode,
          compute_unit: unit,
          measure: measureOption.input.checked,
          respawn: respawnOption.input.checked,
        };
        if (row.deploy) params.deploy = row.deploy;
        errors.delete(unit);
        return daemon
          .rpc("launch.start", params)
          .catch((error) => errors.set(unit, error.message))
          .then(render);
      }

      // Controls of `unit` for its section head on the map: Launch and Shutdown,
      // always offered with the one that applies coloured, the measurement
      // window of an armed session, the peer daemon serving the unit, then the
      // session or the unit's reach.
      // Null for a unit without a launcher of the mode and deploy, or a daemon
      // that launches nothing.
      function unitControls(unit) {
        if (!daemon.offers("launch.start")) return null;
        const state = unitState(unit);
        if (!state) return null;
        const { status, session, holder, reachable } = state;
        const { canLaunch, canShutdown, canMeasure } = state;
        const controls = element("div", "unit-controls");
        const launch = button("Launch", canLaunch ? "primary" : "", () =>
          launchUnit(unit),
        );
        launch.disabled = !canLaunch;
        const shutdown = button("Shutdown", canShutdown ? "danger" : "", () =>
          control(unit, "launch.shutdown", {}),
        );
        shutdown.disabled = !canShutdown;
        controls.append(launch, shutdown);
        let measure = null;
        if (canMeasure) {
          measure = measures.get(unit);
          const open = measure && MEASURE_OPEN.includes(measure.state);
          const pending = measuring.get(unit);
          const toggle = button(
            pending
              ? MEASURE_PENDING[pending]
              : open
                ? "Stop measure"
                : "Start measure",
            "",
            () => {
              if (measuring.has(unit)) return;
              const method = open ? "measure.stop" : "measure.start";
              measuring.set(unit, method);
              control(unit, method, {}, (result) =>
                measures.set(unit, result),
              ).finally(() => {
                measuring.delete(unit);
                render();
              });
              render();
            },
          );
          toggle.disabled = !!pending;
          controls.appendChild(toggle);
        }
        const peer = unitPeers.get(unit);
        if (peer) {
          const chip = element(
            "span",
            `unit-peer ${peer.state}`,
            peerLabel(peer),
          );
          if (peer.mismatch?.length) chip.classList.add("mismatch");
          chip.title = peerTitle(peer);
          controls.appendChild(chip);
        }
        if (!reachable)
          controls.appendChild(
            element("span", `unit-status ${status}`, status),
          );
        if (session) {
          const label = element(
            "span",
            reachable ? `launch-session ${session.state}` : "launch-session",
            sessionLabel(session),
          );
          if (!reachable) label.title = "as last reported by the unit's daemon";
          controls.appendChild(label);
        } else if (reachable && holder)
          controls.appendChild(
            element(
              "span",
              "unit-status",
              holder.compute_unit === unit
                ? `in use · ${holder.mode}`
                : `${holder.mode} runs on ${holder.compute_unit}`,
            ),
          );
        else if (reachable && status)
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
        const canLaunch = launchable().length > 0;
        const canShutdown = stoppable().length > 0;
        allControls.hidden = !daemon.offers("launch.start") || rows.length < 2;
        launchAll.disabled = !canLaunch;
        launchAll.classList.toggle("primary", canLaunch);
        shutdownAll.disabled = !canShutdown;
        shutdownAll.classList.toggle("danger", canShutdown);
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
          const units = system?.compute_units || [];
          unitStatus = new Map(units.map((u) => [u.name, u.status]));
          unitPeers = new Map(
            units.filter((u) => u.daemon).map((u) => [u.name, u.daemon]),
          );
        } catch (error) {
          console.warn("launch:", error);
        }
        render();
      }

      function onSessions() {
        const live = new Map();
        sessions.forEach((session) => {
          if (session.state !== "exited")
            live.set(session.compute_unit, session);
        });
        measures.forEach((_measure, unit) => {
          if (!live.has(unit)) measures.delete(unit);
        });
        live.forEach((session, unit) => {
          if (
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
      // Captured ahead of the popups' own Escape, so one that closes a popup does nothing here.
      document.addEventListener(
        "keydown",
        (event) => {
          if (event.key !== "Escape" || !shown || event.defaultPrevented)
            return;
          if (
            document.querySelector(
              ".popup-overlay.open, #settings-overlay.open",
            )
          )
            return;
          if (event.target.closest?.("input, textarea, select")) return;
          if (memberBar.cancel()) return;
          store.set("entity", "");
          SD.emit("selection.clear");
        },
        true,
      );
      SD.on("daemon.health", loadUnitStatus);
      SD.on("daemon.health", () => memberBar.render());
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
