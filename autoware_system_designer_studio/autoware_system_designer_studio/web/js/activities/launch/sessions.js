// Launch: the daemon's launch sessions and their members, kept current from
// the event stream and resynchronised through launch.status on every hello.
// The daemon holds the last session of each mode of a system on a compute
// unit; `scope` gives the records of one mode. A `stale` session is the one
// an unanswering peer daemon last reported.
// Page events: "launch.sessions" (session key -> session), "launch.member"
// {path, unit, record} and "launch.activity" after the output rates changed.
// A member's restart counts are the daemon's, on its record. A status answer
// may be older than the member events already received, so the newer record
// of a member is kept.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  // Runtime member state -> run-state token of the UI.
  const STATE_TOKENS = {
    running: "running",
    loaded: "running",
    loading: "warning",
    respawning: "warning",
    failed: "failed",
    pending: "idle",
    done: "idle",
  };

  function stateToken(record) {
    if (!record) return "idle";
    if (record.state === "stopped")
      return record.detail?.exit_code === 0 ? "ok" : "warning";
    return STATE_TOKENS[record.state] || "idle";
  }

  function stateLabel(record) {
    if (!record) return "idle";
    const detail = record.detail || {};
    switch (record.state) {
      case "running":
        return detail.pid ? `running · pid ${detail.pid}` : "running";
      case "stopped":
        return `stopped · exit ${detail.exit_code}`;
      case "respawning":
        return `respawning · attempt ${detail.attempt}`;
      case "failed":
        return detail.error ? `failed · ${detail.error}` : "failed";
      case "loaded":
        return detail.node ? `loaded · ${detail.node}` : "loaded";
      default:
        return record.state;
    }
  }

  // Failed members of a session, in the order they failed.
  function sessionFailures(session) {
    return (session?.members || [])
      .filter((record) => record.state === "failed")
      .sort((a, b) => (a.time || 0) - (b.time || 0));
  }

  // Run-state token of a session: a member failure outranks the session state.
  function sessionToken(session) {
    if (!session) return "idle";
    if (sessionFailures(session).length) return "failed";
    if (session.state === "exited") {
      if (session.job_state === "cancelled") return "idle";
      return session.exit_code === 0 ? "ok" : "warning";
    }
    return session.state === "running" ? "running" : "warning";
  }

  // Output growth, in bytes per second, from which a member's log counts as busy.
  const BUSY_LOG_BPS = 4096;

  function busyLog(rate) {
    return rate >= BUSY_LOG_BPS;
  }

  function rateLabel(rate) {
    if (rate >= 1048576) return `${(rate / 1048576).toFixed(1)} MB/s`;
    if (rate >= 1024) return `${(rate / 1024).toFixed(1)} KB/s`;
    return `${Math.round(rate)} B/s`;
  }

  // "2026-11-09 12:43:14 · LoggingSimulation · site_a · main_ecu": a run by its
  // start time, mode, deploy variant and compute unit, as far as its manifest
  // says; the system is the monitor's.
  function runLabel(run) {
    const pad = (n) => String(n).padStart(2, "0");
    const parts = [];
    if (run.started) {
      const d = new Date(run.started * 1000);
      parts.push(
        `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
          `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`,
      );
    } else parts.push(run.run);
    ["mode", "deploy", "compute_unit"].forEach((key) => {
      if (run[key]) parts.push(run[key]);
    });
    return parts.join(" · ");
  }

  // Units of the launch records `commands` ({ mode, unit, deploy }) by mode and
  // deploy variant: a function of (mode, deploy) giving the units launched.
  function modeUnits(commands) {
    return (mode, deploy) =>
      (commands || [])
        .filter((c) => c.mode === mode && (c.deploy || "") === (deploy || ""))
        .map((c) => c.unit);
  }

  // `units` as a label: "<all units>" when they cover every one of `all`, more
  // than one, else their names in order.
  function unitsLabel(units, all = []) {
    const own = new Set(units);
    if (all.length > 1 && all.every((unit) => own.has(unit)))
      return "<all units>";
    return [...own].sort().join(", ");
  }

  // Runs in listing order, those of one launch gathered where its first run
  // stands: [{ launch, value, label, runs }], `launch` null for a run naming
  // none. `value` keys the group in a selector. A launch is labelled by its
  // first start, mode, deploy variant and compute units, "<all units>" for every
  // unit `unitsOf(mode, deploy)` gives.
  function runGroups(runs, unitsOf = () => []) {
    const groups = [];
    const byLaunch = new Map();
    runs.forEach((run) => {
      let group = run.launch ? byLaunch.get(run.launch) : null;
      if (!group) {
        group = {
          launch: run.launch || null,
          value: run.launch ? `launch:${run.launch}` : run.run,
          label: "",
          runs: [],
        };
        if (run.launch) byLaunch.set(run.launch, group);
        groups.push(group);
      }
      group.runs.push(run);
    });
    groups.forEach((group) => {
      const first = group.runs.reduce((a, b) =>
        (b.started || 0) < (a.started || 0) ? b : a,
      );
      const units = group.runs.map((run) => run.compute_unit).filter(Boolean);
      group.label = runLabel({
        ...first,
        compute_unit: unitsLabel(units, unitsOf(first.mode, first.deploy)),
      });
    });
    return groups;
  }

  // The options of a run selector over `groups`: "Live", then one option per
  // launch (`whole`), or a launch as a group of its units' runs.
  function runOptions(groups, whole) {
    const { element } = SD.widgets;
    const option = (value, label) => {
      const item = element("option", "", label);
      item.value = value;
      return item;
    };
    return [
      option("", "Live"),
      ...groups.map((group) => {
        if (whole || !group.launch) return option(group.value, group.label);
        const items = element("optgroup");
        items.label = group.label;
        items.append(
          ...group.runs.map((run) => option(run.run, runLabel(run))),
        );
        return items;
      }),
    ];
  }

  // Selector of the past launches of `systemId`: "Live", then one option per
  // launch of the listed runs `filter` keeps, shown while the daemon offers
  // `offered`, its labels shortened by `unitsOf` as in `runGroups`.
  // `onChange(value)` follows a pick, and a shown launch leaving the listing as
  // "". `pick(value)` shows the launch keyed `value` and returns its group; an
  // unlisted value shows Live and returns null.
  function runSelector({
    systemId,
    offered,
    filter = () => true,
    unitsOf,
    onChange,
  }) {
    const select = SD.widgets.element("select", "monitor-run-select");
    let groups = [];
    const groupOf = (value) =>
      (value && groups.find((group) => group.value === value)) || null;
    select.addEventListener("change", () => onChange(select.value));

    function load() {
      const daemon = SD.daemon;
      select.hidden = !daemon.offers(offered);
      if (select.hidden) return;
      daemon
        .rpc("launch.runs", { system: systemId })
        .then(({ runs }) => {
          const current = select.value;
          groups = runGroups(runs.filter(filter), unitsOf);
          select.replaceChildren(...runOptions(groups, true));
          select.value = groupOf(current) ? current : "";
          if (current && !groupOf(current)) onChange("");
        })
        .catch((error) => console.warn("launch runs:", error));
    }

    function pick(value) {
      const group = groupOf(value);
      select.value = group ? value : "";
      return group;
    }

    return { element: select, load, pick };
  }

  // Answers of `method` for each of `runs`, asked with `paramsOf(run)`:
  // { read: [{ run, result }], error }, `error` the message of the first
  // failure.
  async function readRuns(runs, method, paramsOf) {
    const outcomes = await Promise.allSettled(
      runs.map((run) => SD.daemon.rpc(method, paramsOf(run))),
    );
    const read = [];
    let error = "";
    outcomes.forEach((outcome, index) => {
      if (outcome.status === "fulfilled")
        read.push({ run: runs[index], result: outcome.value });
      else {
        error ||= outcome.reason?.message || String(outcome.reason);
        console.warn(`${method}:`, outcome.reason);
      }
    });
    return { read, error };
  }

  function sessionLabel(session) {
    if (!session) return "";
    const failed = sessionFailures(session).length;
    const failures = failed ? ` · ${failed} failed` : "";
    if (session.state === "exited") {
      const code = session.exit_code;
      return session.job_state === "cancelled"
        ? "cancelled"
        : `exited · code ${code === null ? "?" : code}${failures}`;
    }
    if (session.shutting_down || session.state === "stopping")
      return `shutting down${failures}`;
    if (session.state === "starting") return "starting";
    return `running · ${session.member_count} members${failures}`;
  }

  // Session key -> session view, members included: the last session of each
  // mode of a system on a compute unit.
  const sessions = new Map();
  // Session key -> member path -> { unit, job, record }.
  const members = new Map();
  // Session key -> member path -> output growth of the members whose files grew in the last window.
  const activity = new Map();
  let request = null;

  function sessionKey(system, mode, unit) {
    return [system, mode, unit].join("\n");
  }

  function keyOf(session) {
    return sessionKey(session.system, session.mode, session.compute_unit);
  }

  function emit() {
    SD.emit("launch.sessions", sessions);
  }

  function setActivity(key, rates) {
    const grown = new Map(
      Object.entries(rates || {}).filter(([, rate]) => rate > 0),
    );
    if (grown.size) activity.set(key, grown);
    else activity.delete(key);
    SD.emit("launch.activity", activity);
  }

  // The live records of one mode of a system, over the sessions that launched
  // `mode()` of `systemId`: sessions by compute unit, members by path with
  // { unit, job, record }, a member's output growth in bytes per second over
  // the last window (0 once quiet), and { unit, restarts, respawns } of a
  // member started again within its launch (`restarts`, the `respawns` among
  // them; null when it never was).
  function scope(systemId, mode) {
    const within = (session) =>
      session.system === systemId && session.mode === mode();
    // [session key, entry] of the member at `path` in the mode's sessions.
    const find = (path) => {
      for (const [key, session] of sessions) {
        const member = within(session) && members.get(key)?.get(path);
        if (member) return [key, member];
      }
      return null;
    };
    return {
      sessions: {
        get: (unit) => sessions.get(sessionKey(systemId, mode(), unit)),
        forEach(visit) {
          sessions.forEach((session) => {
            if (within(session)) visit(session, session.compute_unit);
          });
        },
      },
      members: { get: (path) => find(path)?.[1] },
      logRate(path) {
        for (const [key, session] of sessions) {
          const rate = within(session) && activity.get(key)?.get(path);
          if (rate) return rate;
        }
        return 0;
      },
      restartsOf(path) {
        const member = find(path)?.[1];
        const { restarts = 0, respawns = 0 } = member?.record || {};
        return restarts || respawns
          ? { unit: member.unit, restarts, respawns }
          : null;
      },
    };
  }

  function setMember(key, unit, record) {
    const session = sessions.get(key);
    if (!members.has(key)) members.set(key, new Map());
    members.get(key).set(record.path, {
      unit,
      job: session?.job || "",
      record,
    });
    if (!session) return;
    const index = session.members.findIndex((m) => m.path === record.path);
    if (index >= 0) session.members[index] = record;
    else session.members.push(record);
  }

  function resync() {
    const daemon = SD.daemon;
    if (!daemon.offers("launch.status")) return Promise.resolve();
    const current = daemon
      .rpc("launch.status")
      .then((result) => {
        if (request !== current) return;
        const known = new Map(members);
        sessions.clear();
        members.clear();
        activity.clear();
        result.sessions.forEach((session) => {
          const key = keyOf(session);
          const unit = session.compute_unit;
          const job = session.job || "";
          // A record received since the daemon answered outdates the answer's.
          session.members = (session.members || []).map((record) => {
            const seen = known.get(key)?.get(record.path);
            const newer =
              seen &&
              seen.job === job &&
              (seen.record.time || 0) > (record.time || 0);
            return newer ? seen.record : record;
          });
          sessions.set(key, session);
          members.set(
            key,
            new Map(
              session.members.map((record) => [
                record.path,
                { unit, job, record },
              ]),
            ),
          );
        });
        emit();
      })
      .catch((error) => console.warn("launch status:", error));
    request = current;
    return current;
  }

  SD.on("daemon.health", resync);
  SD.on("daemon.launch", (view) => {
    const key = keyOf(view);
    // A session of an unanswering peer that the daemon dropped.
    if (view.forgotten) {
      sessions.delete(key);
      members.delete(key);
      setActivity(key, {});
      emit();
      return;
    }
    const previous = sessions.get(key);
    // A new launch of the mode starts without the members of its last.
    const relaunched = !!previous && previous.job !== view.job;
    if (relaunched) members.delete(key);
    sessions.set(key, {
      ...view,
      members: (!relaunched && previous?.members) || [],
    });
    emit();
    // The runtime's hello brings the member list; the session view carries only its count.
    if (view.connected && !previous?.connected) resync();
    if (!view.connected && previous?.connected) setActivity(key, {});
  });
  SD.on("daemon.member.activity", (event) =>
    setActivity(
      sessionKey(event.system, event.mode, event.compute_unit),
      event.rates,
    ),
  );
  SD.on("daemon.member", (event) => {
    const { compute_unit: unit, system, mode, ...record } = event;
    setMember(sessionKey(system, mode, unit), unit, record);
    SD.emit("launch.member", { path: record.path, unit, record });
  });

  launch.sessions = sessions;
  launch.scope = scope;
  launch.busyLog = busyLog;
  launch.rateLabel = rateLabel;
  launch.BUSY_LOG_BPS = BUSY_LOG_BPS;
  launch.resync = resync;
  launch.stateToken = stateToken;
  launch.stateLabel = stateLabel;
  launch.sessionLabel = sessionLabel;
  launch.runGroups = runGroups;
  launch.runLabel = runLabel;
  launch.runOptions = runOptions;
  launch.runSelector = runSelector;
  launch.modeUnits = modeUnits;
  launch.unitsLabel = unitsLabel;
  launch.readRuns = readRuns;
  launch.sessionFailures = sessionFailures;
  launch.sessionToken = sessionToken;
})();
