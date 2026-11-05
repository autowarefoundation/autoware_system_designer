// Status bar: the selection of the shown activity, the page's data source and
// the daemon's running jobs.
(function () {
  const SD = window.SD;

  function item(id, text, className = "") {
    const node = document.getElementById(id);
    node.textContent = text;
    node.className = `status-item ${className}`.trim();
    node.hidden = !text;
  }

  function selection(parts) {
    item("status-selection", parts.filter(Boolean).join(" · "));
  }

  // `health` is the daemon's health result, null on a static page; `probeDone` once the probe answered.
  function source(health, probeDone) {
    if (!health) {
      item("status-source", probeDone ? "Static export" : "Checking for daemon…");
      item("status-workspace", "");
      item("status-restart", "");
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
      health.restart_required ? "Restart required: the daemon's package was rebuilt" : "",
      "status-warning",
    );
  }

  function jobs(list) {
    const running = list.filter((job) => job.state === "running");
    item(
      "status-jobs",
      running.length ? `${running.length} job${running.length > 1 ? "s" : ""} running` : "",
      "status-running",
    );
  }

  SD.statusbar = { selection, source, jobs };
})();
