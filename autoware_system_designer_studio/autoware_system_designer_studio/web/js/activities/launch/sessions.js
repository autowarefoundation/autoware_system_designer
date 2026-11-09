// Launch: the daemon's launch sessions and their members, kept current from
// the event stream and resynchronised through launch.status on every hello.
// Page events: "launch.sessions" (unit -> session), "launch.member"
// {path, unit, record} and "launch.activity" after the output rates changed.
// Restarts are counted per member while the page is open: every re-entry into
// running or loading, respawns among them; a new launch of the unit resets them.
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

  const sessions = new Map(); // compute unit -> session view, members included
  const members = new Map(); // member path -> { unit, record }
  // Member path -> { unit, restarts, respawns } of the members restarted while this page watched.
  const counters = new Map();
  // Member path -> { unit, rate }: output growth of the members whose files grew in the last window.
  const activity = new Map();
  let request = null;

  function emit() {
    SD.emit("launch.sessions", sessions);
  }

  // Bytes per second a member's output grew by in the last window; 0 once quiet.
  function logRate(path) {
    return activity.get(path)?.rate || 0;
  }

  function setActivity(unit, rates) {
    activity.forEach((entry, path) => {
      if (entry.unit === unit) activity.delete(path);
    });
    Object.entries(rates || {}).forEach(([path, rate]) => {
      if (rate > 0) activity.set(path, { unit, rate });
    });
    SD.emit("launch.activity", activity);
  }

  // States a member re-enters when it is started again.
  const STARTING_STATES = new Set(["running", "loading"]);

  function count(unit, record, previous) {
    if (!previous || previous.state === record.state) return;
    const respawn = record.state === "respawning";
    const restart =
      STARTING_STATES.has(record.state) && previous.state !== "pending";
    if (!respawn && !restart) return;
    const entry = counters.get(record.path) || {
      unit,
      restarts: 0,
      respawns: 0,
    };
    if (respawn)
      entry.respawns = Math.max(
        entry.respawns,
        record.detail?.attempt || entry.respawns + 1,
      );
    else entry.restarts += 1;
    counters.set(record.path, entry);
  }

  // Restart counts of a member, or null when it was never restarted.
  function restartsOf(path) {
    return counters.get(path) || null;
  }

  function resetCounters(unit) {
    counters.forEach((entry, path) => {
      if (entry.unit === unit) counters.delete(path);
    });
  }

  function setMember(unit, record) {
    count(unit, record, members.get(record.path)?.record);
    members.set(record.path, { unit, record });
    const session = sessions.get(unit);
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
        sessions.clear();
        members.clear();
        activity.clear();
        result.sessions.forEach((session) => {
          session.members ||= [];
          sessions.set(session.compute_unit, session);
          session.members.forEach((record) =>
            setMember(session.compute_unit, record),
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
    const previous = sessions.get(view.compute_unit);
    if (previous && view.job !== previous.job) resetCounters(view.compute_unit);
    sessions.set(view.compute_unit, {
      ...view,
      members: previous?.members || [],
    });
    emit();
    // The runtime's hello brings the member list; the session view carries only its count.
    if (view.connected && !previous?.connected) resync();
    if (!view.connected && previous?.connected)
      setActivity(view.compute_unit, {});
  });
  SD.on("daemon.member.activity", ({ compute_unit: unit, rates }) =>
    setActivity(unit, rates),
  );
  SD.on("daemon.member", (event) => {
    const { compute_unit: unit, ...record } = event;
    setMember(unit, record);
    SD.emit("launch.member", { path: record.path, unit, record });
  });

  launch.sessions = sessions;
  launch.members = members;
  launch.counters = counters;
  launch.restartsOf = restartsOf;
  launch.activity = activity;
  launch.logRate = logRate;
  launch.busyLog = busyLog;
  launch.rateLabel = rateLabel;
  launch.BUSY_LOG_BPS = BUSY_LOG_BPS;
  launch.resync = resync;
  launch.stateToken = stateToken;
  launch.stateLabel = stateLabel;
  launch.sessionLabel = sessionLabel;
  launch.runLabel = runLabel;
  launch.sessionFailures = sessionFailures;
  launch.sessionToken = sessionToken;
})();
