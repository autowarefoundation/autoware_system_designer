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
  const SETTINGS = "replay.settings";
  // Share of the bar each phase takes, by its place in the run.
  const PHASE_GROW = [1, 3, 1];
  // Phases a replay's progress lines name, in order, from the page configuration.
  const phaseList = () =>
    ((SD.replayRuntime || {}).phases || []).map((name, index) => ({
      name,
      label: name.charAt(0).toUpperCase() + name.slice(1),
      grow: PHASE_GROW[index] ?? 1,
    }));

  // What a replay job came to, as { level: ok | warning | failed | running, text },
  // with the `error` that stopped a replay short of its end.
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
        text: "Replay did not run to its end",
        error: result?.error || "",
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
      `${result.callbacks} callbacks, ${result.timer_fires} timer fires, ${result.outputs} outputs` +
      (result.lost_outputs
        ? `, ${result.lost_outputs} publishes never heard`
        : "")
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

  // Last stderr lines of the members a failed replay quotes, one fold a member,
  // open when it is the only one.
  function memberLogs(logs) {
    return logs.map((log) => {
      const fold = element("details", "replay-run-member-log");
      fold.open = logs.length === 1;
      const summary = element("summary", "", `stderr of ${log.member}`);
      summary.title = log.path;
      fold.append(
        summary,
        element("pre", "replay-run-member-lines", log.lines.join("\n")),
      );
      return fold;
    });
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
    const all = phaseList();
    const reached = detail.phases || {};
    const current = detail.progress?.phase ?? all[0]?.name;
    const phases = job.params?.check ? all.slice(0, 1) : all;
    return phases.map((phase) => {
      const fraction = reached[phase.name] ?? null;
      if (fraction === 1) return { phase, state: "done", width: 1 };
      if (running && phase.name === current)
        return { phase, state: "active", width: fraction };
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
        : phaseList().map((phase) => ({ phase, state: "pending", width: 0 }));
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
      const phase = states.find(
        (entry) => entry.phase.name === progress?.phase,
      )?.phase;
      step.textContent =
        progress?.step && phase ? `${phase.label}: ${progress.step}` : "";
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

  // Seconds of a span are kept to this step.
  const SPAN_STEP = 0.1;
  const snap = (seconds) => Math.round(seconds / SPAN_STEP) * SPAN_STEP;
  const tenths = (seconds) => String(Number(seconds.toFixed(1)));

  // Ends of a span as seconds from the bag's start: `from` at zero or more,
  // `to` after it or null for the bag's end, both within `length` when known.
  function spanEnds(from, to, length = null) {
    const limit = length ?? Infinity;
    const open = to === null || to === undefined || to === "";
    const end = open
      ? limit
      : Math.min(Math.max(snap(Number(to)), SPAN_STEP), limit);
    const start = Math.min(
      Math.max(snap(Number(from) || 0), 0),
      Math.max(end - SPAN_STEP, 0),
    );
    return { from: start, to: end >= limit ? null : end };
  }

  // Run options { start, duration } of a span; a span of the whole bag gives none.
  function spanOptions({ from, to }) {
    const options = {};
    if (from > 0) options.start = Number(from.toFixed(1));
    if (to !== null) options.duration = Number((to - from).toFixed(1));
    return options;
  }

  function clock(ns) {
    const d = new Date(ns / 1e6);
    const pad = (n) => String(n).padStart(2, "0");
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  // The replayed span of the bag: a brush over the bag's length with an end
  // handle each side, and the two ends as seconds from the bag's start.
  // `bagSpan()` gives { startNs, durationNs } of the bag or null; without it
  // the ends are typed.
  function spanTimeline(bagSpan) {
    const root = element("div", "replay-run-span");
    const brush = element("div", "replay-span-brush");
    const range = element("div", "replay-span-range");
    const handle = (label) => {
      const node = element("div", "replay-span-handle");
      node.tabIndex = 0;
      node.setAttribute("role", "slider");
      node.setAttribute("aria-label", label);
      return node;
    };
    const fromHandle = handle("Span start");
    const toHandle = handle("Span end");
    range.append(fromHandle, toHandle);
    brush.appendChild(range);
    const fromInput = numberInput("0", "0", "any");
    const toInput = numberInput("end", "0", "any");
    fromInput.setAttribute("aria-label", "Span start in seconds");
    toInput.setAttribute("aria-label", "Span end in seconds");
    const ends = element("span", "replay-span-ends");
    ends.append(fromInput, "–", toInput, "s");
    const note = element("span", "replay-span-note");
    const whole = element("button", "panel-button", "Whole bag");
    whole.type = "button";
    root.append(
      element("span", "replay-span-label", "Span"),
      brush,
      ends,
      note,
      whole,
    );

    // The span shown, and the brush gesture under way: { anchor } stretches
    // the span from a fixed second, { grab, from, to } shifts it whole. A span
    // of the whole bag has no room to shift; a press on it anchors a new one.
    let span = { from: 0, to: null };
    let drag = null;

    const length = () => {
      const bag = bagSpan();
      return bag ? bag.durationNs / 1e9 : null;
    };

    function render() {
      const bag = bagSpan();
      const total = length();
      const to = span.to ?? total;
      brush.classList.toggle("unknown", total === null);
      whole.disabled = span.from === 0 && span.to === null;
      if (document.activeElement !== fromInput)
        fromInput.value = span.from ? tenths(span.from) : "";
      if (document.activeElement !== toInput)
        toInput.value = span.to === null ? "" : tenths(span.to);
      toInput.placeholder = total === null ? "end" : tenths(total);
      if (total === null) {
        range.style.left = "0";
        range.style.width = "100%";
        note.textContent = to === null ? "" : `${tenths(to - span.from)} s`;
        return;
      }
      const left = Math.min(span.from / total, 1);
      range.style.left = `${100 * left}%`;
      range.style.width = `${100 * (Math.min(to / total, 1) - left)}%`;
      const at = (seconds) => clock(bag.startNs + seconds * 1e9);
      note.textContent = `${at(span.from)} – ${at(to)} · ${tenths(to - span.from)} of ${tenths(total)} s`;
      [
        [fromHandle, span.from],
        [toHandle, to],
      ].forEach(([node, seconds]) => {
        node.setAttribute("aria-valuemin", "0");
        node.setAttribute("aria-valuemax", tenths(total));
        node.setAttribute("aria-valuenow", tenths(seconds));
        node.setAttribute("aria-valuetext", `${tenths(seconds)} s`);
      });
    }

    function pick(from, to) {
      span = spanEnds(from, to, length());
      render();
    }

    function secondsAt(clientX) {
      const box = brush.getBoundingClientRect();
      const share = Math.min(Math.max((clientX - box.left) / box.width, 0), 1);
      return snap(share * length());
    }

    brush.addEventListener("pointerdown", (event) => {
      const total = length();
      if (total === null || event.button !== 0) return;
      const to = span.to ?? total;
      const at = secondsAt(event.clientX);
      if (event.target === fromHandle) drag = { anchor: to };
      else if (event.target === toHandle) drag = { anchor: span.from };
      else if (event.target === range && (span.from > 0 || span.to !== null))
        drag = { grab: at, from: span.from, to };
      else drag = { anchor: at };
      brush.setPointerCapture(event.pointerId);
      brush.classList.add("dragging");
      event.preventDefault();
    });
    brush.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const total = length();
      const at = secondsAt(event.clientX);
      if ("grab" in drag) {
        const shift = Math.min(
          Math.max(at - drag.grab, -drag.from),
          total - drag.to,
        );
        pick(drag.from + shift, drag.to + shift);
      } else if (at !== drag.anchor)
        pick(Math.min(at, drag.anchor), Math.max(at, drag.anchor));
    });
    const endDrag = () => {
      drag = null;
      brush.classList.remove("dragging");
    };
    brush.addEventListener("pointerup", endDrag);
    brush.addEventListener("pointercancel", endDrag);
    // Arrow keys move a focused end by a second, with Shift by ten; Home and
    // End to the farthest it reaches.
    [fromHandle, toHandle].forEach((node) => {
      node.addEventListener("keydown", (event) => {
        const total = length();
        if (total === null) return;
        const unit = event.shiftKey ? 10 : 1;
        const step = {
          ArrowLeft: -unit,
          ArrowDown: -unit,
          ArrowRight: unit,
          ArrowUp: unit,
          Home: -total,
          End: total,
        }[event.key];
        if (!step) return;
        event.preventDefault();
        const to = span.to ?? total;
        if (node === fromHandle)
          pick(Math.min(Math.max(span.from + step, 0), to - SPAN_STEP), to);
        else pick(span.from, Math.max(to + step, span.from + SPAN_STEP));
      });
    });
    const typed = () => {
      if (!fromInput.checkValidity() || !toInput.checkValidity()) return;
      pick(Number(fromInput.value), toInput.value);
    };
    [fromInput, toInput].forEach((input) => {
      input.addEventListener("input", typed);
      input.addEventListener("blur", render);
    });
    whole.addEventListener("click", () => pick(0, null));

    render();
    return {
      element: root,
      render,
      // Run options { start, duration } of the span.
      options: () => spanOptions(span),
      set(run) {
        const from = run.start ?? 0;
        span = spanEnds(
          from,
          run.duration === undefined || run.duration === null
            ? null
            : from + run.duration,
        );
        render();
      },
    };
  }

  // Settings of a run, folded and grouped by category two groups abreast: the
  // run's own (`own`, a list of { label, note, control }) and the driver
  // settings of the runtime, each with its description and an input that
  // shows the default while empty. A setting left empty takes the default of
  // the run. `ownSet()` counts the run's own that are set.
  function settingsFold(daemon, own, ownSet) {
    const root = element("details", "replay-run-settings");
    const count = element("span", "replay-block-count");
    const summary = element("summary", "", "Settings");
    summary.appendChild(count);
    const groups = element("div", "replay-run-setting-groups");
    root.append(summary, groups);
    // Driver settings the daemon listed, and the values set, by name.
    let settings = [];
    let values = {};

    function renderCount() {
      const set = Object.keys(values).length + ownSet();
      count.textContent = set ? `${set} set` : "defaults";
    }

    function group(title, rows) {
      const node = element("section", "replay-run-setting-group");
      node.appendChild(element("h4", "replay-run-setting-title", title));
      rows.forEach(({ label, note, control, title: tip }) => {
        const row = element("label", "replay-run-setting");
        if (tip) row.title = tip;
        const text = element("span", "replay-run-setting-text");
        text.appendChild(element("span", "replay-run-setting-name", label));
        if (note)
          text.appendChild(element("span", "replay-run-setting-note", note));
        row.append(text, control);
        node.appendChild(row);
      });
      return node;
    }

    function driverRow(setting) {
      const seconds = setting.unit === "s";
      const input = numberInput(
        `${setting.default}${seconds ? " s" : ""}`,
        seconds ? "0" : "1",
        seconds ? "any" : "1",
      );
      input.value = values[setting.name] ?? "";
      input.setAttribute("aria-label", setting.name);
      input.addEventListener("input", () => {
        if (input.value === "" || !input.checkValidity())
          delete values[setting.name];
        else values[setting.name] = Number(input.value);
        renderCount();
      });
      return {
        label: setting.name,
        note: setting.description,
        control: input,
        title: `Default: ${setting.default}${seconds ? " s" : ""}`,
      };
    }

    function render() {
      renderCount();
      // Driver settings by category, in the order the daemon lists them.
      const categories = new Map();
      settings.forEach((setting) => {
        const name = setting.category || "driver";
        if (!categories.has(name)) categories.set(name, []);
        categories.get(name).push(driverRow(setting));
      });
      groups.replaceChildren(
        group("Run", own),
        ...[...categories].map(([name, rows]) =>
          group(name.charAt(0).toUpperCase() + name.slice(1), rows),
        ),
      );
    }

    function load() {
      if (!daemon.connected || !daemon.health || !daemon.offers(SETTINGS))
        return;
      daemon.rpc(SETTINGS).then(
        (result) => {
          if (JSON.stringify(result.driver) === JSON.stringify(settings))
            return;
          settings = result.driver;
          render();
        },
        () => {},
      );
    }

    own.forEach(({ control }) =>
      control.addEventListener("input", renderCount),
    );
    SD.on("daemon.health", load);
    SD.on("daemon.connection", load);
    load();
    render();
    return {
      element: root,
      // Driver values set, by name; empty when every setting is at its default.
      values: () => ({ ...values }),
      set(next) {
        values = { ...(next || {}) };
        root.open = Object.keys(values).length + ownSet() > 0;
        render();
      },
    };
  }

  // `request()` gives { params } of a replay, or { blocker } naming what it
  // lacks; `bagSpan()` the time span of the bag, or null.
  function runPanel(daemon, request, bagSpan = () => null) {
    const root = element("div", "replay-block");
    const span = spanTimeline(bagSpan);
    const leadIn = numberInput("default", "0", "any");
    const domain = numberInput("environment's", "0", "1");
    const saveReference = document.createElement("input");
    saveReference.type = "checkbox";
    const option = (label, control) => {
      const wrapper = element("label", "replay-run-option", label);
      wrapper.appendChild(control);
      return wrapper;
    };
    const jobSelect = document.createElement("select");
    jobSelect.setAttribute("aria-label", "Replay job");
    const jobOption = option("Job", jobSelect);
    jobOption.classList.add("replay-run-job");
    const settings = settingsFold(
      daemon,
      [
        {
          label: "lead_in",
          note: "Seconds from the span's start over which only the ambient inputs and the last message of each latched topic are fed.",
          control: leadIn,
        },
        {
          label: "domain_id",
          note: "ROS domain of the replay.",
          control: domain,
        },
      ],
      () => [leadIn, domain].filter((input) => input.value !== "").length,
    );
    const check = element("button", "panel-button", "Pre-flight");
    const run = element("button", "panel-button primary", "Run replay");
    const cancel = element("button", "panel-button", "Cancel");
    [check, run, cancel].forEach((button) => (button.type = "button"));
    const hint = element("span", "panel-hint blocked");
    const buttons = element("div", "replay-run-buttons");
    buttons.append(
      check,
      run,
      cancel,
      option("Save outputs as the reference", saveReference),
      hint,
      jobOption,
    );
    const error = element("div", "panel-banner panel-banner-error");
    error.hidden = true;

    const status = element("div", "replay-run-outcome");
    const progress = progressBar();
    const failure = element("div", "replay-run-error");
    failure.hidden = true;
    const memberLogList = element("div", "replay-run-member-logs");
    const counts = element("div", "replay-run-counts");
    const runDir = element("div", "replay-run-dir");
    const findingList = element("ul", "replay-run-findings");
    const logHost = element("div", "replay-run-log");
    const report = element("div", "replay-run-report");
    report.append(
      status,
      progress.element,
      failure,
      memberLogList,
      counts,
      runDir,
      findingList,
      logHost,
    );
    // Log shown before the first replay job.
    const idleLog = terminal("No replay run yet.");
    logHost.appendChild(idleLog.element);
    root.append(span.element, settings.element, buttons, error, report);

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
        failure.hidden = true;
        memberLogList.replaceChildren();
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
      const { level, text, error: stopped } = outcome(job);
      status.className = `replay-run-outcome ${level}`;
      status.textContent = text;
      progress.render(job);
      failure.textContent = stopped || "";
      failure.hidden = !stopped;
      const logs = job.detail?.result?.member_logs || [];
      // Folds are kept while the quoted logs stay the same.
      const logKey = `${job.id} ${JSON.stringify(logs)}`;
      if (memberLogList.dataset.key !== logKey) {
        memberLogList.dataset.key = logKey;
        memberLogList.replaceChildren(...memberLogs(logs));
      }
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
      span.render();
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
        Object.assign(extra, span.options());
        if (leadIn.value !== "") extra.lead_in = Number(leadIn.value);
        const set = settings.values();
        if (Object.keys(set).length) extra.driver = set;
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

    // Run options as a setup keeps them: lead-in and driver settings apart, as the configuration holds them.
    function getOptions() {
      const run = { save_reference: saveReference.checked, ...span.options() };
      if (domain.value !== "") run.domain_id = Number(domain.value);
      return {
        run,
        leadIn: leadIn.value === "" ? null : Number(leadIn.value),
        driver: settings.values(),
      };
    }

    function setOptions(run, leadInValue, driverValues) {
      span.set(run);
      domain.value = run.domain_id ?? "";
      saveReference.checked = !!run.save_reference;
      leadIn.value = leadInValue ?? "";
      settings.set(driverValues);
    }

    return { element: root, refresh, getOptions, setOptions };
  }

  replay.outcome = outcome;
  replay.findings = findings;
  replay.phaseStates = phaseStates;
  replay.spanEnds = spanEnds;
  replay.spanOptions = spanOptions;
  replay.runPanel = runPanel;
})();
