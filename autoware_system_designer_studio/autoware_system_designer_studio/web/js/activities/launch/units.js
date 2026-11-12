// Launch: what a compute unit offers from its reach on the daemon, its session
// of the shown mode and the session holding it, and the labels of the peer
// daemon serving a unit.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  const UNREACHABLE = "unreachable";

  // `status` is the unit's reach in systems.list, `session` its session of the
  // shown mode, `holder` the live session a launch would wait for. A session of
  // an unreachable unit is the one its daemon last reported.
  function unitActions({ status, session, holder }) {
    const active = !!session && session.state !== "exited";
    const reachable = status !== UNREACHABLE;
    return {
      active,
      reachable,
      canLaunch: status === "available" && !active && !holder,
      canShutdown: active && reachable && !session.shutting_down,
      canMeasure:
        active &&
        reachable &&
        !!session.connected &&
        !!session.control?.measure_armed,
    };
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
  launch.peerLabel = peerLabel;
  launch.peerTitle = peerTitle;
})();
