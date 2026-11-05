// Build control page: starts and cancels build jobs, follows their state and log over the event stream.
(function () {
  "use strict";

  var CLIENT_HEADER = "X-System-Designer-Client";
  var LOG_LINES_SHOWN = 5000;

  var systems = [];
  var jobs = new Map();
  var selected = null; // { id, lastSeq, lines }
  var connected = false;
  var rpcId = 0;

  var el = function (id) {
    return document.getElementById(id);
  };

  function rpc(method, params) {
    rpcId += 1;
    return fetch("/api/rpc", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        [CLIENT_HEADER]: "build-page",
      },
      body: JSON.stringify({ id: rpcId, method: method, params: params || {} }),
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (body) {
        if (!body.ok)
          throw new Error(body.error.code + ": " + body.error.message);
        return body.result;
      });
  }

  function showError(text) {
    var banner = el("error-banner");
    banner.textContent = text || "";
    banner.hidden = !text;
  }

  function option(value, label) {
    var opt = document.createElement("option");
    opt.value = value;
    opt.textContent = label;
    return opt;
  }

  // -- form ---------------------------------------------------------------

  function currentSystem() {
    return systems.find(function (s) {
      return s.id === el("system").value;
    });
  }

  function fillSystemFields() {
    var system = currentSystem();
    var mode = el("mode");
    var previousMode = mode.value;
    mode.replaceChildren();
    if (system) {
      system.modes.forEach(function (m) {
        mode.appendChild(option(m.name, m.name));
      });
      var names = system.modes.map(function (m) {
        return m.name;
      });
      if (names.indexOf(previousMode) >= 0) mode.value = previousMode;
      else if (system.default_mode) mode.value = system.default_mode;
    }
    var workspace = el("target").value === "workspace";
    document.querySelectorAll(".workspace-only").forEach(function (node) {
      node.hidden = !workspace;
    });
    fillUnits();
  }

  // Units local to this daemon that have a build script in the selected mode.
  function fillUnits() {
    var system = currentSystem();
    var unit = el("unit");
    var previousUnit = unit.value;
    unit.replaceChildren();
    if (system) {
      var scripted = (system.build_scripts || {})[el("mode").value] || [];
      system.compute_units
        .filter(function (u) {
          return u.status === "available" && scripted.indexOf(u.name) >= 0;
        })
        .forEach(function (u) {
          unit.appendChild(
            option(
              u.name,
              u.description ? u.name + " (" + u.description + ")" : u.name,
            ),
          );
        });
      if (previousUnit) unit.value = previousUnit;
      if (!unit.value && unit.options.length) unit.selectedIndex = 0;
    }
    updateStartButton();
  }

  function updateStartButton() {
    var running = Array.from(jobs.values()).some(function (job) {
      return job.kind === "build" && job.state === "running";
    });
    var ready =
      !!currentSystem() &&
      (el("target").value !== "workspace" || !!el("unit").value);
    el("start").disabled = !connected || running || !ready;
  }

  function loadSystems() {
    return rpc("systems.list").then(function (result) {
      systems = result.systems;
      var select = el("system");
      var previous = select.value;
      select.replaceChildren();
      systems.forEach(function (s) {
        select.appendChild(option(s.id, s.name + " (" + s.package + ")"));
      });
      if (previous) select.value = previous;
      fillSystemFields();
    });
  }

  function startBuild(event) {
    event.preventDefault();
    var params = { system: el("system").value, target: el("target").value };
    if (params.target === "workspace") {
      params.mode = el("mode").value;
      params.compute_unit = el("unit").value;
    }
    el("start").disabled = true;
    rpc("build.start", params)
      .then(function (job) {
        upsertJob(job);
        selectJob(job.id);
      })
      .catch(function (err) {
        showError(err.message);
        updateStartButton();
      });
  }

  // -- jobs ---------------------------------------------------------------

  function upsertJob(job) {
    jobs.set(job.id, job);
    renderJobs();
    if (selected && selected.id === job.id) renderJobHeader(job);
    updateStartButton();
  }

  function renderJobs() {
    var body = el("jobs");
    body.replaceChildren();
    var list = Array.from(jobs.values()).sort(function (a, b) {
      return b.created_at - a.created_at;
    });
    if (!list.length) {
      var empty = document.createElement("tr");
      empty.innerHTML = '<td colspan="5" class="empty">No jobs</td>';
      body.appendChild(empty);
      return;
    }
    list.forEach(function (job) {
      var row = document.createElement("tr");
      if (selected && selected.id === job.id) row.className = "selected";
      var cells = [
        job.id,
        job.title,
        null,
        new Date(job.created_at * 1000).toLocaleTimeString(),
        null,
      ];
      cells.forEach(function (text) {
        row.appendChild(document.createElement("td")).textContent =
          text === null ? "" : text;
      });
      var state = document.createElement("span");
      state.className = "job-state " + job.state;
      state.textContent = stateLabel(job);
      row.children[2].appendChild(state);
      if (job.state === "running") {
        var cancel = document.createElement("button");
        cancel.textContent = job.cancel_requested ? "Cancelling" : "Cancel";
        cancel.disabled = job.cancel_requested;
        cancel.addEventListener("click", function (event) {
          event.stopPropagation();
          rpc("jobs.cancel", { job: job.id })
            .then(upsertJob)
            .catch(function (err) {
              showError(err.message);
            });
        });
        row.children[4].appendChild(cancel);
      }
      row.addEventListener("click", function () {
        selectJob(job.id);
      });
      body.appendChild(row);
    });
  }

  function stateLabel(job) {
    if (job.state === "running")
      return job.cancel_requested ? "cancelling" : "running";
    if (job.state === "failed" && job.exit_code !== null)
      return "failed (" + job.exit_code + ")";
    return job.state;
  }

  function renderJobHeader(job) {
    el("job-title").textContent = job.id + " " + stateLabel(job);
    el("job-argv").textContent = job.argv.join(" ");
    var detail = job.detail || {};
    var built = detail.built_packages || [];
    var total = (detail.packages || []).length;
    el("job-packages").textContent = built.length
      ? "Started " +
        built.length +
        " package(s); last: " +
        built[built.length - 1]
      : total
        ? "Selected: " + detail.packages.join(", ")
        : "";
  }

  function selectJob(id) {
    selected = { id: id, lastSeq: 0, lines: [] };
    el("job-view").hidden = false;
    el("job-log").textContent = "";
    renderJobs();
    fetchLog();
  }

  function fetchLog() {
    var target = selected;
    if (!target) return;
    rpc("jobs.get", { job: target.id, since: target.lastSeq })
      .then(function (result) {
        if (selected !== target) return;
        upsertJob(result.job);
        appendLines(result.log.first_seq, result.log.lines);
      })
      .catch(function (err) {
        showError(err.message);
      });
  }

  function appendLines(firstSeq, lines) {
    if (!selected || !lines.length) return;
    var skip = Math.max(selected.lastSeq + 1 - firstSeq, 0);
    if (skip >= lines.length) return;
    var fresh = lines.slice(skip);
    var log = el("job-log");
    var atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
    selected.lines = selected.lines.concat(fresh);
    selected.lastSeq = firstSeq + lines.length - 1;
    if (selected.lines.length > LOG_LINES_SHOWN * 1.2) {
      selected.lines = selected.lines.slice(-LOG_LINES_SHOWN);
      log.textContent = selected.lines.join("\n") + "\n";
    } else {
      log.appendChild(document.createTextNode(fresh.join("\n") + "\n"));
    }
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  function loadJobs() {
    return rpc("jobs.list").then(function (result) {
      jobs = new Map(
        result.jobs.map(function (job) {
          return [job.id, job];
        }),
      );
      renderJobs();
      updateStartButton();
      if (selected) fetchLog();
    });
  }

  function loadHealth() {
    return rpc("health").then(function (health) {
      el("workspace").textContent =
        health.workspace || "(none: builds unavailable)";
      el("restart-banner").hidden = !health.restart_required;
    });
  }

  // -- event stream -------------------------------------------------------

  function setConnected(value) {
    connected = value;
    var status = el("connection");
    status.textContent = value ? "Connected" : "Disconnected, retrying";
    status.className =
      "connection-status " + (value ? "connected" : "disconnected");
    updateStartButton();
  }

  function connect() {
    var source = new EventSource("/api/events");
    // The browser reconnects on its own; the next hello resynchronises.
    source.addEventListener("error", function () {
      setConnected(false);
    });
    source.addEventListener("hello", function () {
      setConnected(true);
      // No replay across reconnects; resynchronise through the request API.
      Promise.all([loadHealth(), loadSystems(), loadJobs()])
        .then(function () {
          showError("");
          var requested = new URLSearchParams(window.location.search).get("job");
          if (!selected && requested && jobs.has(requested)) selectJob(requested);
        })
        .catch(function (err) {
          showError(err.message);
        });
    });
    source.addEventListener("job", function (event) {
      upsertJob(JSON.parse(event.data));
    });
    source.addEventListener("job.log", function (event) {
      var batch = JSON.parse(event.data);
      if (!selected || batch.job !== selected.id) return;
      if (batch.first_seq > selected.lastSeq + 1) fetchLog();
      else appendLines(batch.first_seq, batch.lines);
    });
    source.addEventListener("daemon", function () {
      loadHealth();
    });
    source.addEventListener("systems.changed", function () {
      loadSystems();
    });
  }

  if (window.SD) window.SD.theme.init();
  el("system").addEventListener("change", fillSystemFields);
  el("target").addEventListener("change", fillSystemFields);
  el("mode").addEventListener("change", fillUnits);
  el("unit").addEventListener("change", updateStartButton);
  el("build-form").addEventListener("submit", startBuild);
  connect();
})();
