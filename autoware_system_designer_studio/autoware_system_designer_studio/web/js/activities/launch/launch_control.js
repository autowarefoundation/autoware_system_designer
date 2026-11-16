// Launch: the launch state of the daemon and the controls of the shown mode
// and deploy, shared by the top bar and the state map. The state is of the
// system and mode launched, whatever system and mode are shown. Per compute unit: its launcher, its reach
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
    // Unit -> reach, of the units of the system the launch state is of.
    let stateStatus = unitStatus;
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

    // Name of a system of the site by its id, else the id.
    const systemName = (id) =>
      (config.systems || []).find((system) => system.id === id)?.name || id;

    const holderText = (holder, unit) =>
      holderLabel(holder, unit, config.systemId, systemName);

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

    // A live session of the daemon, running one mode and deploy of one system
    // at a time; null without one.
    function launchedSession() {
      for (const session of sessions.values()) {
        if (session.state !== "exited") return session;
      }
      return null;
    }

    // The system and mode the launch state is of: those launched, else the
    // shown ones.
    const stateSystem = () => launchedSession()?.system || config.systemId;
    const stateMode = () => launchedSession()?.mode || store.get("mode");
    const ofState = {
      sessions: {
        get: (unit) =>
          launch.scope(stateSystem(), stateMode).sessions.get(unit),
      },
    };

    // Units of the state: of another system, those its mode launched; of this
    // one, those with a launcher of the mode in the launched deploy (in any
    // deploy when it names none known), else in the shown deploy.
    function stateUnits() {
      const launched = launchedSession();
      if (!launched) return rows().map((row) => row.unit);
      if (launched.system !== config.systemId) {
        const units = [];
        launch
          .scope(launched.system, () => launched.mode)
          .sessions.forEach((_session, unit) => units.push(unit));
        return units;
      }
      const ofMode = config.launchCommands.filter(
        (record) => record.mode === launched.mode,
      );
      const ofDeploy = ofMode.filter(
        (record) => (record.deploy || "") === (launched.deploy || ""),
      );
      const records = ofDeploy.length ? ofDeploy : ofMode;
      return [...new Set(records.map((record) => record.unit))];
    }

    // Launch state of the daemon in one token and line, with its system and
    // mode once a session is known: the session of a lone unit, else the
    // units launched among all. `system` is { id, name, shown }, `shown` for
    // the system of this page.
    function summary() {
      const units = stateUnits();
      if (!units.length)
        return {
          system: null,
          mode: "",
          token: "idle",
          label: `No launchers for ${store.get("mode")}`,
        };
      const id = stateSystem();
      const system = {
        id,
        name: systemName(id),
        shown: id === config.systemId,
      };
      const held = units
        .map((unit) => ofState.sessions.get(unit))
        .filter(Boolean);
      if (!held.length) {
        return starting.size
          ? { system, mode: stateMode(), token: "warning", label: "starting" }
          : { system: null, mode: "", token: "idle", label: "idle" };
      }
      const mode = stateMode();
      const token = held
        .map(sessionToken)
        .reduce((a, b) => (SD.RunState.RANK[b] > SD.RunState.RANK[a] ? b : a));
      if (units.length === 1)
        return { system, mode, token, label: sessionLabel(held[0]) };
      const live = held.filter((session) => session.state !== "exited");
      const failed = held.reduce(
        (sum, session) => sum + sessionFailures(session).length,
        0,
      );
      const failures = failed ? ` · ${failed} failed` : "";
      return {
        system,
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
        const status = stateStatus.get(unit);
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
        const status = stateStatus.get(unit);
        return unitActions({ status, session }).canShutdown;
      });
    }

    // What `unit` offers as elements: `actions`, Launch and Shutdown, always
    // offered with the one that applies coloured, the measurement window of
    // an armed session and Forget on the last reported session of an
    // unreachable unit; `state`, the session or the unit's reach; `notes`,
    // the peer daemon serving the unit, the last error and the measurement.
    // Null for a unit without a launcher of the mode and deploy, or a daemon
    // that launches nothing.
    function unitParts(unit) {
      if (!offered()) return null;
      const unitNow = unitState(unit);
      if (!unitNow) return null;
      const { status, session, holder, reachable } = unitNow;
      const { canLaunch, canShutdown, canMeasure, canForget } = unitNow;
      const actions = [];
      const state = [];
      const notes = [];
      const start = button("Launch", canLaunch ? "primary" : "", () =>
        launchUnit(unit),
      );
      start.disabled = !canLaunch;
      if (holder) start.title = `waits for ${holderText(holder, unit)}`;
      const shutdown = button("Shutdown", canShutdown ? "danger" : "", () =>
        shutdownUnit(unit),
      );
      shutdown.disabled = !canShutdown;
      actions.push(start, shutdown);
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
        actions.push(toggle);
      }
      if (canForget) {
        const forget = button("Forget", "", () =>
          request(unit, "launch.forget", {}),
        );
        forget.title =
          "Drop the sessions the unit's daemon last reported, so the system can run another mode. The unit may still be running.";
        actions.push(forget);
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
        notes.push(chip);
      }
      if (!reachable)
        state.push(element("span", `unit-status ${status}`, status));
      if (session) {
        const label = element(
          "span",
          reachable ? `launch-session ${session.state}` : "launch-session",
          sessionLabel(session),
        );
        if (!reachable) label.title = "as last reported by the unit's daemon";
        state.push(label);
      } else if (holder && (reachable || holder.compute_unit === unit))
        state.push(element("span", "unit-status", holderText(holder, unit)));
      else if (reachable && status)
        state.push(
          element("span", `unit-status ${status}`, status.replace(/_/g, " ")),
        );
      const error = errors.get(unit);
      if (error) {
        const note = element("span", "launch-error", error);
        note.title = error;
        notes.push(note);
      }
      if (measure)
        notes.push(
          element("span", "launch-measure", `measure: ${measure.message}`),
        );
      const token = session
        ? sessionToken(session)
        : starting.has(unit)
          ? "warning"
          : "idle";
      return { actions, state, notes, token };
    }

    // The last control error of each unit of the shown mode and deploy and of
    // the launch state, as [unit, message].
    function unitErrors() {
      const units = new Set([
        ...rows().map((row) => row.unit),
        ...stateUnits(),
      ]);
      return [...errors].filter(([unit]) => units.has(unit));
    }

    // Every part of `unit` in one row, the actions first; null as `unitParts`.
    function unitControls(unit) {
      const parts = unitParts(unit);
      if (!parts) return null;
      const controls = element("div", "unit-controls");
      controls.append(...parts.actions, ...parts.state, ...parts.notes);
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
        stateStatus = unitStatus;
        const running = stateSystem();
        if (running !== config.systemId) {
          const other = await daemon.system(running);
          stateStatus = new Map(
            (other?.compute_units || []).map((u) => [u.name, u.status]),
          );
        }
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
    SD.on("daemon.probed", changed);
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
      unitParts,
      unitControls,
      unitErrors,
    };
  }

  launch.launchControl = launchControl;
})();
