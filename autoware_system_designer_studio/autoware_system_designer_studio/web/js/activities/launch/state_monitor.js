// Launch: launched members as a namespace tree with run states beside the
// selected member's log, or the launcher's own output. Member state and log
// lines arrive on the daemon's event stream, keyed by member path; a
// composable node reads its container's log.
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
  // Legend entries: token, label, meaning.
  const LEGEND = [
    ["running", "running", "process running or node loaded"],
    ["ok", "exited", "exited with code 0"],
    ["warning", "warning", "loading, respawning, or exited with another code"],
    ["failed", "failed", "failed to start or load"],
    ["idle", "idle", "pending, done, or not launched"],
  ];

  function stateMark(token) {
    return element("span", `state-dot ${token}`);
  }

  function stateMonitor(container, config, store) {
    const daemon = SD.daemon;
    const {
      members,
      sessions,
      stateToken,
      stateLabel,
      sessionLabel,
      nestComposables,
      worstToken,
      tokenSummary,
      followsContainer,
    } = launch;
    const treePane = element("div", "pane-side monitor-tree");
    const filter = element("input", "panel-search");
    filter.type = "search";
    filter.placeholder = "Filter by namespace or name";
    filter.setAttribute("aria-label", "Filter members");
    const launcherList = element("div", "monitor-launchers");
    const tree = element("div", "ns-tree");
    const legend = element("div", "monitor-legend");
    LEGEND.forEach(([token, label, meaning]) => {
      const item = element("span", "monitor-legend-item");
      item.title = meaning;
      item.append(stateMark(token), element("span", "", label));
      legend.appendChild(item);
    });
    treePane.append(filter, launcherList, tree, legend);

    const view = mainView({ empty: "Select a node to follow its log." });
    view.head.classList.add("member-head");
    const badge = element("span", "member-state idle");
    const badgeMark = stateMark("idle");
    const badgeText = element("span");
    badge.append(badgeMark, badgeText);
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

    // Log path -> its terminal, kept while the page lives; job id -> the launcher's terminal.
    const logs = new Map();
    const launcherLogs = new Map(); // job id -> { log, lastSeq }
    const expanded = new Set();
    let root = null;
    // The selected member entity (member_path, container_path) or null.
    let selected = null;
    let selectedJob = "";
    // The tree renders only while shown; a change made while hidden renders on the next show.
    let hidden = false;
    let stale = false;
    // Tree path -> tokens of the members within, itself first; filled per render.
    let memberTokens = new Map();

    function memberOf(node) {
      return node.entities.find((e) => e.entity_type === "node");
    }

    function recordOf(entity) {
      return members.get(entity.member_path)?.record;
    }

    // Token and label of one member; a composable follows its container once that stopped.
    function memberState(entity) {
      const own = recordOf(entity);
      const host =
        entity.container_path && members.get(entity.container_path)?.record;
      if (!followsContainer(host))
        return { token: stateToken(own), label: stateLabel(own) };
      return {
        token: stateToken(host),
        label: `${stateLabel(own)} · container ${stateLabel(host)}`,
      };
    }

    function tokensWithin(node) {
      if (memberTokens.has(node.path)) return memberTokens.get(node.path);
      const own = memberOf(node);
      const list = own ? [memberState(own).token] : [];
      node.children.forEach((child) => list.push(...tokensWithin(child)));
      memberTokens.set(node.path, list);
      return list;
    }

    function decorate(node, row) {
      const tokens = tokensWithin(node);
      if (!tokens.length) return;
      const own = memberOf(node);
      const mark = stateMark(worstToken(tokens));
      const summary = tokenSummary(own ? tokens.slice(1) : tokens) || "idle";
      mark.title = own ? memberState(own).label : summary;
      if (own && node.children.size) mark.title += ` · members: ${summary}`;
      row.insertBefore(mark, row.querySelector(".ns-label"));
      if (own?.container_path) row.classList.add("ns-composable");
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
      memberTokens = new Map();
      SD.NamespaceTree.render(tree, root, {
        query: filter.value,
        expanded,
        selectedPath: selected?.path,
        decorate,
        selectable: (node) => !!memberOf(node),
        onToggle: renderTree,
        onSelect(node) {
          const own = memberOf(node);
          if (own) showLog(own);
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
          stateMark(LAUNCHER_TOKENS[session.state] || "idle"),
          element("span", "monitor-launcher-unit", session.compute_unit),
          element("span", "monitor-launcher-state", sessionLabel(session)),
        );
        row.addEventListener("click", () => showLauncher(session));
        launcherList.appendChild(row);
      });
    }

    function showLog(entity) {
      selected = entity;
      selectedJob = "";
      view.body.replaceChildren(
        logOf(entity.container_path || entity.member_path).element,
      );
      view.setEmpty("");
      view.setTitle(
        entity.container_path
          ? `${entity.member_path} · in ${entity.container_path}`
          : entity.member_path,
      );
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
      selected = null;
      selectedJob = session.job;
      view.body.replaceChildren(launcherLog(session.job).log.element);
      view.setEmpty("");
      view.setTitle(`launcher · ${session.compute_unit} · ${session.job}`);
      fetchLauncher(session.job);
      showState();
      renderTree();
      renderLaunchers();
    }

    // Actions address a process: a composable node is acted on through its container.
    function showState() {
      const member = selected && members.get(selected.member_path);
      const session = member && sessions.get(member.unit);
      const { token, label } = selected
        ? memberState(selected)
        : { token: "idle", label: "" };
      badgeText.textContent = label;
      badge.className = `member-state ${token}`;
      badgeMark.className = `state-dot ${token}`;
      const canAct =
        !!member &&
        !selected.container_path &&
        !!session &&
        session.state !== "exited" &&
        session.connected;
      actions.hidden = !canAct;
      actions
        .querySelectorAll("button")
        .forEach((button) => (button.disabled = !canAct));
    }

    function act(action) {
      const member = selected && members.get(selected.member_path);
      if (!member) return;
      note.textContent = "";
      daemon
        .rpc(`launch.${action}`, {
          compute_unit: member.unit,
          pattern: selected.member_path,
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
      root = SD.NamespaceTree.build(
        nestComposables(SD.NamespaceTree.entities(data)),
      );
      // Top-level entries start folded; everything beneath them starts expanded.
      expanded.clear();
      const expand = (node) =>
        node.children.forEach((child) => {
          expanded.add(child.path);
          expand(child);
        });
      root.children.forEach((child) => expand(child));
      renderTree();
    }

    SD.on("launch.member", ({ path }) => {
      if (
        selected &&
        (path === selected.member_path || path === selected.container_path)
      )
        showState();
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
