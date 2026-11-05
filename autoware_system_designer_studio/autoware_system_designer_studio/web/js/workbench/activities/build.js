// Build activity: build options and the job list beside the selected job's
// log. A started build becomes the selected job.
(function () {
  const SD = window.SD;
  const { element, field, boundSelect, terminal } = SD.widgets;
  const BUILD_KIND = "build";

  function stateLabel(job) {
    if (job.state === "running") return job.cancel_requested ? "cancelling" : "running";
    if (job.state === "failed" && job.exit_code !== null) return `failed (${job.exit_code})`;
    return job.state;
  }

  // Header and log of one job, following the event stream; gaps refetch through jobs.get.
  function jobLog(main, daemon, onError) {
    const empty = element("p", "main-empty", "Select a build job, or start one.");
    const head = element("div", "main-view-head");
    const title = element("span", "main-view-title");
    const state = element("span", "job-state");
    head.append(title, state);
    const argv = element("code", "job-argv");
    const packages = element("div", "job-packages");
    const meta = element("div", "job-meta");
    meta.append(argv, packages);
    const log = terminal("No output yet.");
    const view = element("div", "log-view");
    view.append(head, meta, log.element);
    main.append(empty, view);

    let selected = null; // { id, lastSeq }

    function renderHeader(job) {
      title.textContent = `${job.id} · ${job.title}`;
      state.textContent = stateLabel(job);
      state.className = `job-state ${job.state}`;
      argv.textContent = job.argv.join(" ");
      const detail = job.detail || {};
      const built = detail.built_packages || [];
      packages.textContent = built.length
        ? `Started ${built.length} package(s); last: ${built[built.length - 1]}`
        : (detail.packages || []).length
          ? `Selected: ${detail.packages.join(", ")}`
          : "";
    }

    function append(firstSeq, lines) {
      if (!selected || !lines.length) return;
      const skip = Math.max(selected.lastSeq + 1 - firstSeq, 0);
      if (skip >= lines.length) return;
      log.append(lines.slice(skip));
      selected.lastSeq = firstSeq + lines.length - 1;
    }

    function fetch() {
      const target = selected;
      if (!target) return;
      daemon
        .rpc("jobs.get", { job: target.id, since: target.lastSeq })
        .then((result) => {
          if (selected !== target) return;
          daemon.upsertJob(result.job);
          renderHeader(result.job);
          append(result.log.first_seq, result.log.lines);
        })
        .catch((error) => onError(error.message));
    }

    function select(id) {
      if (selected?.id === id) return;
      selected = id ? { id, lastSeq: 0 } : null;
      empty.hidden = !!selected;
      view.hidden = !selected;
      log.clear();
      const job = id && daemon.jobs().find((j) => j.id === id);
      if (job) renderHeader(job);
      fetch();
    }

    SD.on("daemon.job", (job) => {
      if (selected?.id === job.id) renderHeader(job);
    });
    SD.on("daemon.job.log", (batch) => {
      if (!selected || batch.job !== selected.id) return;
      if (batch.first_seq > selected.lastSeq + 1) fetch();
      else append(batch.first_seq, batch.lines);
    });
    // No replay across reconnects; the log resynchronises through the request API.
    SD.on("daemon.connection", ({ connected }) => {
      if (connected) fetch();
    });

    view.hidden = true;
    return { select };
  }

  SD.workbench.registerActivity({
    id: "build",
    title: "Build",
    order: 30,
    daemon: true,
    inspector: false,
    scope: ["unit"],
    requires: ["build.start", "jobs.list", "jobs.get", "jobs.cancel"],
    icon: "M3 6.5L10 3l7 3.5v7L10 17l-7-3.5zM3 6.5L10 10l7-3.5M10 10v7",
    mount({ side, main }, { config, store, daemon }) {
      store.define("job", "");
      store.syncUrl();
      // This daemon's view of the system: undefined until systems.list answers, null when absent.
      let systemView;

      const banner = element("div", "panel-banner panel-banner-warning");
      banner.hidden = true;
      const error = element("div", "panel-banner panel-banner-error");
      error.hidden = true;
      side.append(banner, error);

      const target = document.createElement("select");
      SD.widgets.setOptions(target, [
        ["deployment", "Deployment package"],
        ["workspace", "Packages up to a compute unit"],
      ]);
      const unitField = field(
        "Compute unit",
        boundSelect("unit", [["", "Select a unit"], ...config.computeUnits.map((u) => [u.name, u.name])]),
      );
      const hint = element("p", "panel-hint");
      const start = element("button", "panel-button primary", "Start build");
      start.type = "button";
      side.append(field("Target", target), unitField, hint, start);

      side.appendChild(element("div", "panel-section-title", "Jobs"));
      const jobList = element("div", "job-list");
      side.appendChild(jobList);

      function showError(text) {
        error.textContent = text || "";
        error.hidden = !text;
      }

      const log = jobLog(main, daemon, showError);

      // Units of the selected mode this daemon can build: local and with a build script.
      function buildableUnits() {
        if (!systemView) return [];
        const scripted = (systemView.build_scripts || {})[store.get("mode")] || [];
        return systemView.compute_units
          .filter((unit) => unit.status === "available" && scripted.includes(unit.name))
          .map((unit) => unit.name);
      }

      function buildRunning() {
        return daemon.jobs().some((job) => job.kind === BUILD_KIND && job.state === "running");
      }

      function refreshForm() {
        const workspaceTarget = target.value === "workspace";
        const unit = store.get("unit");
        unitField.hidden = !workspaceTarget;
        let blocker = "";
        if (systemView === undefined) blocker = "Loading the daemon's view of this system…";
        else if (!systemView) blocker = "This system is not in the daemon's install tree.";
        else if (!daemon.health?.workspace) blocker = "The daemon has no build workspace (start it with --workspace).";
        else if (workspaceTarget && !unit) blocker = "Select a compute unit.";
        else if (workspaceTarget && !buildableUnits().includes(unit))
          blocker = `No build script for ${unit} in mode ${store.get("mode")} on this daemon.`;
        else if (buildRunning()) blocker = "A build is running; one runs at a time.";
        hint.textContent = blocker || (workspaceTarget ? `colcon build up to ${unit}` : `colcon build ${config.packageName}`);
        hint.classList.toggle("blocked", !!blocker);
        start.disabled = !!blocker || !daemon.connected;
        banner.textContent = daemon.health?.restart_required
          ? "The daemon's own package was rebuilt; restart the daemon to run the new code."
          : "";
        banner.hidden = !banner.textContent;
      }

      function renderJobs(jobs) {
        const builds = jobs.filter((job) => job.kind === BUILD_KIND);
        jobList.replaceChildren();
        if (!builds.length) {
          jobList.appendChild(element("p", "panel-empty", "No builds yet."));
          return;
        }
        builds.forEach((job) => {
          const item = element("div", "job-item");
          item.classList.toggle("selected", job.id === store.get("job"));
          item.addEventListener("click", () => store.set("job", job.id));
          const head = element("div", "job-item-head");
          head.append(element("span", `job-state ${job.state}`, stateLabel(job)), element("span", "job-id", job.id));
          item.append(head, element("div", "job-title", job.title));
          if (job.state === "running") {
            const cancel = element("button", "panel-button", job.cancel_requested ? "Cancelling" : "Cancel");
            cancel.type = "button";
            cancel.disabled = job.cancel_requested;
            cancel.addEventListener("click", (e) => {
              e.stopPropagation();
              daemon.rpc("jobs.cancel", { job: job.id }).then(daemon.upsertJob, (err) => showError(err.message));
            });
            item.appendChild(cancel);
          }
          jobList.appendChild(item);
        });
      }

      async function loadSystem() {
        try {
          systemView = await daemon.system(config.systemId);
          showError("");
        } catch (err) {
          showError(err.message);
        }
        refreshForm();
      }

      start.addEventListener("click", () => {
        const params = { system: config.systemId, target: target.value };
        if (target.value === "workspace") {
          params.mode = store.get("mode");
          params.compute_unit = store.get("unit");
        }
        start.disabled = true;
        daemon
          .rpc("build.start", params)
          .then((job) => {
            daemon.upsertJob(job);
            store.set("job", job.id);
            showError("");
          })
          .catch((err) => {
            showError(err.message);
            refreshForm();
          });
      });

      target.addEventListener("change", refreshForm);
      ["mode", "unit"].forEach((key) => store.onChange(key, refreshForm));
      store.onChange("job", (id) => {
        log.select(id);
        renderJobs(daemon.jobs());
      });
      SD.on("daemon.jobs", (jobs) => {
        renderJobs(jobs);
        refreshForm();
      });
      SD.on("daemon.health", refreshForm);
      SD.on("daemon.connection", refreshForm);
      SD.on("daemon.systems.changed", loadSystem);

      renderJobs(daemon.jobs());
      log.select(store.get("job"));
      loadSystem();
    },
  });
})();
