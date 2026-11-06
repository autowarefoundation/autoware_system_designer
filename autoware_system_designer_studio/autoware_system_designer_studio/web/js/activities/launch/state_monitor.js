// Launch: launched members as a namespace tree with run states beside the
// selected member's log, or the launcher's own output. Member state and log
// lines arrive on the daemon's event stream, keyed by member path.
(function () {
  const SD = window.SD;
  const { element, terminal, mainView } = SD.widgets;
  const launch = (SD.activities.launch ||= {});
  // Session state -> run-state token of its launcher entry.
  const LAUNCHER_TOKENS = {
    running: "running",
    starting: "warning",
    stopping: "warning",
  };
  const ACTIONS = [
    ["stop", "Stop"],
    ["restart", "Restart"],
    ["kill", "Kill"],
  ];

  function stateMonitor(container, config, store) {
    const daemon = SD.daemon;
    const { members, sessions, stateToken, stateLabel, sessionLabel } = launch;
    const treePane = element("div", "pane-side monitor-tree");
    const filter = element("input", "panel-search");
    filter.type = "search";
    filter.placeholder = "Filter by namespace or name";
    filter.setAttribute("aria-label", "Filter members");
    const launcherList = element("div", "monitor-launchers");
    const tree = element("div", "ns-tree");
    treePane.append(filter, launcherList, tree);

    const view = mainView({ empty: "Select a node to follow its log." });
    view.head.classList.add("member-head");
    const badge = element("span", "member-state idle");
    const actions = element("span", "member-actions");
    ACTIONS.forEach(([action, label]) => {
      const button = element("button", "panel-button", label);
      button.type = "button";
      button.dataset.action = action;
      button.addEventListener("click", () => act(action));
      actions.appendChild(button);
    });
    const note = element("span", "member-note");
    view.trailing.append(note, badge, actions);
    container.append(treePane, view.element);

    // Member path -> its terminal, kept while the page lives; job id -> the launcher's terminal.
    const logs = new Map();
    const launcherLogs = new Map(); // job id -> { log, lastSeq }
    const expanded = new Set();
    let root = null;
    let selectedPath = "";
    let selectedJob = "";
    // The tree renders only while shown; a change made while hidden renders on the next show.
    let hidden = false;
    let stale = false;

    function decorate(node, row) {
      if (!node.entities.some((e) => e.entity_type === "node")) return;
      const token = stateToken(members.get(node.path)?.record);
      const dot = element("span", `state-dot ${token}`);
      dot.title = stateLabel(members.get(node.path)?.record);
      row.insertBefore(dot, row.querySelector(".ns-label"));
    }

    function logOf(path) {
      if (!logs.has(path)) logs.set(path, terminal("No output yet."));
      return logs.get(path);
    }

    function renderTree() {
      if (!root) return;
      if (hidden) {
        stale = true;
        return;
      }
      SD.NamespaceTree.render(tree, root, {
        query: filter.value,
        expanded,
        selectedPath,
        decorate,
        onSelect(node) {
          if (node.entities.some((e) => e.entity_type === "node"))
            showLog(node.path);
          renderTree();
        },
      });
    }

    // One entry per session: the launcher process's own output.
    function renderLaunchers() {
      launcherList.replaceChildren();
      sessions.forEach((session) => {
        if (session.system !== config.systemId || !session.job) return;
        const row = element("button", "monitor-launcher");
        row.type = "button";
        row.classList.toggle("selected", session.job === selectedJob);
        row.append(
          element(
            "span",
            `state-dot ${LAUNCHER_TOKENS[session.state] || "idle"}`,
          ),
          element("span", "monitor-launcher-unit", session.compute_unit),
          element("span", "monitor-launcher-state", sessionLabel(session)),
        );
        row.addEventListener("click", () => showLauncher(session));
        launcherList.appendChild(row);
      });
    }

    function showLog(path) {
      selectedPath = path;
      selectedJob = "";
      view.body.replaceChildren(logOf(path).element);
      view.setEmpty("");
      view.setTitle(path);
      showState();
      renderLaunchers();
    }

    function launcherLog(jobId) {
      if (!launcherLogs.has(jobId))
        launcherLogs.set(jobId, {
          log: terminal("No output yet."),
          lastSeq: 0,
        });
      return launcherLogs.get(jobId);
    }

    function appendLauncher(jobId, firstSeq, lines) {
      const entry = launcherLog(jobId);
      const skip = Math.max(entry.lastSeq + 1 - firstSeq, 0);
      if (skip >= lines.length) return;
      entry.log.append(lines.slice(skip));
      entry.lastSeq = firstSeq + lines.length - 1;
    }

    function fetchLauncher(jobId) {
      const entry = launcherLog(jobId);
      daemon
        .rpc("jobs.get", { job: jobId, since: entry.lastSeq })
        .then((result) =>
          appendLauncher(jobId, result.log.first_seq, result.log.lines),
        )
        .catch((error) => console.warn("launcher log:", error));
    }

    function showLauncher(session) {
      selectedPath = "";
      selectedJob = session.job;
      view.body.replaceChildren(launcherLog(session.job).log.element);
      view.setEmpty("");
      view.setTitle(`launcher · ${session.compute_unit} · ${session.job}`);
      fetchLauncher(session.job);
      showState();
      renderTree();
      renderLaunchers();
    }

    function showState() {
      const member = members.get(selectedPath);
      const session = member && sessions.get(member.unit);
      const token = selectedPath ? stateToken(member?.record) : "idle";
      badge.textContent = selectedPath ? stateLabel(member?.record) : "";
      badge.className = `member-state ${token}`;
      const canAct =
        !!member &&
        !!session &&
        session.state !== "exited" &&
        session.connected;
      actions.hidden = !canAct;
      actions
        .querySelectorAll("button")
        .forEach((button) => (button.disabled = !canAct));
    }

    function act(action) {
      const member = members.get(selectedPath);
      if (!member) return;
      note.textContent = "";
      daemon
        .rpc(`launch.${action}`, {
          compute_unit: member.unit,
          pattern: selectedPath,
        })
        .catch((error) => {
          note.textContent = error.message;
        });
    }

    async function loadMode() {
      const data = await SD.NamespaceTree.loadStructure(
        config.dataBase,
        store.get("mode"),
      );
      root = SD.NamespaceTree.build(SD.NamespaceTree.entities(data));
      // Every namespace starts expanded.
      expanded.clear();
      const expand = (node) =>
        node.children.forEach((child) => {
          expanded.add(child.path);
          expand(child);
        });
      expand(root);
      renderTree();
    }

    SD.on("launch.member", ({ path }) => {
      if (path === selectedPath) showState();
      renderTree();
    });
    SD.on("launch.sessions", () => {
      showState();
      renderTree();
      renderLaunchers();
    });
    SD.on("daemon.member.log", ({ path, lines }) => logOf(path).append(lines));
    SD.on("daemon.job.log", (batch) => {
      if (!launcherLogs.has(batch.job)) return;
      if (batch.first_seq > launcherLog(batch.job).lastSeq + 1)
        fetchLauncher(batch.job);
      else appendLauncher(batch.job, batch.first_seq, batch.lines);
    });
    filter.addEventListener("input", renderTree);
    const reload = () =>
      loadMode().catch((error) => console.warn("launch monitor:", error));
    store.onChange("mode", reload);
    reload();
    showState();
    renderLaunchers();

    return {
      show() {
        hidden = false;
        if (!stale) return;
        stale = false;
        renderTree();
      },
      hide() {
        hidden = true;
      },
    };
  }

  launch.stateMonitor = stateMonitor;
})();
