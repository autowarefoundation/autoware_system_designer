// Launch: launched members as a namespace tree with run states beside the
// selected member's log, or the launcher's own output. Member states arrive on
// the daemon's event stream, keyed by member path; the view is read only, the
// Control view acts on members. One member's log is
// followed at a time: `launch.follow` brings its current tail, `member.log`
// events the lines after, and the follow is released when the selection
// leaves it or the monitor hides, and a selection made while hidden is
// followed on the next show; a composable node follows its container's
// log. Once the launcher ended, the followed log stays with a note where it
// ended, and a follow brings the tail the daemon reads from the runtime's
// files. Failed members are listed with their errors above the tree. A member
// whose output grows fast carries a busy-log mark, from the daemon's
// `member.activity` rates; a composable node's output is its container's. The
// selected node or module is the store's `entity`, shared with Explore; a
// selected module shows the severest state among its members. A selector
// beside the title shows a past launch instead, the runs of its compute units
// as one tree: the daemon lists the runtime's log directories and tails their
// files; the tree keeps the mode's structure, members no run holds a log for
// are dimmed, members outside the mode are listed below the tree, and no run
// state is shown.
(function () {
  const SD = window.SD;
  const { element, terminal, mainView } = SD.widgets;
  const launch = (SD.activities.launch ||= {});
  const stateMark = (token) => SD.RunState.mark(token);

  function busyMark() {
    return element("span", "log-busy");
  }

  function stateMonitor(container, config, store) {
    const daemon = SD.daemon;
    // Live records of the shown mode; a session of another mode or system is not launched here.
    const { members, sessions, logRate, restartsOf } = launch.scope(
      config.systemId,
      () => store.get("mode"),
    );
    const {
      sessionLabel,
      sessionFailures,
      sessionToken,
      busyLog,
      rateLabel,
      BUSY_LOG_BPS,
      worstToken,
      tokenSummary,
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
    const legend = SD.RunState.legend("monitor-legend");
    const busyLegend = element("span", "state-legend-item");
    busyLegend.title = `output growing by ${rateLabel(BUSY_LOG_BPS)} or more`;
    busyLegend.append(busyMark(), element("span", "", "busy log"));
    legend.appendChild(busyLegend);
    const runNote = element(
      "p",
      "monitor-run-note",
      "Logs of a past launch; run states are not recorded.",
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
    const rateText = element("span", "member-rate");
    const restartText = element("span", "member-restarts");
    view.trailing.append(rateText, restartText, badge);
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
    // The past launch shown instead of the live sessions, or null: its label,
    // its runs oldest first with their members from the daemon, member path ->
    // the member's logs [{ run, slug, label }] for the members of the tree, and
    // { path, logs } of its members outside the tree.
    let run = null;
    // Past runs by launch, as the selector lists them.
    let runGroups = [];
    let runSeq = 0;
    let selectedExtra = null;

    function memberOf(node) {
      return node.entities.find((e) => e.entity_type === "node");
    }

    function moduleOf(node) {
      return node.entities.find((e) => e.entity_type === "module");
    }

    function memberState(entity) {
      return launch.memberState(entity, members);
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
            system: session.system,
            mode: session.mode,
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
        .rpc("launch.follow", {
          compute_unit: session.compute_unit,
          path,
          system: session.system,
          mode: session.mode,
        })
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
        .rpc("launch.unfollow", {
          compute_unit: entry.unit,
          path: entry.path,
          system: entry.system,
          mode: entry.mode,
        })
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
        if (!session.job) return;
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
      else if (!hidden) follow(logHolder(entity));
      view.setEmpty("");
      const title = entity.container_path
        ? `${entity.member_path} · in ${entity.container_path}`
        : entity.member_path;
      view.setTitle(run ? `${title} · ${run.label}` : title);
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

    // Drops the selected entity and its follow; a shown launcher log stays.
    function clearSelection() {
      selected = null;
      unfollow();
      showNothing();
      showState();
      renderTree();
      renderLaunchers();
    }

    function showNothing() {
      view.body.replaceChildren();
      view.setEmpty(EMPTY_LOG);
      view.setTitle("");
    }

    // -- past runs

    // Fills the member log from the member's directories in the shown launch,
    // one run after another under the run's label where there are several.
    function showRunLog(logs) {
      unfollow();
      const seq = ++runSeq;
      if (!logs?.length) {
        memberLog.clear("No log in this run.");
        return;
      }
      memberLog.clear("Loading…");
      Promise.all(
        logs.map(({ run: name, slug }) =>
          daemon.rpc("launch.run_tail", { run: name, member: slug }),
        ),
      )
        .then((results) => {
          if (seq !== runSeq) return;
          memberLog.clear("No output.");
          results.forEach((result, index) => {
            if (logs.length > 1) memberLog.note(logs[index].label);
            memberLog.append([...(result.out || []), ...(result.err || [])]);
          });
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
      run.extras.forEach((extra) => {
        const row = element("button", "monitor-run-extra", extra.path);
        row.type = "button";
        row.classList.toggle("selected", extra.path === selectedExtra);
        row.addEventListener("click", () => showExtra(extra));
        extraList.appendChild(row);
      });
    }

    function showExtra({ path, logs }) {
      selected = null;
      selectedJob = "";
      selectedExtra = path;
      view.body.replaceChildren(memberLog.element);
      view.setEmpty("");
      view.setTitle(`${path} · ${run.label}`);
      showRunLog(logs);
      showState();
      renderTree();
      renderExtras();
    }

    // Places the members of the launch's runs on the tree by path; the rest are
    // listed outside it.
    function placeRun() {
      run.slugs = new Map();
      const extras = new Map();
      run.runs.forEach(({ name, label, members }) => {
        members.forEach(({ slug, path }) => {
          const logs = byMember.has(path) ? run.slugs : extras;
          if (!logs.has(path)) logs.set(path, []);
          logs.get(path).push({ run: name, slug, label });
        });
      });
      run.extras = [...extras].map(([path, logs]) => ({ path, logs }));
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

    // Shows the live sessions (an empty value) or a past launch: the runs of
    // its units, a unit started again within it holding several.
    function setRun(value) {
      runSelect.value = value;
      const group = runGroups.find((g) => g.value === value);
      if (!group) {
        runSelect.value = "";
        run = null;
        runSeq += 1;
        selectedExtra = null;
        reselect();
        return;
      }
      const runs = [...group.runs].sort(
        (a, b) => (a.started || 0) - (b.started || 0),
      );
      Promise.allSettled(
        runs.map((r) => daemon.rpc("launch.run", { run: r.run })),
      ).then((results) => {
        if (runSelect.value !== value) return;
        const read = results
          .map((result, index) => ({ result, summary: runs[index] }))
          .filter(({ result }) => result.status === "fulfilled");
        results
          .filter((result) => result.status === "rejected")
          .forEach((result) => console.warn("launch run:", result.reason));
        if (!read.length) {
          setRun("");
          return;
        }
        unfollow();
        run = {
          name: value,
          label: group.label,
          runs: read.map(({ result, summary }) => ({
            name: summary.run,
            label: runLabel(summary),
            members: result.value.members || [],
          })),
          slugs: new Map(),
          extras: [],
        };
        placeRun();
        reselect();
      });
    }

    // Past runs of the system by launch, newest first; the shown launch stays
    // selected while listed.
    function loadRuns() {
      runSelect.hidden = !daemon.offers("launch.runs");
      if (runSelect.hidden) return;
      daemon
        .rpc("launch.runs", { system: config.systemId })
        .then(({ runs }) => {
          const current = run?.name || "";
          runGroups = launch.runGroups(runs);
          runSelect.replaceChildren(...launch.runOptions(runGroups, true));
          if (current && !runGroups.some((g) => g.value === current))
            setRun("");
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

    function showState() {
      const { token, label } = selectedState();
      badgeText.textContent = label;
      badge.className = `member-state ${token}`;
      badgeMark.className = `state-dot ${token}`;
      const rate =
        selected?.entity_type === "node" ? logRate(logHolder(selected)) : 0;
      rateText.textContent = rate > 0 ? `log ${rateLabel(rate)}` : "";
      rateText.classList.toggle("busy", busyLog(rate));
      const restarts =
        selected?.entity_type === "node" && restartsOf(selected.member_path);
      restartText.textContent = restarts?.restarts
        ? `restarted ×${restarts.restarts}`
        : "";
      restartText.title = restarts ? `${restarts.respawns} respawned` : "";
      badge.hidden = !!run;
      rateText.hidden = !!run;
      restartText.hidden = !!run;
    }

    async function loadMode() {
      root = SD.NamespaceTree.build(
        await launch.modeEntities(config.dataBase, store.get("mode")),
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
      renderLaunchers();
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
      if (!key && selected) clearSelection();
      else if (key !== selected?.key) selectKey(key);
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
