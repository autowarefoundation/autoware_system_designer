// Launch: the launch state of the system and the controls of the shown mode
// and deploy, shared by the top bar and the state map. The state is of the
// mode launched, whatever mode is shown. Per compute unit: its launcher, its reach
// on the daemon, its session and what it offers; launch, shutdown, forget and
// the measurement window through the daemon. The launch method names the
// command shown; the launch options apply to every launch from here.
// Page event: "launch.control" after any of it changed.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const launch = (SD.activities.launch ||= {});

  const METHOD_KEY = "workbench.launchMethod";
  // Launch options by name: the preference holding each.
  const OPTION_KEYS = {
    measure: "workbench.launchMeasure",
    respawn: "workbench.launchRespawn",
  };
  const METHODS = [
    ["native", "Runtime"],
    ["xml", "XML"],
  ];
  // Measurement window states in which the next verb is stop.
  const MEASURE_OPEN = ["running"];
  // Button label while a measurement request awaits its answer.
  const MEASURE_PENDING = {
    "measure.start": "Starting measure…",
    "measure.stop": "Stopping measure…",
  };

  function button(label, className, onClick) {
    const node = element("button", `panel-button ${className}`.trim(), label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function launchControl(config, store, daemon) {
    const { sessions, sessionLabel, sessionToken, sessionFailures } = launch;
    const { unitActions, unitHolder, holderLabel, peerLabel, peerTitle } =
      launch;
    const hasNative = config.launchCommands.some((record) => record.native);
    const methods = METHODS.filter(
      ([value]) => value !== "native" || hasNative,
    );
    let method = SD.storage.get(METHOD_KEY);
    if (!methods.some(([value]) => value === method)) method = methods[0][0];
    let unitStatus = new Map();
    // Unit -> record of the peer daemon serving it; no entry for a unit of this daemon.
    let unitPeers = new Map();
    // Units with a launch request the daemon has not answered yet.
    const starting = new Set();
    const ofMode = launch.scope(config.systemId, () => store.get("mode"));
    // Per unit: the last error of a control, and the last measurement result.
    const errors = new Map();
    const measures = new Map();
    // Unit -> the measurement request awaiting its answer; its button takes no click meanwhile.
    const measuring = new Map();

    const changed = () => SD.emit("launch.control");
    const offered = () => daemon.offers("launch.start");

    function option(name) {
      return SD.storage.get(OPTION_KEYS[name]) === "true";
    }

    function setOption(name, value) {
      SD.storage.set(OPTION_KEYS[name], String(value));
    }

    function setMethod(value) {
      method = value;
      SD.storage.set(METHOD_KEY, value);
      changed();
    }

    // Launch records of the shown mode and deploy, one per compute unit.
    function rows() {
      const mode = store.get("mode");
      const deploy = store.get("deploy");
      return config.launchCommands.filter(
        (r) => r.mode === mode && r.deploy === deploy,
      );
    }

    function request(unit, rpcMethod, params, then) {
      errors.delete(unit);
      return daemon
        .rpc(rpcMethod, { compute_unit: unit, ...params })
        .then((result) => {
          then?.(result);
          changed();
        })
        .catch((error) => {
          errors.set(unit, error.message);
          changed();
        });
    }

    function holderOf(unit) {
      return unitHolder(sessions, {
        system: config.systemId,
        mode: store.get("mode"),
        unit,
      });
    }

    // The launcher of `unit` in the shown mode and deploy with what the unit
    // offers now; null without one.
    function unitState(unit) {
      const row = rows().find((r) => r.unit === unit);
      if (!row) return null;
      const status = unitStatus.get(unit);
      const session = ofMode.sessions.get(unit) || null;
      const live = !!session && session.state !== "exited";
      const holder = live ? null : holderOf(unit);
      let reported = false;
      sessions.forEach((other) => {
        if (other.compute_unit === unit && other.state !== "exited")
          reported = true;
      });
      const actions = unitActions({ status, session, holder, reported });
      if (starting.has(unit)) actions.canLaunch = false;
      return { row, status, session, holder, ...actions };
    }

    function unitsWhere(offer) {
      if (!offered()) return [];
      return rows()
        .map((row) => row.unit)
        .filter((unit) => unitState(unit)?.[offer]);
    }

    const launchable = () => unitsWhere("canLaunch");
    const stoppable = () => unitsWhere("canShutdown");

    function launchUnit(unit) {
      const { row } = unitState(unit);
      const params = {
        system: config.systemId,
        mode: row.mode,
        compute_unit: unit,
        measure: option("measure"),
        respawn: option("respawn"),
      };
      if (row.deploy) params.deploy = row.deploy;
      errors.delete(unit);
      starting.add(unit);
      changed();
      return daemon
        .rpc("launch.start", params)
        .catch((error) => errors.set(unit, error.message))
        .then(() => {
          starting.delete(unit);
          changed();
        });
    }

    const shutdownUnit = (unit) => request(unit, "launch.shutdown", {});

    // The mode the launch state is of: the one launched on this system, a
    // system running one mode at a time, else the shown one.
    function stateMode() {
      for (const session of sessions.values()) {
        if (session.system === config.systemId && session.state !== "exited")
          return session.mode;
      }
      return store.get("mode");
    }
    const ofState = launch.scope(config.systemId, stateMode);

    // Units with a launcher of the state's mode: those of the shown deploy
    // for the shown mode, else of any deploy.
    function stateUnits() {
      const mode = stateMode();
      if (mode === store.get("mode")) return rows().map((row) => row.unit);
      return [
        ...new Set(
          config.launchCommands
            .filter((record) => record.mode === mode)
            .map((record) => record.unit),
        ),
      ];
    }

    // Launch state of the system in one token and line, with its mode once a
    // session of it is known: the session of a lone unit, else the units
    // launched among all.
    function summary() {
      const units = stateUnits();
      if (!units.length)
        return {
          mode: "",
          token: "idle",
          label: `No launchers for ${store.get("mode")}`,
        };
      const held = units
        .map((unit) => ofState.sessions.get(unit))
        .filter(Boolean);
      if (!held.length) {
        return starting.size
          ? { mode: stateMode(), token: "warning", label: "starting" }
          : { mode: "", token: "idle", label: "idle" };
      }
      const mode = stateMode();
      const token = held
        .map(sessionToken)
        .reduce((a, b) => (SD.RunState.RANK[b] > SD.RunState.RANK[a] ? b : a));
      if (units.length === 1)
        return { mode, token, label: sessionLabel(held[0]) };
      const live = held.filter((session) => session.state !== "exited");
      const failed = held.reduce(
        (sum, session) => sum + sessionFailures(session).length,
        0,
      );
      const failures = failed ? ` · ${failed} failed` : "";
      return {
        mode,
        token,
        label: live.length
          ? `units ${live.length}/${units.length}${failures}`
          : `exited${failures}`,
      };
    }

    // Each unit of the state's mode with its token and line: its session,
    // else the launch awaited or its reach on the daemon.
    function unitSummaries() {
      return stateUnits().map((unit) => {
        const session = ofState.sessions.get(unit);
        if (session)
          return {
            unit,
            token: sessionToken(session),
            label: sessionLabel(session),
          };
        const status = unitStatus.get(unit);
        let label = "idle";
        if (starting.has(unit)) label = "starting";
        else if (status && status !== "available")
          label = status.replace(/_/g, " ");
        return {
          unit,
          token: starting.has(unit) ? "warning" : "idle",
          label,
        };
      });
    }

    // Units of the state's mode whose session can shut down.
    function stoppableLaunched() {
      if (!offered()) return [];
      return stateUnits().filter((unit) => {
        const session = ofState.sessions.get(unit) || null;
        const status = unitStatus.get(unit);
        return unitActions({ status, session }).canShutdown;
      });
    }

    // Controls of `unit` in one row: Launch and Shutdown, always offered with
    // the one that applies coloured, the measurement window of an armed
    // session, the peer daemon serving the unit, then the session or the
    // unit's reach, with Forget on the last reported session of an
    // unreachable unit.
    // Null for a unit without a launcher of the mode and deploy, or a daemon
    // that launches nothing.
    function unitControls(unit) {
      if (!offered()) return null;
      const state = unitState(unit);
      if (!state) return null;
      const { status, session, holder, reachable } = state;
      const { canLaunch, canShutdown, canMeasure, canForget } = state;
      const controls = element("div", "unit-controls");
      const start = button("Launch", canLaunch ? "primary" : "", () =>
        launchUnit(unit),
      );
      start.disabled = !canLaunch;
      if (holder) start.title = `waits for ${holderLabel(holder, unit)}`;
      const shutdown = button("Shutdown", canShutdown ? "danger" : "", () =>
        shutdownUnit(unit),
      );
      shutdown.disabled = !canShutdown;
      controls.append(start, shutdown);
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
            const verb = open ? "measure.stop" : "measure.start";
            measuring.set(unit, verb);
            request(unit, verb, {}, (result) =>
              measures.set(unit, result),
            ).finally(() => {
              measuring.delete(unit);
              changed();
            });
            changed();
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
        controls.appendChild(element("span", `unit-status ${status}`, status));
      if (session) {
        const label = element(
          "span",
          reachable ? `launch-session ${session.state}` : "launch-session",
          sessionLabel(session),
        );
        if (!reachable) label.title = "as last reported by the unit's daemon";
        controls.appendChild(label);
      } else if (holder && (reachable || holder.compute_unit === unit))
        controls.appendChild(
          element("span", "unit-status", holderLabel(holder, unit)),
        );
      else if (reachable && status)
        controls.appendChild(
          element("span", `unit-status ${status}`, status.replace(/_/g, " ")),
        );
      if (canForget) {
        const forget = button("Forget", "", () =>
          request(unit, "launch.forget", {}),
        );
        forget.title =
          "Drop the sessions the unit's daemon last reported, so the system can run another mode. The unit may still be running.";
        controls.appendChild(forget);
      }
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
      changed();
    }

    function onSessions() {
      const live = new Map();
      sessions.forEach((session) => {
        if (session.state !== "exited") live.set(session.compute_unit, session);
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
          request(unit, "measure.status", {}, (result) =>
            measures.set(unit, result),
          );
        }
      });
      loadUnitStatus();
    }

    ["mode", "deploy"].forEach((key) => store.onChange(key, changed));
    SD.on("daemon.health", loadUnitStatus);
    SD.on("daemon.systems.changed", loadUnitStatus);
    SD.on("launch.sessions", onSessions);
    if (daemon.health) loadUnitStatus();

    return {
      methods,
      method: () => method,
      setMethod,
      option,
      setOption,
      offered,
      rows,
      summary,
      unitSummaries,
      launchable,
      stoppable,
      stoppableLaunched,
      launchUnit,
      shutdownUnit,
      unitControls,
    };
  }

  launch.launchControl = launchControl;
})();
