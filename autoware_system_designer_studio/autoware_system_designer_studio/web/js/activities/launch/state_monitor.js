// Launch: launched members as a namespace tree with run states beside the
// selected member's log, or the launcher's own output. Member states arrive on
// the daemon's event stream, keyed by member path. One member's log is
// followed at a time: `launch.follow` brings its current tail, `member.log`
// events the lines after, and the follow is released when the selection
// leaves it or the monitor hides; a composable node follows its container's
// log. The selected node or module is the store's `entity`, shared with
// Explore; a selected module shows the severest state among its members.
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

    const EMPTY_LOG = "Select a node to follow its log.";
    const view = mainView({ empty: EMPTY_LOG });
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

    // The followed member's terminal, refilled per follow; job id -> the launcher's terminal.
    const memberLog = terminal("No output yet.");
    const launcherLogs = new Map(); // job id -> { log, lastSeq }
    // The followed log: { path, unit, job } while a runtime serves it, else { path } only.
    let followed = null;
    let followSeq = 0;
    const expanded = new Set();
    let root = null;
    // Entity key -> { entity, node }: the tree entry of every node and module.
    let byKey = new Map();
    // The selected entity, a node (member_path, container_path) or a module, or null.
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

    function moduleOf(node) {
      return node.entities.find((e) => e.entity_type === "module");
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

    // The session serving a log path, while its runtime is connected.
    function servingSession(path) {
      const unit = members.get(path)?.unit;
      const session = unit && sessions.get(unit);
      return session && session.connected && session.state !== "exited"
        ? session
        : null;
    }

    // Follows `path` on its runtime; the previous follow is released first.
    function follow(path) {
      const session = servingSession(path);
      const previous = followed;
      if (
        previous?.path === path &&
        (previous.job || null) === (session?.job || null)
      )
        return;
      const seq = ++followSeq;
      followed = session
        ? { path, unit: session.compute_unit, job: session.job }
        : { path };
      release(previous);
      if (!session) {
        memberLog.clear("Not launched.");
        return;
      }
      memberLog.clear("Loading…");
      daemon
        .rpc("launch.follow", { compute_unit: session.compute_unit, path })
        .then((result) => {
          if (seq !== followSeq) return;
          memberLog.clear("No output yet.");
          memberLog.append([...(result.out || []), ...(result.err || [])]);
        })
        .catch((error) => {
          if (seq === followSeq) memberLog.clear(error.message);
        });
    }

    function release(entry) {
      if (!entry?.unit) return;
      daemon
        .rpc("launch.unfollow", { compute_unit: entry.unit, path: entry.path })
        .catch(() => {});
    }

    function unfollow() {
      followSeq += 1;
      release(followed);
      followed = null;
    }

    // Re-follows after the serving runtime changed: connected, reconnected, or gone.
    function refollow() {
      if (!followed || hidden) return;
      const session = servingSession(followed.path);
      if ((session?.job || null) !== (followed.job || null)) follow(followed.path);
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
        selectable: (node) => !!(memberOf(node) || moduleOf(node)),
        onToggle: renderTree,
        onSelect(node) {
          const own = memberOf(node);
          const module = moduleOf(node);
          if (own) showLog(own);
          else if (module) showModule(module);
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
      view.body.replaceChildren(memberLog.element);
      follow(entity.container_path || entity.member_path);
      view.setEmpty("");
      view.setTitle(
        entity.container_path
          ? `${entity.member_path} · in ${entity.container_path}`
          : entity.member_path,
      );
      showState();
      renderLaunchers();
      store.set("entity", entity.key);
    }

    function showModule(entity) {
      selected = entity;
      selectedJob = "";
      unfollow();
      view.body.replaceChildren();
      view.setEmpty(EMPTY_LOG);
      view.setTitle(entity.path);
      showState();
      renderLaunchers();
      store.set("entity", entity.key);
    }

    // Selects the entry keyed `key` and unfolds the tree down to it; an unknown
    // key leaves the selection as it is.
    function selectKey(key) {
      const entry = byKey.get(key);
      if (!entry) return;
      const segments = entry.node.path.split("/").filter(Boolean);
      segments.forEach((_segment, index) => {
        expanded.add(`/${segments.slice(0, index + 1).join("/")}`);
      });
      if (entry.entity.entity_type === "node") showLog(entry.entity);
      else showModule(entry.entity);
      renderTree();
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
      unfollow();
      view.body.replaceChildren(launcherLog(session.job).log.element);
      view.setEmpty("");
      view.setTitle(`launcher · ${session.compute_unit} · ${session.job}`);
      fetchLauncher(session.job);
      showState();
      renderTree();
      renderLaunchers();
    }

    // State of the selection: a member's own, or the severest among a module's members.
    function selectedState() {
      if (!selected) return { token: "idle", label: "" };
      if (selected.entity_type === "node") return memberState(selected);
      const tokens = tokensWithin(byKey.get(selected.key).node);
      return {
        token: worstToken(tokens),
        label: tokenSummary(tokens) || "idle",
      };
    }

    // Actions address a process: a composable node is acted on through its container.
    function showState() {
      const member = selected?.member_path && members.get(selected.member_path);
      const session = member && sessions.get(member.unit);
      const { token, label } = selectedState();
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
      const member = selected?.member_path && members.get(selected.member_path);
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
      byKey = new Map();
      const index = (node) => {
        node.entities.forEach((entity) => {
          if (entity.key) byKey.set(entity.key, { entity, node });
        });
        node.children.forEach(index);
      };
      index(root);
      // Top-level entries start folded; everything beneath them starts expanded.
      expanded.clear();
      const expand = (node) =>
        node.children.forEach((child) => {
          expanded.add(child.path);
          expand(child);
        });
      root.children.forEach((child) => expand(child));
      renderTree();
      selectKey(store.get("entity"));
    }

    SD.on("launch.member", () => {
      renderTree();
      showState();
    });
    SD.on("launch.sessions", () => {
      renderTree();
      showState();
      renderLaunchers();
      refollow();
    });
    SD.on("daemon.member.log", ({ compute_unit: unit, path, lines }) => {
      if (followed?.unit === unit && followed.path === path)
        memberLog.append(lines);
    });
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
    store.onChange("entity", (key) => {
      if (key !== selected?.key) selectKey(key);
    });
    reload();
    showState();
    renderLaunchers();

    return {
      show() {
        hidden = false;
        if (selected?.entity_type === "node")
          follow(selected.container_path || selected.member_path);
        if (!stale) return;
        stale = false;
        renderTree();
        showState();
      },
      hide() {
        hidden = true;
        unfollow();
      },
    };
  }

  launch.stateMonitor = stateMonitor;
})();
