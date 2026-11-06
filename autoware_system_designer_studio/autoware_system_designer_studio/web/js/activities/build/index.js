// Build activity: build options and the job list beside the selected job's
// log. A started build becomes the selected job.
(function () {
  const SD = window.SD;
  const { element, field, section, boundSelect } = SD.widgets;
  const { jobLog, stateLabel } = SD.activities.build;
  const BUILD_KIND = "build";

  SD.workbench.registerActivity({
    id: "build",
    title: "Build",
    order: 30,
    daemon: true,
    inspector: false,
    requires: ["build.start", "jobs.list", "jobs.get", "jobs.cancel"],
    icon: "M3 6.5L10 3l7 3.5v7L10 17l-7-3.5zM3 6.5L10 10l7-3.5M10 10v7",
    status: (store) => [store.get("unit") || "no unit"],
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
        boundSelect("unit", [
          ["", "Select a unit"],
          ...config.computeUnits.map((u) => [u.name, u.name]),
        ]),
      );
      const hint = element("p", "panel-hint");
      const start = element("button", "panel-button primary", "Start build");
      start.type = "button";
      side.append(field("Target", target), unitField, hint, start);

      const jobList = element("div", "job-list");
      side.appendChild(section("Jobs", jobList));

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
        else if (!daemon.health?.workspace)
          blocker = "The daemon has no build workspace (start it with --workspace).";
        else if (workspaceTarget && !unit) blocker = "Select a compute unit.";
        else if (workspaceTarget && !buildableUnits().includes(unit))
          blocker = `No build script for ${unit} in mode ${store.get("mode")} on this daemon.`;
        else if (buildRunning()) blocker = "A build is running; one runs at a time.";
        hint.textContent =
          blocker ||
          (workspaceTarget ? `colcon build up to ${unit}` : `colcon build ${config.packageName}`);
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
          head.append(
            element("span", `job-state ${job.state}`, stateLabel(job)),
            element("span", "job-id", job.id),
          );
          item.append(head, element("div", "job-title", job.title));
          if (job.state === "running") {
            const cancel = element(
              "button",
              "panel-button",
              job.cancel_requested ? "Cancelling" : "Cancel",
            );
            cancel.type = "button";
            cancel.disabled = job.cancel_requested;
            cancel.addEventListener("click", (e) => {
              e.stopPropagation();
              daemon
                .rpc("jobs.cancel", { job: job.id })
                .then(daemon.upsertJob, (err) => showError(err.message));
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
