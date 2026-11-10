// Launch: live topic rates of a mode. One row per topic, joining the design
// (publishing and subscribing nodes, the rate and the warn and error rates
// its output port declares) with the runtimes' counters of every compute unit
// running the mode (publish and take rates, callback and communication times).
// Rows sit in a tree of the publishing node's namespace or of the topic's
// namespace, sorted by a column within each group, and filter by status. The
// rates flow only while the view is shown: it follows
// the monitor of each connected session and releases it when hidden; the last
// rates of a launch stay shown after it ended, until its unit is launched
// again. A selector beside the title shows a past run instead, from the rates
// record the runtime leaves in the run's log directory, over the whole run or
// a span ending at a chosen sample. A row click selects the topic and its
// publishing node as the store's `entity`; the interfaces of the topic's
// publishers (else subscribers), or of the entity's node, are marked by
// direction, the selected topic in the stronger shade of its own.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  // Without declared warn and error rates, an observed rate below LOW or above
  // HIGH times the declared one is off.
  const LOW = 0.8;
  const HIGH = 1.25;
  // Topics every node owns; they say nothing about the design.
  const INFRA_TOPICS = new Set(["/rosout", "/parameter_events", "/clock"]);

  // Row status -> label, meaning and run-state token of its colour.
  const STATUS = {
    ok: [
      "ok",
      "publishing at the declared rate, or at any rate when none is declared",
      "ok",
    ],
    error: ["error", "below the declared error rate", "failed"],
    slow: [
      "slow",
      `below the declared warn rate, else below ${LOW} of the declared rate`,
      "warning",
    ],
    fast: ["fast", `above ${HIGH} of the declared rate`, "warning"],
    silent: [
      "silent",
      "advertised or subscribed, no message in the last interval",
      "failed",
    ],
    intra: [
      "intra",
      "intra-process only: rcl is not called, so nothing is counted",
      "idle",
    ],
    new: ["new", "endpoint created since the last interval", "idle"],
    once: [
      "once",
      "declared to publish once (latched); no rate expected",
      "idle",
    ],
    unseen: [
      "not seen",
      "declared, no traced endpoint although a unit of its nodes runs",
      "idle",
    ],
    offline: [
      "offline",
      "every node of the topic is on a compute unit that is not running",
      "idle",
    ],
  };
  // Problems first within a group.
  const STATUS_ORDER = [
    "silent",
    "error",
    "slow",
    "fast",
    "unseen",
    "new",
    "intra",
    "once",
    "ok",
    "offline",
  ];

  const GROUPS = [
    ["namespace", "Node namespace"],
    ["topic", "Topic namespace"],
    ["none", "None"],
  ];
  // Group of the topics without a known node.
  const NO_NODE = "(no node)";

  // Sort keys by column; a missing value sorts last either way.
  const SORT_KEYS = {
    status: (row) => STATUS_ORDER.indexOf(row.status),
    topic: (row) => row.topic,
    hz: (row) => row.hz,
    declared: (row) => row.declaredHz,
    callback: (row) => row.callbackMs,
    comm: (row) => row.commMs,
    publisher: (row) => row.origin,
    subs: (row) => row.subscribers.length,
  };

  function topicName(topic) {
    if (Array.isArray(topic))
      return `/${topic
        .map((part) => String(part).replace(/^\/+|\/+$/g, ""))
        .filter(Boolean)
        .join("/")}`;
    const text = String(topic || "");
    return text.startsWith("/") ? text : `/${text}`;
  }

  // Topic of a port, or null for a port connected to no topic.
  function portTopic(port) {
    if (port.topic === undefined || port.topic === null) return null;
    const name = topicName(port.topic);
    return name === "/" ? null : name;
  }

  function positive(value) {
    return typeof value === "number" && value > 0 ? value : null;
  }

  // Declared rates of an output port: the frequency its event carries, else that
  // of a periodic process producing it; the warn and error rates the system
  // monitor checks; `once` for a port declared to publish once.
  function declaredRates(entity, port) {
    const event = port.event || {};
    let hz = positive(event.frequency);
    if (hz === null) {
      const producers = new Set((event.trigger_ids || []).map(String));
      (entity.events || []).forEach((process) => {
        const producing =
          producers.has(String(process.unique_id)) ||
          (process.action_ids || [])
            .map(String)
            .includes(String(event.unique_id));
        if (
          producing &&
          process.type === "periodic" &&
          positive(process.frequency)
        )
          hz = Math.max(hz || 0, process.frequency);
      });
    }
    return {
      declaredHz: hz,
      warnHz: hz === null ? null : positive(event.warn_rate),
      errorHz: hz === null ? null : positive(event.error_rate),
      once: event.frequency === 0,
    };
  }

  // Topic -> { topic, msgType, publishers: [{path, declaredHz, warnHz, errorHz, once, intra}],
  // subscribers: [path], units, declaredHz, warnHz, errorHz, once } from the node entities of the
  // mode; `path` is the runtime member path, `units` the compute units of its nodes. The topic's
  // rates are its fastest publisher's.
  function designTopics(entities) {
    const topics = new Map();
    const entry = (topic, msgType) => {
      if (!topics.has(topic))
        topics.set(topic, {
          topic,
          msgType,
          publishers: [],
          subscribers: [],
          units: new Set(),
          declaredHz: null,
          warnHz: null,
          errorHz: null,
          once: false,
        });
      return topics.get(topic);
    };
    entities.forEach((entity) => {
      if (entity.entity_type !== "node") return;
      const path = entity.member_path || entity.path;
      const intra = !!entity.launcher?.use_intra_process_comms;
      (entity.out_ports || []).forEach((port) => {
        const name = portTopic(port);
        if (!name) return;
        const record = entry(name, port.msg_type);
        if (entity.compute_unit) record.units.add(entity.compute_unit);
        const rates = declaredRates(entity, port);
        record.publishers.push({ path, ...rates, intra });
        if (
          rates.declaredHz !== null &&
          rates.declaredHz > (record.declaredHz || 0)
        )
          Object.assign(record, {
            declaredHz: rates.declaredHz,
            warnHz: rates.warnHz,
            errorHz: rates.errorHz,
          });
        if (rates.once) record.once = true;
      });
      (entity.in_ports || []).forEach((port) => {
        const name = portTopic(port);
        if (!name) return;
        const record = entry(name, port.msg_type);
        if (entity.compute_unit) record.units.add(entity.compute_unit);
        record.subscribers.push(path);
      });
    });
    return topics;
  }

  // Topic -> { publishers: [{node, unit, hz, callback_ms, silent_s}], subscribers: [{node, unit, hz, comm_ms}] }
  // over the monitor snapshots of the running units.
  function observedTopics(snapshots) {
    const topics = new Map();
    const entry = (topic) => {
      if (!topics.has(topic))
        topics.set(topic, { publishers: [], subscribers: [] });
      return topics.get(topic);
    };
    snapshots.forEach((snapshot, unit) => {
      (snapshot.nodes || []).forEach((node) => {
        (node.outputs || []).forEach((row) =>
          entry(row.topic).publishers.push({ node: node.node, unit, ...row }),
        );
        (node.inputs || []).forEach((row) =>
          entry(row.topic).subscribers.push({ node: node.node, unit, ...row }),
        );
      });
    });
    return topics;
  }

  function sum(values) {
    return values.reduce((total, value) => total + value, 0);
  }

  function mean(values) {
    return values.length ? sum(values) / values.length : null;
  }

  // Rate of a topic: the sum of its publishes, else the fastest take where no publish is counted
  // (a publisher off the monitored units); null while every endpoint is new.
  function topicRate(publishers, subscribers) {
    const pubs = publishers.filter((p) => p.hz !== null && p.hz !== undefined);
    const pubHz = sum(pubs.map((p) => p.hz));
    if (pubHz > 0) return { hz: pubHz, from: "publish" };
    const subs = subscribers.filter((s) => s.hz !== null && s.hz !== undefined);
    const subHz = subs.length ? Math.max(...subs.map((s) => s.hz)) : 0;
    if (subHz > 0) return { hz: subHz, from: "take" };
    if (!pubs.length && !subs.length) return { hz: null, from: null };
    return { hz: 0, from: null };
  }

  function statusOf(row, design, runningUnits) {
    const observed = row.publishers.length + row.subscribers.length;
    if (!observed) {
      const reachable =
        !runningUnits ||
        !design?.units.size ||
        [...design.units].some((u) => runningUnits.has(u));
      return reachable ? "unseen" : "offline";
    }
    if (row.hz === null) return "new";
    if (row.hz === 0) {
      if (design?.once && !row.declaredHz) return "once";
      const intra =
        design?.publishers.length && design.publishers.every((p) => p.intra);
      return intra ? "intra" : "silent";
    }
    if (row.declaredHz) {
      if (row.errorHz && row.hz < row.errorHz) return "error";
      if (row.warnHz && row.hz < row.warnHz) return "slow";
      if (!row.warnHz && row.hz < row.declaredHz * LOW) return "slow";
      if (row.hz > row.declaredHz * HIGH) return "fast";
    }
    return "ok";
  }

  // Table rows: one per declared topic, plus the observed undeclared ones when `undeclared`;
  // `runningUnits` tells a topic of stopped units from one that should have been seen.
  function topicRows(
    design,
    observed,
    { undeclared = false, runningUnits = null } = {},
  ) {
    const names = new Set(design.keys());
    if (undeclared) observed.forEach((_value, topic) => names.add(topic));
    const rows = [];
    names.forEach((topic) => {
      if (INFRA_TOPICS.has(topic)) return;
      const declared = design.get(topic) || null;
      const seen = observed.get(topic) || { publishers: [], subscribers: [] };
      const rate = topicRate(seen.publishers, seen.subscribers);
      const row = {
        topic,
        declared: !!declared,
        msgType: declared?.msgType || "",
        declaredHz: declared?.declaredHz ?? null,
        warnHz: declared?.warnHz ?? null,
        errorHz: declared?.errorHz ?? null,
        hz: rate.hz,
        rateFrom: rate.from,
        publishers: seen.publishers,
        subscribers: seen.subscribers,
        designPublishers: declared
          ? declared.publishers.map((p) => p.path)
          : [],
        designSubscribers: declared ? declared.subscribers : [],
        callbackMs: mean(
          seen.publishers
            .filter((p) => p.callback_ms !== undefined)
            .map((p) => p.callback_ms),
        ),
        commMs: mean(
          seen.subscribers
            .filter((s) => s.comm_ms !== undefined)
            .map((s) => s.comm_ms),
        ),
        silentS:
          Math.max(0, ...seen.publishers.map((p) => p.silent_s || 0)) || null,
      };
      row.origin = row.designPublishers[0] || seen.publishers[0]?.node || null;
      row.status = statusOf(row, declared, runningUnits);
      rows.push(row);
    });
    return rows;
  }

  function segments(path) {
    return String(path || "")
      .split("/")
      .filter(Boolean);
  }

  // Namespace segments a row groups under: those of its publishing node, else
  // of its first subscriber, or of the topic; null for a topic without a node.
  function groupPath(row, method) {
    if (method === "topic") return segments(row.topic).slice(0, -1);
    const node =
      row.origin || row.designSubscribers[0] || row.subscribers[0]?.node;
    return node ? segments(node).slice(0, -1) : null;
  }

  // Row order by `key`, ascending for a positive `dir`; ties by topic.
  function compareRows(key = "status", dir = 1) {
    const value = SORT_KEYS[key] || SORT_KEYS.status;
    const missing = (v) => v === null || v === undefined;
    return (a, b) => {
      const x = value(a);
      const y = value(b);
      const absent = missing(x) - missing(y);
      if (absent) return absent;
      const order = missing(x)
        ? 0
        : typeof x === "string"
          ? x.localeCompare(y)
          : x - y;
      return dir * order || a.topic.localeCompare(b.topic);
    };
  }

  // Group tree of the rows: { label, path, children, rows, count, statuses },
  // children by label and rows by `compare`. The root holds the ungrouped rows;
  // a group with no topic of its own and a single sub-group merges with it.
  function groupTree(rows, method, compare = compareRows()) {
    const node = (label, path) => ({
      label,
      path,
      children: new Map(),
      rows: [],
      count: 0,
      statuses: new Map(),
    });
    const child = (at, label, path) => {
      if (!at.children.has(label)) at.children.set(label, node(label, path));
      return at.children.get(label);
    };
    const root = node("", "");
    rows.forEach((row) => {
      let at = root;
      if (method !== "none") {
        const parts = groupPath(row, method);
        if (parts === null) at = child(root, NO_NODE, NO_NODE);
        else
          parts.forEach(
            (part) => (at = child(at, `/${part}`, `${at.path}/${part}`)),
          );
      }
      at.rows.push(row);
    });
    const finish = (at) => {
      at.children = Array.from(at.children.values())
        .map(finish)
        .sort((a, b) => a.label.localeCompare(b.label));
      at.rows.sort(compare);
      at.count = at.rows.length;
      at.rows.forEach((row) =>
        at.statuses.set(row.status, (at.statuses.get(row.status) || 0) + 1),
      );
      at.children.forEach((sub) => {
        at.count += sub.count;
        sub.statuses.forEach((n, status) =>
          at.statuses.set(status, (at.statuses.get(status) || 0) + n),
        );
      });
      return at;
    };
    const compact = (at) => {
      while (!at.rows.length && at.children.length === 1) {
        const only = at.children[0];
        at = { ...only, label: at.label + only.label };
      }
      at.children = at.children.map(compact);
      return at;
    };
    finish(root);
    root.children = root.children.map(compact);
    return root;
  }

  // Publishing and subscribing nodes of a row, declared or seen.
  function rowNodes(row) {
    return {
      pub: new Set([
        ...row.designPublishers,
        ...row.publishers.map((p) => p.node),
      ]),
      sub: new Set([
        ...row.designSubscribers,
        ...row.subscribers.map((s) => s.node),
      ]),
    };
  }

  // Nodes whose interfaces a selected topic marks: its publishers, else its subscribers.
  function topicNodes(row) {
    const { pub, sub } = rowNodes(row);
    return pub.size ? pub : sub;
  }

  // Topic -> "in" | "out" | "both": the interfaces of `nodes` by direction.
  function flowMarks(rows, nodes) {
    const marks = new Map();
    if (!nodes.size) return marks;
    rows.forEach((row) => {
      const { pub, sub } = rowNodes(row);
      const out = [...nodes].some((n) => pub.has(n));
      const input = [...nodes].some((n) => sub.has(n));
      if (out || input)
        marks.set(row.topic, out && input ? "both" : out ? "out" : "in");
    });
    return marks;
  }

  function hzLabel(hz) {
    if (hz === null || hz === undefined) return "–";
    if (hz === 0) return "0";
    return hz >= 10 ? hz.toFixed(1) : hz.toFixed(2);
  }

  function msLabel(ms) {
    if (ms === null || ms === undefined) return "";
    return ms >= 10 ? `${ms.toFixed(1)} ms` : `${ms.toFixed(2)} ms`;
  }

  // Short name of a node path: its last two segments.
  function shortNode(path) {
    const parts = String(path).split("/").filter(Boolean);
    return parts.slice(-2).join("/");
  }

  // ---- view -----------------------------------------------------------------

  const GROUP_KEY = "workbench.topicGroup";
  const UNDECLARED_KEY = "workbench.topicUndeclared";
  const STATUSES_KEY = "workbench.topicStatuses";
  const SORT_KEY = "workbench.topicSort";

  // Columns: label, sort key, meaning.
  const COLUMNS = [
    ["Status", "status", "problems first"],
    ["Topic", "topic", ""],
    ["Hz", "hz", "publish rate, else take rate"],
    [
      "Declared",
      "declared",
      "rate the design declares for the output, with its warn and error rates",
    ],
    [
      "Callback",
      "callback",
      "mean publish time after the thread's latest take or timer fire",
    ],
    ["Comm", "comm", "mean take time after the DDS source timestamp"],
    ["Publisher", "publisher", "the publishing node"],
    ["Subs", "subs", "subscribers seen, of the declared ones"],
  ];

  function topicRates(container, config, store) {
    const { element, segmented } = SD.widgets;
    const daemon = SD.daemon;
    const ofMode = launch.scope(config.systemId, () => store.get("mode"));

    let group = SD.storage.get(GROUP_KEY) || "namespace";
    if (!GROUPS.some(([value]) => value === group)) group = "namespace";
    let undeclared = SD.storage.get(UNDECLARED_KEY) === "true";
    // Statuses shown; every status when empty.
    const statuses = new Set(
      String(SD.storage.get(STATUSES_KEY) || "")
        .split(",")
        .filter((status) => STATUS[status]),
    );
    let [sortKey, sortDir] = String(SD.storage.get(SORT_KEY) || "").split(":");
    if (!SORT_KEYS[sortKey]) sortKey = "status";
    sortDir = sortDir === "-1" ? -1 : 1;
    let query = "";
    // Collapsed groups by method and path.
    const collapsed = new Set();
    // The selected topic, and the entity its click selected.
    let selectedTopic = null;
    let topicEntity = null;

    let entities = [];
    let design = new Map();
    let entitiesMode = null;
    // Unit -> { job, data }: the latest snapshot of the unit's launch of the shown mode.
    const snapshots = new Map();
    // The past run shown instead of the live sessions, or null:
    // { name, until, span, result, error }.
    let run = null;
    let runSeq = 0;
    // Units whose monitor this view follows.
    const followed = new Set();
    let shown = false;
    let timer = null;

    // -- toolbar
    const toolbar = element("div", "topic-toolbar");
    const groupSwitch = segmented(GROUPS, (value) => {
      group = value;
      SD.storage.set(GROUP_KEY, value);
      groupSwitch.mark(value);
      render();
    });
    groupSwitch.mark(group);
    const filter = element("input", "topic-filter");
    filter.type = "search";
    filter.placeholder = "Filter topics or nodes";
    filter.addEventListener("input", () => {
      query = filter.value.trim();
      render();
    });
    const toggle = (label, title, key, get, set) => {
      const wrapper = element("label", "topic-toggle");
      wrapper.title = title;
      const input = element("input");
      input.type = "checkbox";
      input.checked = get();
      input.addEventListener("change", () => {
        set(input.checked);
        SD.storage.set(key, String(input.checked));
        render();
      });
      wrapper.append(input, element("span", "", label));
      return wrapper;
    };
    toolbar.append(
      element("span", "topic-toolbar-label", "Group"),
      groupSwitch.element,
      filter,
      toggle(
        "Undeclared",
        "also topics the nodes use outside the design",
        UNDECLARED_KEY,
        () => undeclared,
        (v) => (undeclared = v),
      ),
    );
    // -- past runs: the selector beside the title, the span and the end sample
    const runSelect = element("select", "monitor-run-select");
    runSelect.title = "Live sessions, or the rates record of a past run";
    runSelect.addEventListener("change", () => setRun(runSelect.value));
    const runBar = element("div", "topic-run-bar");
    const spanSelect = element("select", "topic-run-span");
    [
      ["", "Whole run"],
      ["10", "Last 10 s"],
      ["60", "Last 1 min"],
      ["300", "Last 5 min"],
    ].forEach(([value, label]) => {
      const option = element("option", "", label);
      option.value = value;
      spanSelect.appendChild(option);
    });
    const slider = element("input", "topic-run-slider");
    slider.type = "range";
    slider.min = "0";
    slider.step = "1";
    const runTime = element("span", "topic-run-time");
    runBar.append(
      element("span", "topic-toolbar-label", "Span"),
      spanSelect,
      element("span", "topic-toolbar-label", "ending at"),
      slider,
      runTime,
    );
    runBar.hidden = true;
    let slideTimer = null;
    spanSelect.addEventListener("change", () => {
      if (!run) return;
      run.span = spanSelect.value ? Number(spanSelect.value) : null;
      loadRun();
    });
    slider.addEventListener("input", () => {
      if (!run?.result) return;
      runTime.textContent = clock(run.result.times[Number(slider.value)]);
      clearTimeout(slideTimer);
      slideTimer = setTimeout(() => {
        run.until = run.result.times[Number(slider.value)];
        loadRun();
      }, 150);
    });

    const summary = element("div", "topic-summary");
    const note = element("p", "main-empty");
    const tableHost = element("div", "topic-table-host");
    container.append(toolbar, runBar, summary, note, tableHost);

    function clock(t_ns) {
      if (!t_ns) return "";
      const d = new Date(t_ns / 1e6);
      const pad = (n) => String(n).padStart(2, "0");
      return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    // Shows the live sessions (an empty name) or a past run.
    function setRun(name) {
      runSelect.value = name;
      runSeq += 1;
      run = name
        ? { name, until: null, span: null, result: null, error: "" }
        : null;
      runBar.hidden = !run;
      spanSelect.value = "";
      syncFollows();
      if (run) loadRun();
      else render();
    }

    function loadRun() {
      const seq = runSeq;
      const params = { run: run.name };
      if (run.until !== null) params.until_ns = run.until;
      if (run.span !== null) params.span_s = run.span;
      daemon
        .rpc("launch.run_rates", params)
        .then((result) => {
          if (seq !== runSeq) return;
          run.result = result;
          run.error = "";
          slider.max = String(Math.max(result.times.length - 1, 0));
          slider.value = String(result.times.indexOf(result.time_ns));
          runTime.textContent = clock(result.time_ns);
          render();
        })
        .catch((error) => {
          if (seq !== runSeq) return;
          run.error = error.message;
          render();
        });
    }

    // Past runs of the shown mode with a rates record, newest first; the shown run stays selected while listed.
    function loadRuns() {
      runSelect.hidden = !daemon.offers("launch.run_rates");
      if (runSelect.hidden) return;
      daemon
        .rpc("launch.runs", { system: config.systemId })
        .then(({ runs }) => {
          const mode = store.get("mode");
          const listed = runs.filter(
            (r) => r.rates && (!r.mode || r.mode === mode),
          );
          const current = run?.name || "";
          runSelect.replaceChildren(
            ...[
              ["", "Live"],
              ...listed.map((r) => [r.run, launch.runLabel(r)]),
            ].map(([value, label]) => {
              const option = element("option", "", label);
              option.value = value;
              return option;
            }),
          );
          if (current && !listed.some((r) => r.run === current)) setRun("");
          else runSelect.value = current;
        })
        .catch((error) => console.warn("topic runs:", error));
    }

    async function loadDesign() {
      const mode = store.get("mode");
      if (entitiesMode === mode) return;
      const data = await SD.NamespaceTree.loadStructure(config.dataBase, mode);
      entities = launch.nestComposables(SD.NamespaceTree.entities(data));
      design = designTopics(entities);
      entitiesMode = mode;
      render();
    }

    function keyOfNode(path) {
      const entity = entities.find(
        (e) => e.entity_type === "node" && e.member_path === path,
      );
      return entity ? entity.key : "";
    }

    // Follows the monitor of every connected session of the mode that offers one while the live
    // sessions are shown; drops the rest.
    function syncFollows() {
      const wanted = new Set();
      if (shown && !run && daemon.offers("monitor.follow"))
        ofMode.sessions.forEach((session, unit) => {
          if (
            session.state !== "exited" &&
            session.connected &&
            session.control?.monitor
          )
            wanted.add(unit);
        });
      wanted.forEach((unit) => {
        if (followed.has(unit)) return;
        followed.add(unit);
        daemon
          .rpc("monitor.follow", { compute_unit: unit })
          .then(() =>
            daemon.rpc("monitor.sample", { compute_unit: unit, interval: 1.0 }),
          )
          .then((snapshot) => accept(unit, snapshot))
          .catch((error) => {
            followed.delete(unit);
            console.warn("topic rates:", error);
          });
      });
      followed.forEach((unit) => {
        if (wanted.has(unit)) return;
        followed.delete(unit);
        daemon.rpc("monitor.unfollow", { compute_unit: unit }).catch(() => {});
      });
      schedule();
    }

    function accept(unit, snapshot) {
      if (!followed.has(unit)) return;
      snapshots.set(unit, {
        job: ofMode.sessions.get(unit)?.job,
        data: snapshot,
      });
      schedule();
    }

    // Held snapshots of units relaunched since, or of sessions no longer known, are dropped.
    function pruneHeld() {
      snapshots.forEach((held, unit) => {
        if (ofMode.sessions.get(unit)?.job !== held.job) snapshots.delete(unit);
      });
    }

    function schedule() {
      if (timer || !shown) return;
      timer = setTimeout(() => {
        timer = null;
        render();
      }, 100);
    }

    function matches(row) {
      if (statuses.size && !statuses.has(row.status)) return false;
      if (!query) return true;
      const needle = query.toLowerCase();
      return (
        row.topic.toLowerCase().includes(needle) ||
        [
          ...row.designPublishers,
          ...row.designSubscribers,
          ...row.publishers.map((p) => p.node),
        ].some((node) => String(node).toLowerCase().includes(needle))
      );
    }

    function statusChip(status) {
      const [label, meaning, token] = STATUS[status];
      const chip = element("span", `topic-status ${token}`, label);
      chip.title = meaning;
      return chip;
    }

    function endpointsTitle(row) {
      const lines = [];
      row.publishers.forEach((p) =>
        lines.push(
          `pub ${p.node} [${p.unit}] ${hzLabel(p.hz)} Hz${p.callback_ms !== undefined ? ` · callback ${msLabel(p.callback_ms)}` : ""}`,
        ),
      );
      row.subscribers.forEach((s) =>
        lines.push(
          `sub ${s.node} [${s.unit}] ${hzLabel(s.hz)} Hz${s.comm_ms !== undefined ? ` · comm ${msLabel(s.comm_ms)}` : ""}`,
        ),
      );
      const missing = row.designSubscribers.filter(
        (path) => !row.subscribers.some((s) => s.node === path),
      );
      missing.forEach((path) => lines.push(`sub ${path} (not seen)`));
      return lines.join("\n");
    }

    function declaredCell(row) {
      const cell = element(
        "td",
        "topic-num muted",
        row.declaredHz ? hzLabel(row.declaredHz) : "",
      );
      const limits = [];
      if (row.warnHz) limits.push(`warn below ${hzLabel(row.warnHz)} Hz`);
      if (row.errorHz) limits.push(`error below ${hzLabel(row.errorHz)} Hz`);
      if (limits.length) cell.title = limits.join(", ");
      return cell;
    }

    function rowElement(row, depth, flow) {
      const tr = element("tr", "topic-row");
      tr.dataset.topic = row.topic;
      if (row.origin) tr.dataset.node = row.origin;
      if (flow) tr.classList.add(`flow-${flow}`);
      if (row.topic === selectedTopic) tr.classList.add("chosen");
      const topicCell = element("td", "topic-name", row.topic);
      topicCell.style.setProperty("--depth", depth);
      topicCell.title = row.msgType
        ? `${row.topic}\n${row.msgType}`
        : row.topic;
      if (!row.declared) topicCell.classList.add("undeclared");
      const rate = element("td", "topic-num", hzLabel(row.hz));
      if (row.rateFrom === "take")
        rate.title = "take rate: no publish counted on the running units";
      if (row.silentS) rate.title = `silent for ${row.silentS} s`;
      const subs = row.subscribers.length;
      const designSubs = row.designSubscribers.length;
      const subsCell = element(
        "td",
        "topic-num",
        designSubs && subs < designSubs
          ? `${subs}/${designSubs}`
          : String(subs),
      );
      subsCell.title = endpointsTitle(row);
      const pubCell = element(
        "td",
        "topic-node",
        row.origin
          ? shortNode(row.origin) +
              (row.publishers.length > 1
                ? ` +${row.publishers.length - 1}`
                : "")
          : "",
      );
      pubCell.title = endpointsTitle(row);
      const statusCell = element("td", "topic-status-cell");
      statusCell.appendChild(statusChip(row.status));
      tr.append(
        statusCell,
        topicCell,
        rate,
        declaredCell(row),
        element("td", "topic-num", msLabel(row.callbackMs)),
        element("td", "topic-num", msLabel(row.commMs)),
        pubCell,
        subsCell,
      );
      return tr;
    }

    // Snapshots, the units they stand for, the summary heading and the note of the shown source.
    function source() {
      if (run) {
        const result = run.result;
        if (!result)
          return {
            shown: new Map(),
            units: new Set(),
            heading: "",
            note: run.error || "Loading the run…",
          };
        const unit = result.compute_unit || "run";
        return {
          shown: new Map([[unit, result]]),
          units: new Set([unit]),
          heading: `${unit} · ${result.interval_s} s from ${clock(result.from_ns)} to ${clock(result.time_ns)}`,
          note: run.error,
        };
      }
      pruneHeld();
      const connected = [];
      ofMode.sessions.forEach((session, unit) => {
        if (session.state !== "exited" && session.connected)
          connected.push([unit, session]);
      });
      const monitored = connected
        .filter(([, session]) => session.control?.monitor)
        .map(([unit]) => unit);
      const held = [...snapshots.keys()].filter(
        (unit) => !connected.some(([live]) => live === unit),
      );
      let text = "";
      if (!daemon.offers("monitor.follow"))
        text = "This daemon serves no topic rates.";
      else if (!connected.length && !held.length)
        text = `No running launch of ${store.get("mode")}.`;
      else if (connected.length && !monitored.length && !held.length)
        text =
          "The running launches have no tracer: launched with --no-tracer, or it is not installed.";
      const parts = [];
      if (monitored.length) parts.push(monitored.join(", "));
      if (held.length) parts.push(`${held.join(", ")} ended, last rates held`);
      return {
        shown: new Map(
          [...snapshots].map(([unit, entry]) => [unit, entry.data]),
        ),
        units: new Set([...connected.map(([unit]) => unit), ...held]),
        heading: parts.join(" · "),
        note: text,
      };
    }

    // Status chip that toggles the status in the shown set.
    function statusFilterItem(status, count) {
      const item = element("button", "topic-summary-item topic-status-filter");
      item.type = "button";
      const active = statuses.has(status);
      item.classList.toggle("active", active);
      item.classList.toggle("dimmed", statuses.size > 0 && !active);
      item.setAttribute("aria-pressed", String(active));
      item.append(statusChip(status), element("span", "", String(count)));
      item.addEventListener("click", () => {
        if (active) statuses.delete(status);
        else statuses.add(status);
        setStatuses();
      });
      return item;
    }

    function setStatuses(list = null) {
      if (list) {
        statuses.clear();
        list.forEach((status) => statuses.add(status));
      }
      SD.storage.set(STATUSES_KEY, [...statuses].join(","));
      render();
    }

    function headElement() {
      const head = element("tr");
      COLUMNS.forEach(([label, key, title]) => {
        const th = element("th", "topic-sortable", label);
        if (title) th.title = title;
        if (key === sortKey) {
          th.classList.add("sorted");
          th.appendChild(
            element("span", "topic-sort-mark", sortDir > 0 ? "▲" : "▼"),
          );
        }
        th.addEventListener("click", () => {
          sortDir = key === sortKey ? -sortDir : 1;
          sortKey = key;
          SD.storage.set(SORT_KEY, `${sortKey}:${sortDir}`);
          render();
        });
        head.appendChild(th);
      });
      const thead = element("thead");
      thead.appendChild(head);
      return thead;
    }

    function groupElement(node, depth, open, key) {
      const tr = element("tr", "topic-group");
      const td = element("td", "", "");
      td.colSpan = COLUMNS.length;
      td.style.setProperty("--depth", depth);
      td.append(
        element("span", "topic-caret", open ? "▾" : "▸"),
        element("span", "topic-group-label", node.label),
      );
      STATUS_ORDER.filter(
        (status) => status !== "ok" && node.statuses.get(status),
      ).forEach((status) => {
        const item = element("span", "topic-summary-item");
        item.append(
          statusChip(status),
          element("span", "", String(node.statuses.get(status))),
        );
        td.appendChild(item);
      });
      td.appendChild(element("span", "topic-group-count", `${node.count}`));
      tr.appendChild(td);
      tr.addEventListener("click", () => {
        if (collapsed.has(key)) collapsed.delete(key);
        else collapsed.add(key);
        render();
      });
      return tr;
    }

    // Sub-groups of a tree node, then its own topics.
    function appendTree(body, node, depth, marks) {
      node.children.forEach((child) => {
        const key = `${group}:${child.path}`;
        const open = !collapsed.has(key);
        body.appendChild(groupElement(child, depth, open, key));
        if (open) appendTree(body, child, depth + 1, marks);
      });
      node.rows.forEach((row) =>
        body.appendChild(rowElement(row, depth, marks.get(row.topic))),
      );
    }

    function render() {
      if (!shown) return;
      const view = source();
      note.textContent = view.note;
      note.hidden = !note.textContent;
      const rows = topicRows(design, observedTopics(view.shown), {
        undeclared,
        runningUnits: view.units,
      });
      const counts = new Map();
      rows.forEach((row) =>
        counts.set(row.status, (counts.get(row.status) || 0) + 1),
      );
      const filterAction = element("button", "topic-filter-action");
      filterAction.type = "button";
      if (statuses.size) {
        filterAction.textContent = "Show all";
        filterAction.addEventListener("click", () => setStatuses([]));
      } else {
        filterAction.textContent = "Problems";
        filterAction.title = "every status but ok";
        filterAction.addEventListener("click", () =>
          setStatuses(STATUS_ORDER.filter((status) => status !== "ok")),
        );
      }

      // Nodes whose interfaces are marked: the selected topic's, else the entity's.
      const selected = rows.find((row) => row.topic === selectedTopic) || null;
      const selectedKey = store.get("entity");
      const entityPath =
        entities.find((e) => e.key === selectedKey)?.member_path || "";
      const nodes = selected
        ? topicNodes(selected)
        : new Set(entityPath ? [entityPath] : []);
      const marks = flowMarks(rows, nodes);
      const legend = element("span", "topic-flow-legend");
      if (nodes.size) {
        const of =
          nodes.size === 1 ? shortNode([...nodes][0]) : `${nodes.size} nodes`;
        legend.title = [...nodes].join("\n");
        legend.append(
          element("span", "topic-flow-swatch flow-in"),
          element("span", "", "input"),
          element("span", "topic-flow-swatch flow-out"),
          element("span", "", `output of ${of}`),
        );
      }

      summary.replaceChildren(
        element(
          "span",
          "topic-summary-units",
          view.heading
            ? `${view.heading} · ${rows.length} topics`
            : `${rows.length} topics`,
        ),
        ...STATUS_ORDER.filter((status) => counts.get(status)).map((status) =>
          statusFilterItem(status, counts.get(status)),
        ),
        filterAction,
        legend,
      );

      const table = element("table", "topic-table");
      table.appendChild(headElement());
      const body = element("tbody");
      appendTree(
        body,
        groupTree(rows.filter(matches), group, compareRows(sortKey, sortDir)),
        0,
        marks,
      );
      table.appendChild(body);
      const scroll = tableHost.scrollTop;
      tableHost.replaceChildren(table);
      tableHost.scrollTop = scroll;
    }

    // A row click selects its topic and publishing node; a second click drops both.
    tableHost.addEventListener("click", (event) => {
      const row = event.target.closest?.("tr.topic-row");
      if (!row) return;
      if (row.dataset.topic === selectedTopic) {
        selectedTopic = null;
        topicEntity = null;
        store.set("entity", "");
        render();
        return;
      }
      selectedTopic = row.dataset.topic;
      topicEntity = row.dataset.node ? keyOfNode(row.dataset.node) : "";
      store.set("entity", topicEntity);
      render();
    });
    SD.on("daemon.monitor", (event) => {
      if (event.system !== config.systemId || event.mode !== store.get("mode"))
        return;
      accept(event.compute_unit, event);
    });
    SD.on("launch.sessions", () => {
      if (!shown) return;
      syncFollows();
      loadRuns();
    });
    // An entity selected elsewhere drops the selected topic.
    store.onChange("entity", (key) => {
      if (selectedTopic !== null && key !== topicEntity) {
        selectedTopic = null;
        topicEntity = null;
      }
      schedule();
    });
    SD.on("selection.clear", () => {
      selectedTopic = null;
      topicEntity = null;
      schedule();
    });
    store.onChange("mode", () => {
      snapshots.clear();
      selectedTopic = null;
      topicEntity = null;
      if (run) setRun("");
      entitiesMode = null;
      followed.forEach((unit) =>
        daemon.rpc("monitor.unfollow", { compute_unit: unit }).catch(() => {}),
      );
      followed.clear();
      if (shown) {
        loadDesign().catch((error) => console.error("topic rates:", error));
        syncFollows();
        loadRuns();
      }
    });

    return {
      runSelector: runSelect,
      show() {
        if (shown) return;
        shown = true;
        loadDesign().catch((error) => console.error("topic rates:", error));
        syncFollows();
        loadRuns();
        render();
      },
      hide() {
        if (!shown) return;
        shown = false;
        syncFollows();
      },
    };
  }

  launch.topicRates = topicRates;
  launch.designTopics = designTopics;
  launch.observedTopics = observedTopics;
  launch.topicRows = topicRows;
  launch.groupTree = groupTree;
  launch.compareRows = compareRows;
  launch.flowMarks = flowMarks;
  launch.topicNodes = topicNodes;
  launch.TOPIC_STATUS = STATUS;
})();
