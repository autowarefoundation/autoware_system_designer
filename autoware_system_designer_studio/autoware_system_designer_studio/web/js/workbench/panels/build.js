// Build panel: starts and cancels builds of this system on the daemon. Logs open
// on the terminals page; the panel lists jobs and their state only.
(function () {
  const SD = window.SD;
  const BUILD_KIND = "build";
  const TERMINALS_PAGE = "daemon/build.html";

  function stateLabel(job) {
    if (job.state === "running") return job.cancel_requested ? "cancelling" : "running";
    if (job.state === "failed" && job.exit_code !== null) return `failed (${job.exit_code})`;
    return job.state;
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  SD.workbench.registerPanel({
    id: "build",
    title: "Build",
    order: 30,
    daemon: true,
    requires: ["build.start", "jobs.list", "jobs.cancel"],
    icon: "M3 6.5L10 3l7 3.5v7L10 17l-7-3.5zM3 6.5L10 10l7-3.5M10 10v7",
    mount(body, { config, store, daemon }) {
      let systemView = null;

      const banner = element("div", "panel-banner panel-banner-warning");
      banner.hidden = true;
      const error = element("div", "panel-banner panel-banner-error");
      error.hidden = true;
      body.append(banner, error);

      const targetField = element("label", "panel-field", "Target");
      const target = document.createElement("select");
      [
        ["deployment", "Deployment package"],
        ["workspace", "Packages up to the selected unit"],
      ].forEach(([value, label]) => {
        const opt = element("option", "", label);
        opt.value = value;
        target.appendChild(opt);
      });
      targetField.appendChild(target);
      body.appendChild(targetField);

      const hint = element("p", "panel-hint");
      const start = element("button", "panel-button primary", "Start build");
      start.type = "button";
      body.append(hint, start);

      body.appendChild(element("div", "panel-section-title", "Jobs"));
      const jobList = element("div", "job-list");
      body.appendChild(jobList);

      function showError(text) {
        error.textContent = text || "";
        error.hidden = !text;
      }

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
        let blocker = "";
        if (!systemView) blocker = "This system is not in the daemon's install tree.";
        else if (!daemon.health?.workspace) blocker = "The daemon has no build workspace (start it with --workspace).";
        else if (workspaceTarget && !unit) blocker = "Select a compute unit in the top bar.";
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
          const head = element("div", "job-item-head");
          head.appendChild(element("span", `job-state ${job.state}`, stateLabel(job)));
          head.appendChild(element("span", "job-id", job.id));
          item.appendChild(head);
          item.appendChild(element("div", "job-title", job.title));
          const actions = element("div", "job-actions");
          const log = element("a", "", "Open log");
          log.href = `${daemon.pageUrl(TERMINALS_PAGE)}?job=${encodeURIComponent(job.id)}`;
          log.target = "_blank";
          log.rel = "noopener";
          actions.appendChild(log);
          if (job.state === "running") {
            const cancel = element("button", "panel-button", job.cancel_requested ? "Cancelling" : "Cancel");
            cancel.type = "button";
            cancel.disabled = job.cancel_requested;
            cancel.addEventListener("click", () => {
              daemon.rpc("jobs.cancel", { job: job.id }).then(daemon.upsertJob, (err) => showError(err.message));
            });
            actions.appendChild(cancel);
          }
          item.appendChild(actions);
          jobList.appendChild(item);
        });
      }

      async function loadSystem() {
        try {
          const result = await daemon.rpc("systems.list");
          systemView = result.systems.find((system) => system.id === config.systemId) || null;
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
            showError("");
          })
          .catch((err) => {
            showError(err.message);
            refreshForm();
          });
      });

      target.addEventListener("change", refreshForm);
      ["mode", "unit"].forEach((key) => store.onChange(key, refreshForm));
      SD.on("daemon.jobs", (jobs) => {
        renderJobs(jobs);
        refreshForm();
      });
      SD.on("daemon.health", refreshForm);
      SD.on("daemon.connection", refreshForm);
      SD.on("daemon.systems.changed", loadSystem);

      renderJobs(daemon.jobs());
      loadSystem();
    },
  });
})();
