// Replay: a replay started through the daemon as a job: its options with the
// job followed, the pre-flight and run buttons, the outcome the job reports
// and its log. The
// Run section around it gives its heading.
(function () {
  const SD = window.SD;
  const { element, terminal } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const JOB_KIND = "replay";
  const START = "replay.start";
  // Phases a replay's progress lines name, in order, with their share of the bar.
  const PHASES = [
    { name: "pre-process", label: "Pre-process", grow: 1 },
    { name: "replay", label: "Replay", grow: 3 },
    { name: "post-process", label: "Post-process", grow: 1 },
  ];

  // What a replay job came to, as { level: ok | warning | failed | running, text }.
  function outcome(job) {
    const detail = job.detail || {};
    const errors = (detail.problems || []).filter((p) => p.level === "error");
    if (job.state === "running")
      return {
        level: "running",
        text: job.cancel_requested ? "Cancelling" : "Running",
      };
    if (job.state === "cancelled")
      return { level: "warning", text: "Cancelled" };
    if (errors.length)
      return {
        level: "failed",
        text: `Pre-flight failed: ${errors.length} error${errors.length > 1 ? "s" : ""}`,
      };
    if (job.params?.check)
      return job.state === "succeeded"
        ? { level: "ok", text: "Pre-flight passed" }
        : { level: "failed", text: "Pre-flight did not run" };
    const result = detail.result;
    if (!result || !result.completed)
      return {
        level: "failed",
        text: `Replay did not run to its end${result?.error ? `: ${result.error}` : ""}`,
      };
    if (detail.verdict)
      return detail.verdict.passed
        ? { level: "ok", text: "Outputs match the reference" }
        : { level: "failed", text: "Outputs differ from the reference" };
    if (job.params?.save_reference && job.state === "succeeded")
      return { level: "ok", text: "Completed; outputs saved as the reference" };
    return job.state === "succeeded"
      ? { level: "warning", text: "Completed; no reference to compare with" }
      : { level: "failed", text: "Replay failed" };
  }

  function countsLine(result) {
    if (!result || !result.completed) return "";
    const seconds = ((result.end_ns - result.start_ns) / 1e9).toFixed(3);
    return (
      `${seconds} s of ROS time in ${result.wall_s} s: ${result.messages} messages in, ` +
      `${result.callbacks} callbacks, ${result.timer_fires} timer fires, ${result.outputs} outputs`
    );
  }

  // Lines under the outcome: problems, violations and the topics that differ.
  function findings(job) {
    const detail = job.detail || {};
    const lines = [];
    (detail.problems || []).forEach((p) =>
      lines.push({ level: p.level, text: p.message }),
    );
    (detail.result?.endpoint_problems || []).forEach((p) =>
      lines.push({ level: p.level, text: p.message }),
    );
    (detail.result?.violations || []).forEach((v) =>
      lines.push({
        level: "warning",
        text: `outside the granted order: ${Object.values(v).join(" ")}`,
      }),
    );
    (detail.verdict?.topics || [])
      .filter((topic) => topic.status !== "match")
      .forEach((topic) =>
        lines.push({
          level: topic.status === "ignored" ? "warning" : "error",
          text: `${topic.topic}: ${topic.status}${topic.difference?.detail ? `, ${topic.difference.detail}` : ""}`,
        }),
      );
    return lines;
  }

  // State of each phase segment of a job, as { phase, state, width (0..1) or
  // null for an unknown share }, with state done | active | stopped | pending:
  // a phase is done once it reported fraction 1, active while the running job
  // is on it, stopped when the job left it otherwise. Null when the job
  // reported no progress. A pre-flight has its first phase only.
  function phaseStates(job) {
    const detail = job.detail || {};
    const running = job.state === "running";
    if (!detail.progress && !running) return null;
    const reached = detail.phases || {};
    const current = detail.progress?.phase ?? PHASES[0].name;
    const phases = job.params?.check ? PHASES.slice(0, 1) : PHASES;
    return phases.map((phase) => {
      const fraction = reached[phase.name] ?? null;
      if (fraction === 1) return { phase, state: "done", width: 1 };
      if (running && phase.name === current) return { phase, state: "active", width: fraction };
      if (phase.name in reached || phase.name === current)
        return { phase, state: "stopped", width: fraction };
      return { phase, state: "pending", width: 0 };
    });
  }

  // The run's phases as segments, each filled by its share done, and the step
  // the current phase is on; every phase pending before the first job.
  function progressBar() {
    const root = element("div", "replay-run-progress");
    const segments = element("div", "replay-run-phases");
    const step = element("div", "replay-run-step");
    root.append(segments, step);

    function render(job) {
      const states = job
        ? phaseStates(job)
        : PHASES.map((phase) => ({ phase, state: "pending", width: 0 }));
      root.hidden = !states;
      if (!states) return;
      const stoppedClass = job?.state === "cancelled" ? "cancelled" : "failed";
      segments.replaceChildren(
        ...states.map(({ phase, state, width }) => {
          const segment = element(
            "div",
            `replay-run-phase ${state === "stopped" ? stoppedClass : state}`,
          );
          segment.style.flexGrow = phase.grow;
          const track = element("div", "replay-run-phase-track");
          const fill = element("div", "replay-run-phase-fill");
          if (width === null) fill.classList.add("indeterminate");
          else fill.style.width = `${(width * 100).toFixed(1)}%`;
          track.appendChild(fill);
          const label = element("span", "replay-run-phase-label", phase.label);
          if (state === "active" && width !== null && width < 1)
            label.textContent += ` ${Math.floor(width * 100)}%`;
          segment.append(label, track);
          return segment;
        }),
      );
      const progress = job?.detail?.progress;
      const phase = PHASES.find((entry) => entry.name === progress?.phase);
      step.textContent = progress?.step && phase ? `${phase.label}: ${progress.step}` : "";
    }

    return { element: root, render };
  }

  function numberInput(placeholder, min, step) {
    const input = element("input", "panel-search replay-run-number");
    input.type = "number";
    input.min = min;
    input.step = step;
    input.placeholder = placeholder;
    return input;
  }

  // `request()` gives { params } of a replay, or { blocker } naming what it lacks.
  function runPanel(daemon, request) {
    const root = element("div", "replay-block");
    const duration = numberInput("whole bag", "0", "any");
    const domain = numberInput("environment's", "0", "1");
    const saveReference = document.createElement("input");
    saveReference.type = "checkbox";
    const option = (label, control) => {
      const wrapper = element("label", "replay-run-option", label);
      wrapper.appendChild(control);
      return wrapper;
    };
    const options = element("div", "replay-run-options");
    const jobSelect = document.createElement("select");
    jobSelect.setAttribute("aria-label", "Replay job");
    const jobOption = option("Job", jobSelect);
    jobOption.classList.add("replay-run-job");
    options.append(
      option("Duration (s)", duration),
      option("ROS domain id", domain),
      option("Save outputs as the reference", saveReference),
      jobOption,
    );
    const check = element("button", "panel-button", "Pre-flight");
    const run = element("button", "panel-button primary", "Run replay");
    const cancel = element("button", "panel-button", "Cancel");
    [check, run, cancel].forEach((button) => (button.type = "button"));
    const hint = element("span", "panel-hint blocked");
    const buttons = element("div", "replay-run-buttons");
    buttons.append(check, run, cancel, hint);
    const error = element("div", "panel-banner panel-banner-error");
    error.hidden = true;

    const status = element("div", "replay-run-outcome");
    const progress = progressBar();
    const counts = element("div", "replay-run-counts");
    const runDir = element("div", "replay-run-dir");
    const findingList = element("ul", "replay-run-findings");
    const logHost = element("div", "replay-run-log");
    const report = element("div", "replay-run-report");
    report.append(
      status,
      progress.element,
      counts,
      runDir,
      findingList,
      logHost,
    );
    // Log shown before the first replay job.
    const idleLog = terminal("No replay run yet.");
    logHost.appendChild(idleLog.element);
    root.append(options, buttons, error, report);

    // The followed job: its id, terminal and the sequence of the last line shown.
    let followed = null;

    const replayJobs = () => daemon.jobs().filter((j) => j.kind === JOB_KIND);

    function showError(text) {
      error.textContent = text || "";
      error.hidden = !text;
    }

    function append(firstSeq, lines) {
      const skip = Math.max(followed.lastSeq + 1 - firstSeq, 0);
      if (skip >= lines.length) return;
      followed.log.append(lines.slice(skip));
      followed.lastSeq = firstSeq + lines.length - 1;
    }

    function fetchLog() {
      const target = followed;
      if (!target) return;
      daemon
        .rpc("jobs.get", { job: target.id, since: target.lastSeq })
        .then((result) => {
          if (followed !== target) return;
          append(result.log.first_seq, result.log.lines);
        })
        .catch((err) => showError(err.message));
    }

    function follow(id) {
      if (followed?.id === id) return;
      followed = id
        ? { id, log: terminal("No output yet."), lastSeq: 0 }
        : null;
      logHost.replaceChildren((followed ? followed.log : idleLog).element);
      fetchLog();
    }

    function renderReport() {
      const jobs = replayJobs();
      jobOption.hidden = !jobs.length;
      if (!jobs.length) {
        follow(null);
        status.textContent = counts.textContent = runDir.textContent = "";
        progress.render(null);
        findingList.replaceChildren();
        return;
      }
      if (!followed || !jobs.some((job) => job.id === followed.id))
        follow(jobs[0].id);
      SD.widgets.setOptions(
        jobSelect,
        jobs.map((job) => [job.id, `${job.id} · ${job.title}`]),
      );
      jobSelect.value = followed.id;
      const job = jobs.find((entry) => entry.id === followed.id);
      const { level, text } = outcome(job);
      status.className = `replay-run-outcome ${level}`;
      status.textContent = text;
      progress.render(job);
      counts.textContent = countsLine(job.detail?.result);
      runDir.textContent = job.detail?.run_dir || "";
      findingList.replaceChildren(
        ...findings(job).map((line) =>
          element("li", `replay-run-finding ${line.level}`, line.text),
        ),
      );
    }

    function refresh() {
      const offered = !!daemon.health && daemon.offers(START);
      root.hidden = !offered;
      if (!offered) return;
      const { blocker } = request();
      const running = replayJobs().find((job) => job.state === "running");
      const text = !daemon.connected
        ? "The daemon is not connected."
        : running
          ? ""
          : blocker || "";
      hint.textContent = text;
      check.disabled = run.disabled = !!text || !!running;
      cancel.hidden = !running;
      cancel.disabled = !!running?.cancel_requested;
      renderReport();
    }

    function start(checkOnly) {
      const { params, blocker } = request();
      if (blocker) return;
      const extra = { check: checkOnly };
      if (!checkOnly) {
        extra.save_reference = saveReference.checked;
        if (duration.value) extra.duration = Number(duration.value);
      }
      if (domain.value !== "") extra.domain_id = Number(domain.value);
      check.disabled = run.disabled = true;
      daemon
        .rpc(START, { ...params, ...extra })
        .then((job) => {
          showError("");
          follow(job.id);
          daemon.upsertJob(job);
        })
        .catch((err) => {
          showError(err.message);
          refresh();
        });
    }

    check.addEventListener("click", () => start(true));
    run.addEventListener("click", () => start(false));
    cancel.addEventListener("click", () => {
      const running = replayJobs().find((job) => job.state === "running");
      if (!running) return;
      daemon
        .rpc("jobs.cancel", { job: running.id })
        .then(daemon.upsertJob, (err) => showError(err.message));
    });
    jobSelect.addEventListener("change", () => {
      follow(jobSelect.value);
      renderReport();
    });
    SD.on("daemon.job.log", (batch) => {
      if (!followed || batch.job !== followed.id) return;
      if (batch.first_seq > followed.lastSeq + 1) fetchLog();
      else append(batch.first_seq, batch.lines);
    });
    SD.on("daemon.jobs", refresh);
    SD.on("daemon.health", refresh);
    SD.on("daemon.connection", ({ connected }) => {
      if (connected) fetchLog();
      refresh();
    });

    return { element: root, refresh };
  }

  replay.outcome = outcome;
  replay.findings = findings;
  replay.phaseStates = phaseStates;
  replay.runPanel = runPanel;
})();
