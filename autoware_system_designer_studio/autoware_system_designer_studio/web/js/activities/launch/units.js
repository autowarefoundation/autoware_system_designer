// Launch: what a compute unit offers from its reach on the daemon, its session
// of the shown mode and the session holding it, which session holds a unit,
// and the labels of the peer daemon serving a unit.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  const UNREACHABLE = "unreachable";

  // `status` is the unit's reach in systems.list, `session` its session of the
  // shown mode, `holder` the live session a launch would wait for. A session of
  // an unreachable unit is the one its daemon last reported; `reported` says
  // the unit has such a live session of any mode, which holds its system to
  // that mode until it is forgotten.
  function unitActions({ status, session, holder, reported }) {
    const active = !!session && session.state !== "exited";
    const reachable = status !== UNREACHABLE;
    return {
      active,
      reachable,
      canLaunch: status === "available" && !active && !holder,
      canShutdown: active && reachable && !session.shutting_down,
      canForget: !reachable && (active || !!reported),
      canMeasure:
        active &&
        reachable &&
        !!session.connected &&
        !!session.control?.measure_armed,
    };
  }

  // The live session a launch of `unit` in `mode` of `system` waits for: a unit
  // runs one launch at a time, the daemon one mode of one system at a time
  // across its units, and a mode shutting down on any unit ends on all of
  // them before it launches again.
  function unitHolder(sessions, { system, mode, unit }) {
    let holder = null;
    sessions.forEach((session) => {
      if (session.state === "exited") return;
      const joined =
        session.system === system &&
        session.mode === mode &&
        !shuttingDown(session);
      if (!joined) holder = session;
    });
    return holder;
  }

  function shuttingDown(session) {
    return !!session.shutting_down || session.state === "stopping";
  }

  // What holds a launch of `unit` of `system`, in words; `nameOf` names a
  // system by its id.
  function holderLabel(holder, unit, system, nameOf = (id) => id) {
    if (system !== undefined && holder.system !== system)
      return `${nameOf(holder.system)} runs`;
    if (holder.compute_unit === unit) return `in use · ${holder.mode}`;
    return shuttingDown(holder)
      ? `${holder.compute_unit} shutting down`
      : `${holder.mode} runs on ${holder.compute_unit}`;
  }

  // "main@10.0.0.12" of a peer behind an SSH forward, else "127.0.0.1:8782".
  function peerLabel(peer) {
    if (!peer) return "";
    if (peer.ssh) return peer.ssh;
    try {
      return new URL(peer.url).host;
    } catch (error) {
      return peer.url || "";
    }
  }

  // The peer record in lines: address, link state with its error, version, differences.
  function peerTitle(peer) {
    if (!peer) return "";
    const lines = [peer.ssh ? `${peer.url} over ssh ${peer.ssh}` : peer.url];
    lines.push(peer.error ? `${peer.state}: ${peer.error}` : peer.state);
    if (peer.version) lines.push(`version ${peer.version}`);
    (peer.mismatch || []).forEach((difference) => lines.push(difference));
    return lines.join("\n");
  }

  launch.unitActions = unitActions;
  launch.unitHolder = unitHolder;
  launch.holderLabel = holderLabel;
  launch.peerLabel = peerLabel;
  launch.peerTitle = peerTitle;
})();
