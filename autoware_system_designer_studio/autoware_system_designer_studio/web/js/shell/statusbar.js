// Status bar: the selection of the shown activity, the page's data source, the
// daemon's peers and its running jobs.
(function () {
  const SD = window.SD;

  function item(id, text, className = "", title = "") {
    const node = document.getElementById(id);
    node.textContent = text;
    node.className = `status-item ${className}`.trim();
    node.title = title;
    node.hidden = !text;
  }

  // "Peers 2/3 · sub_ecu_2 unreachable": the connected peer daemons among all,
  // then the units of the others.
  function peers(health) {
    const all = health?.peers || [];
    const down = all.filter((peer) => peer.state !== "connected");
    const parts = [`Peers ${all.length - down.length}/${all.length}`];
    down.forEach((peer) =>
      parts.push(`${peer.units.join(", ")} ${peer.state}`),
    );
    item(
      "status-peers",
      all.length ? parts.join(" · ") : "",
      down.length ? "status-warning" : "",
      all
        .map((peer) => {
          const via = peer.ssh ? ` over ssh ${peer.ssh}` : "";
          const error = peer.error ? `: ${peer.error}` : "";
          return `${peer.units.join(", ")} · ${peer.url}${via} · ${peer.state}${error}`;
        })
        .join("\n"),
    );
  }

  function selection(parts) {
    item("status-selection", parts.filter(Boolean).join(" · "));
  }

  // `health` is the daemon's health result, null on a static page; `probeDone` once the probe answered.
  function source(health, probeDone) {
    if (!health) {
      item(
        "status-source",
        probeDone ? "Static export" : "Checking for daemon…",
      );
      item("status-workspace", "");
      item("status-restart", "");
      peers(null);
      return;
    }
    const version = health.version ? ` ${health.version}` : "";
    item("status-source", `Daemon${version} · pid ${health.pid}`);
    item(
      "status-workspace",
      health.workspace ? `Workspace ${health.workspace}` : "No build workspace",
    );
    item(
      "status-restart",
      health.restart_required
        ? "Restart required: the daemon's package was rebuilt"
        : "",
      "status-warning",
    );
    peers(health);
  }

  function jobs(list) {
    const running = list.filter((job) => job.state === "running");
    item(
      "status-jobs",
      running.length
        ? `${running.length} job${running.length > 1 ? "s" : ""} running`
        : "",
      "status-running",
    );
  }

  SD.statusbar = { selection, source, jobs };
})();
