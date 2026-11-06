// Build: the selected job's command line and package progress over its log.
// Log lines arrive on the daemon's event stream by sequence number; a gap, or
// a reconnect, resynchronises through jobs.get from the last line shown.
(function () {
  const SD = window.SD;
  const { element, terminal, mainView } = SD.widgets;
  const build = (SD.activities.build ||= {});

  function stateLabel(job) {
    if (job.state === "running")
      return job.cancel_requested ? "cancelling" : "running";
    if (job.state === "failed" && job.exit_code !== null)
      return `failed (${job.exit_code})`;
    return job.state;
  }

  function packagesLine(job) {
    const detail = job.detail || {};
    const built = detail.built_packages || [];
    const selected = detail.packages || [];
    if (built.length)
      return `Started ${built.length} package(s); last: ${built[built.length - 1]}`;
    return selected.length ? `Selected: ${selected.join(", ")}` : "";
  }

  // Returns { select(id) }; `showError(text)` reports a failed log request.
  function jobLog(container, daemon, showError) {
    const view = mainView({ empty: "Select a build to follow its log." });
    const argv = element("div", "job-argv");
    const packages = element("div", "job-packages");
    const meta = element("div", "job-meta");
    meta.append(argv, packages);
    const body = element("div", "pane");
    view.body.append(meta, body);
    container.appendChild(view.element);

    // The selected job: its id, terminal and the sequence of the last line shown.
    let selected = null;

    function renderHeader(job) {
      view.setTitle(`${job.id} · ${stateLabel(job)}`);
      argv.textContent = (job.argv || []).join(" ");
      packages.textContent = packagesLine(job);
    }

    function append(firstSeq, lines) {
      const skip = Math.max(selected.lastSeq + 1 - firstSeq, 0);
      if (skip >= lines.length) return;
      selected.log.append(lines.slice(skip));
      selected.lastSeq = firstSeq + lines.length - 1;
    }

    function fetchLog() {
      const target = selected;
      if (!target) return;
      daemon
        .rpc("jobs.get", { job: target.id, since: target.lastSeq })
        .then((result) => {
          if (selected !== target) return;
          renderHeader(result.job);
          append(result.log.first_seq, result.log.lines);
        })
        .catch((error) => showError(error.message));
    }

    function select(id) {
      if (!id) {
        selected = null;
        view.setEmpty("Select a build to follow its log.");
        return;
      }
      if (selected?.id === id) return;
      selected = { id, log: terminal("No output yet."), lastSeq: 0 };
      body.replaceChildren(selected.log.element);
      view.setTitle(id);
      argv.textContent = "";
      packages.textContent = "";
      view.setEmpty("");
      fetchLog();
    }

    SD.on("daemon.job.log", (batch) => {
      if (!selected || batch.job !== selected.id) return;
      if (batch.first_seq > selected.lastSeq + 1) fetchLog();
      else append(batch.first_seq, batch.lines);
    });
    SD.on("daemon.jobs", (jobs) => {
      const job = selected && jobs.find((entry) => entry.id === selected.id);
      if (job) renderHeader(job);
    });
    SD.on("daemon.connection", ({ connected }) => {
      if (connected) fetchLog();
    });

    return { select };
  }

  build.stateLabel = stateLabel;
  build.jobLog = jobLog;
})();
