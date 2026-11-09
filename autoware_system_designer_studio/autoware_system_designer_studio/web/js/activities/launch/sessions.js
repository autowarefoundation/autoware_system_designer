// Launch: the daemon's launch sessions and their members, kept current from
// the event stream and resynchronised through launch.status on every hello.
// Page events: "launch.sessions" (unit -> session) and "launch.member"
// {path, unit, record}.
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
  let request = null;

  function emit() {
    SD.emit("launch.sessions", sessions);
  }

  function setMember(unit, record) {
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
    sessions.set(view.compute_unit, {
      ...view,
      members: previous?.members || [],
    });
    emit();
    // The runtime's hello brings the member list; the session view carries only its count.
    if (view.connected && !previous?.connected) resync();
  });
  SD.on("daemon.member", (event) => {
    const { compute_unit: unit, ...record } = event;
    setMember(unit, record);
    SD.emit("launch.member", { path: record.path, unit, record });
  });

  launch.sessions = sessions;
  launch.members = members;
  launch.resync = resync;
  launch.stateToken = stateToken;
  launch.stateLabel = stateLabel;
  launch.sessionLabel = sessionLabel;
  launch.sessionFailures = sessionFailures;
  launch.sessionToken = sessionToken;
})();
