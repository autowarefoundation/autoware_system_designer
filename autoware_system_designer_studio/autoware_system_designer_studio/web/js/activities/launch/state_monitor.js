// Launch: launched members as a namespace tree with run states beside the
// selected member's log, or the launcher's own output. Member states arrive on
// the daemon's event stream, keyed by member path. One member's log is
// followed at a time: `launch.follow` brings its current tail, `member.log`
// events the lines after, and the follow is released when the selection
// leaves it or the monitor hides; a composable node follows its container's
// log. Once the launcher ended, the followed log stays with a note where it
// ended, and a follow brings the tail the daemon reads from the runtime's
// files. Failed members are listed with their errors above the tree. A member
// whose output grows fast carries a busy-log mark, from the daemon's
// `member.activity` rates; a composable node's output is its container's. The
// selected node or module is the store's `entity`, shared with Explore; a
// selected module shows the severest state among its members. A selector
// beside the title shows a past run instead: the daemon lists the runtime's
// log directories and tails their files; the tree keeps the mode's structure,
// members the run holds no log for are dimmed, its members outside the mode
// are listed below the tree, and no run state is shown.
(function () {
  const SD = window.SD;
  const { element, terminal, mainView } = SD.widgets;
  const launch = (SD.activities.launch ||= {});
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

  function busyMark() {
    return element("span", "log-busy");
  }

  function stateMonitor(container, config, store) {
    const daemon = SD.daemon;
    const {
      members,
      sessions,
      stateToken,
      stateLabel,
      sessionLabel,
      sessionFailures,
      sessionToken,
      logRate,
      busyLog,
      rateLabel,
      BUSY_LOG_BPS,
      nestComposables,
      worstToken,
      tokenSummary,
      followsContainer,
      runLabel,
    } = launch;
    const treePane = element("div", "pane-side monitor-tree");
    const filter = element("input", "panel-search");
    filter.type = "search";
    filter.placeholder = "Filter by namespace or name";
    filter.setAttribute("aria-label", "Filter members");
    const launcherList = element("div", "monitor-launchers");
    const failureList = element("div", "monitor-failures");
    const tree = element("div", "ns-tree");
    const legend = element("div", "monitor-legend");
    LEGEND.forEach(([token, label, meaning]) => {
      const item = element("span", "monitor-legend-item");
      item.title = meaning;
      item.append(stateMark(token), element("span", "", label));
      legend.appendChild(item);
    });
    const busyLegend = element("span", "monitor-legend-item");
    busyLegend.title = `output growing by ${rateLabel(BUSY_LOG_BPS)} or more`;
    busyLegend.append(busyMark(), element("span", "", "busy log"));
    legend.appendChild(busyLegend);
    const runNote = element(
      "p",
      "monitor-run-note",
      "Logs of a past run; run states are not recorded.",
    );
    runNote.hidden = true;
    const extraList = element("div", "monitor-run-extras");
    extraList.hidden = true;
    treePane.append(
      filter,
      launcherList,
      failureList,
      runNote,
      tree,
      extraList,
      legend,
    );
    // Live sessions, or a past run whose log directory the daemon reads.
    const runSelect = element("select", "monitor-run-select");
    runSelect.setAttribute("aria-label", "Run");
    runSelect.addEventListener("change", () => setRun(runSelect.value));

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
    const rateText = element("span", "member-rate");
    view.trailing.append(note, rateText, badge, actions);
    container.append(treePane, view.element);

    // The followed member's terminal, refilled per follow; job id -> the launcher's terminal.
    const memberLog = terminal("No output yet.");
    const launcherLogs = new Map(); // job id -> { log, lastSeq }
    // The followed log: { path, unit, job, live, ended } while a session serves
    // it (live: through its channel; ended: the end note is shown), else { path }.
    let followed = null;
    let followSeq = 0;
    const expanded = new Set();
    let root = null;
    // Entity key -> { entity, node }: the tree entry of every node and module.
    let byKey = new Map();
    // Member path -> the same entry, for the node members of the tree.
    let byMember = new Map();
    // The selected entity, a node (member_path, container_path) or a module, or null.
    let selected = null;
    let selectedJob = "";
    // The tree renders only while shown; a change made while hidden renders on the next show.
    let hidden = false;
    let stale = false;
    // Tree path -> tokens of the members within, itself first; filled per render.
    let memberTokens = new Map();
    // Tree path -> the fastest output growth among the members within; filled per render.
    let logRates = new Map();
    // The past run shown instead of the live sessions, or null: its summary and
    // members from the daemon, member path -> directory name for the members of
    // the tree, and {slug, label} of its members outside the tree.
    let run = null;
    let runSeq = 0;
    let selectedExtra = null;

    function memberOf(node) {
      return node.entities.find((e) => e.entity_type === "node");
    }

    function moduleOf(node) {
      return node.entities.find((e) => e.entity_type === "module");
    }

    function recordOf(entity) {
      return members.get(entity.member_path)?.record;
    }

    // Token and label of one member; a composable follows its container once
    // that stopped, unless it failed itself.
    function memberState(entity) {
      const own = recordOf(entity);
      const host =
        entity.container_path && members.get(entity.container_path)?.record;
      if (own?.state === "failed" || !followsContainer(host))
        return { token: stateToken(own), label: stateLabel(own) };
      return {
        token: stateToken(host),
        label: `${stateLabel(own)} · container ${stateLabel(host)}`,
      };
    }

    // The member whose files hold `entity`'s output.
    function logHolder(entity) {
      return entity.container_path || entity.member_path;
    }

    function rateWithin(node) {
      if (logRates.has(node.path)) return logRates.get(node.path);
      const own = memberOf(node);
      let rate = own && !own.container_path ? logRate(own.member_path) : 0;
      node.children.forEach((child) => {
        rate = Math.max(rate, rateWithin(child));
      });
      logRates.set(node.path, rate);
      return rate;
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
      const own = memberOf(node);
      if (run) {
        if (!own) return;
        if (own.container_path) row.classList.add("ns-composable");
        if (!run.slugs.has(logHolder(own))) row.classList.add("ns-absent");
        return;
      }
      const tokens = tokensWithin(node);
      if (!tokens.length) return;
      const mark = stateMark(worstToken(tokens));
      const summary = tokenSummary(own ? tokens.slice(1) : tokens) || "idle";
      mark.title = own ? memberState(own).label : summary;
      if (own && node.children.size) mark.title += ` · members: ${summary}`;
      row.insertBefore(mark, row.querySelector(".ns-label"));
      if (own?.container_path) row.classList.add("ns-composable");
      const rate = rateWithin(node);
      if (!busyLog(rate)) return;
      const busy = busyMark();
      busy.title =
        own && !own.container_path
          ? `log ${rateLabel(rate)}`
          : `busiest log within ${rateLabel(rate)}`;
      row.appendChild(busy);
    }

    // The session serving a log path: through its channel while connected,
    // else from its runtime's files once it has a log directory.
    function servingSession(path) {
      const unit = members.get(path)?.unit;
      const session = unit && sessions.get(unit);
      return session && (session.connected || session.log_dir) ? session : null;
    }

    function endNote(session) {
      return `launcher ${sessionLabel(session)}`;
    }

    // Follows `path` on its session; the previous follow is released first. The
    // same path on the same session is left as it is, unless the channel came
    // back since: a follow outlives the channel but has to be remade on it.
    function follow(path) {
      const session = servingSession(path);
      const previous = followed;
      if (
        previous?.path === path &&
        (previous.job || null) === (session?.job || null) &&
        (previous.live || !session?.connected)
      )
        return;
      const seq = ++followSeq;
      const ended = !!session && session.state === "exited";
      followed = session
        ? {
            path,
            unit: session.compute_unit,
            job: session.job,
            live: session.connected,
            ended,
          }
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
          if (followed.ended)
            memberLog.note(endNote(sessions.get(followed.unit)));
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

    // Re-follows after the serving session changed: launched again, or its
    // channel came back; a log whose launcher ended keeps its lines and gets
    // the end note once.
    function refollow() {
      if (!followed || hidden || run) return;
      const session = servingSession(followed.path);
      if (followed.job && !session?.connected) followed.live = false;
      if (
        (session?.job || null) !== (followed.job || null) ||
        (session?.connected && !followed.live)
      ) {
        follow(followed.path);
        return;
      }
      if (session?.state === "exited" && followed.job && !followed.ended) {
        followed.ended = true;
        memberLog.note(endNote(session));
      }
    }

    function renderTree() {
      if (!root) return;
      if (hidden) {
        stale = true;
        return;
      }
      memberTokens = new Map();
      logRates = new Map();
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
      launcherList.hidden = !!run;
      if (run) {
        renderFailures();
        return;
      }
      sessions.forEach((session) => {
        if (session.system !== config.systemId || !session.job) return;
        const row = element("button", "monitor-launcher");
        row.type = "button";
        row.classList.toggle("selected", session.job === selectedJob);
        row.append(
          stateMark(sessionToken(session)),
          element("span", "monitor-launcher-unit", session.compute_unit),
          element("span", "monitor-launcher-state", sessionLabel(session)),
        );
        row.addEventListener("click", () => showLauncher(session));
        launcherList.appendChild(row);
      });
      renderFailures();
    }

    // Failed members of the system's sessions with their errors, in the order
    // they failed; a row selects the member.
    function renderFailures() {
      failureList.replaceChildren();
      if (run) {
        failureList.hidden = true;
        return;
      }
      sessions.forEach((session) => {
        if (session.system !== config.systemId) return;
        sessionFailures(session).forEach((record) => {
          const entry = byMember.get(record.path);
          const row = element("button", "monitor-failure");
          row.type = "button";
          row.disabled = !entry;
          row.classList.toggle(
            "selected",
            selected?.member_path === record.path,
          );
          row.append(
            stateMark("failed"),
            element("span", "monitor-failure-path", record.path),
            element(
              "span",
              "monitor-failure-error",
              record.detail?.error || "failed",
            ),
          );
          row.addEventListener("click", () => selectKey(entry.entity.key));
          failureList.appendChild(row);
        });
      });
      failureList.hidden = !failureList.childElementCount;
    }

    function showLog(entity) {
      selected = entity;
      selectedJob = "";
      selectedExtra = null;
      view.body.replaceChildren(memberLog.element);
      if (run) showRunLog(run.slugs.get(logHolder(entity)));
      else follow(logHolder(entity));
      view.setEmpty("");
      const title = entity.container_path
        ? `${entity.member_path} · in ${entity.container_path}`
        : entity.member_path;
      view.setTitle(run ? `${title} · ${runLabel(run.summary)}` : title);
      showState();
      renderLaunchers();
      renderExtras();
      store.set("entity", entity.key);
    }

    function showModule(entity) {
      selected = entity;
      selectedJob = "";
      selectedExtra = null;
      unfollow();
      view.body.replaceChildren();
      view.setEmpty(EMPTY_LOG);
      view.setTitle(entity.path);
      showState();
      renderLaunchers();
      renderExtras();
      store.set("entity", entity.key);
    }

    function showNothing() {
      view.body.replaceChildren();
      view.setEmpty(EMPTY_LOG);
      view.setTitle("");
    }

    // -- past runs

    // Fills the member log from a member directory of the shown run.
    function showRunLog(slug) {
      unfollow();
      const seq = ++runSeq;
      if (!slug) {
        memberLog.clear("No log in this run.");
        return;
      }
      memberLog.clear("Loading…");
      daemon
        .rpc("launch.run_tail", { run: run.name, member: slug })
        .then((result) => {
          if (seq !== runSeq) return;
          memberLog.clear("No output.");
          memberLog.append([...(result.out || []), ...(result.err || [])]);
        })
        .catch((error) => {
          if (seq === runSeq) memberLog.clear(error.message);
        });
    }

    // Member directories of the shown run outside the tree; a row shows its log.
    function renderExtras() {
      extraList.replaceChildren();
      extraList.hidden = !run || !run.extras.length;
      if (extraList.hidden) return;
      extraList.appendChild(
        element("div", "monitor-run-extras-title", "Members outside this mode"),
      );
      run.extras.forEach(({ slug, label }) => {
        const row = element("button", "monitor-run-extra", label);
        row.type = "button";
        row.classList.toggle("selected", slug === selectedExtra);
        row.addEventListener("click", () => showExtra(slug, label));
        extraList.appendChild(row);
      });
    }

    function showExtra(slug, label) {
      selected = null;
      selectedJob = "";
      selectedExtra = slug;
      view.body.replaceChildren(memberLog.element);
      view.setEmpty("");
      view.setTitle(`${label} · ${runLabel(run.summary)}`);
      showRunLog(slug);
      showState();
      renderTree();
      renderExtras();
    }

    // Places the run's members on the tree by path; the rest are listed outside it.
    function placeRun() {
      run.slugs = new Map();
      run.extras = [];
      run.members.forEach(({ slug, path }) => {
        if (byMember.has(path)) run.slugs.set(path, slug);
        else run.extras.push({ slug, label: path });
      });
    }

    // Renders the selection against the shown run or the live sessions.
    function reselect() {
      runNote.hidden = !run;
      legend.hidden = !!run;
      renderTree();
      renderLaunchers();
      if (selected?.entity_type === "node") showLog(selected);
      else if (selected) showModule(selected);
      else {
        showNothing();
        renderExtras();
      }
      showState();
    }

    // Shows the live sessions (an empty name) or a past run.
    function setRun(name) {
      runSelect.value = name;
      if (!name) {
        run = null;
        runSeq += 1;
        selectedExtra = null;
        reselect();
        return;
      }
      daemon
        .rpc("launch.run", { run: name })
        .then((result) => {
          if (runSelect.value !== name) return;
          unfollow();
          run = {
            name,
            summary: result,
            members: result.members || [],
            slugs: new Map(),
            extras: [],
          };
          placeRun();
          reselect();
        })
        .catch((error) => {
          console.warn("launch run:", error);
          setRun("");
        });
    }

    // Past runs of the system, newest first; the shown run stays selected while listed.
    function loadRuns() {
      runSelect.hidden = !daemon.offers("launch.runs");
      if (runSelect.hidden) return;
      daemon
        .rpc("launch.runs", { system: config.systemId })
        .then(({ runs }) => {
          const current = run?.name || "";
          runSelect.replaceChildren(
            ...[["", "Live"], ...runs.map((r) => [r.run, runLabel(r)])].map(
              ([value, label]) => {
                const option = element("option", "", label);
                option.value = value;
                return option;
              },
            ),
          );
          if (current && !runs.some((r) => r.run === current)) setRun("");
          else runSelect.value = current;
        })
        .catch((error) => console.warn("launch runs:", error));
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
      const rate =
        selected?.entity_type === "node" ? logRate(logHolder(selected)) : 0;
      rateText.textContent = rate > 0 ? `log ${rateLabel(rate)}` : "";
      rateText.classList.toggle("busy", busyLog(rate));
      badge.hidden = !!run;
      rateText.hidden = !!run;
      const canAct =
        !run &&
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
      byMember = new Map();
      const index = (node) => {
        node.entities.forEach((entity) => {
          const entry = { entity, node };
          if (entity.key) byKey.set(entity.key, entry);
          if (entity.member_path) byMember.set(entity.member_path, entry);
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
      if (run) placeRun();
      renderTree();
      renderFailures();
      renderExtras();
      selectKey(store.get("entity"));
    }

    SD.on("launch.member", () => {
      if (run) return;
      renderTree();
      showState();
      renderFailures();
    });
    SD.on("launch.sessions", () => {
      loadRuns();
      if (run) return;
      renderTree();
      showState();
      renderLaunchers();
      refollow();
    });
    SD.on("launch.activity", () => {
      if (run) return;
      renderTree();
      showState();
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
    loadRuns();

    return {
      runSelector: runSelect,
      show() {
        hidden = false;
        loadRuns();
        if (!run && selected?.entity_type === "node")
          follow(logHolder(selected));
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
