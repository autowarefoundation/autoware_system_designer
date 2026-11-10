// Launch: live topic rates of a mode. One row per topic, joining the design
// (publishing and subscribing nodes, the rate and the warn and error rates
// its output port declares) with the runtimes' counters of every compute unit
// running the mode (publish and take rates, callback and communication times).
// Rows are grouped by the publishing node's component or namespace, or by the
// topic's namespace. The rates flow only while the view is shown: it follows
// the monitor of each connected session and releases it when hidden. A row
// click selects its publishing node as the store's `entity`; rows touching
// that node are marked.
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
    ["component", "Component"],
    ["namespace", "Node namespace"],
    ["topic", "Topic namespace"],
    ["none", "None"],
  ];

  function topicName(topic) {
    if (Array.isArray(topic))
      return `/${topic
        .map((part) => String(part).replace(/^\/+|\/+$/g, ""))
        .filter(Boolean)
        .join("/")}`;
    const text = String(topic || "");
    return text.startsWith("/") ? text : `/${text}`;
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
        if (port.topic === undefined || port.topic === null) return;
        const record = entry(topicName(port.topic), port.msg_type);
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
        if (port.topic === undefined || port.topic === null) return;
        const record = entry(topicName(port.topic), port.msg_type);
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

  function parent(path) {
    const parts = String(path || "")
      .split("/")
      .filter(Boolean);
    parts.pop();
    return `/${parts.join("/")}`;
  }

  // Leading `depth` segments of a name's namespace.
  function prefix(path, depth) {
    const parts = String(path || "")
      .split("/")
      .filter(Boolean);
    parts.pop();
    return `/${parts.slice(0, depth).join("/")}`;
  }

  // Group label of a row: the publishing node's first path segment or parent path, or the first
  // two segments of the topic's namespace. A topic without a known publisher groups under its first subscriber, else "(no publisher)".
  function groupOf(row, method) {
    if (method === "none") return "";
    if (method === "topic") return prefix(row.topic, 2);
    const node =
      row.origin || row.designSubscribers[0] || row.subscribers[0]?.node;
    if (!node) return "(no publisher)";
    if (method === "component")
      return `/${String(node).split("/").filter(Boolean)[0] || ""}`;
    return parent(node);
  }

  // [[label, rows]] with groups by label and rows by status, then topic.
  function groupRows(rows, method) {
    const groups = new Map();
    rows.forEach((row) => {
      const label = groupOf(row, method);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label).push(row);
    });
    const rank = (row) => STATUS_ORDER.indexOf(row.status);
    return Array.from(groups)
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([label, list]) => [
        label,
        list.sort(
          (a, b) => rank(a) - rank(b) || a.topic.localeCompare(b.topic),
        ),
      ]);
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
  const PROBLEMS_KEY = "workbench.topicProblems";

  function topicRates(container, config, store) {
    const { element, segmented } = SD.widgets;
    const daemon = SD.daemon;
    const ofMode = launch.scope(config.systemId, () => store.get("mode"));

    let group = SD.storage.get(GROUP_KEY) || "component";
    if (!GROUPS.some(([value]) => value === group)) group = "component";
    let undeclared = SD.storage.get(UNDECLARED_KEY) === "true";
    let problems = SD.storage.get(PROBLEMS_KEY) === "true";
    let query = "";
    const collapsed = new Set();

    let entities = [];
    let design = new Map();
    let entitiesMode = null;
    // Unit -> latest snapshot of the shown mode.
    const snapshots = new Map();
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
        "Problems only",
        "rows whose status is not ok",
        PROBLEMS_KEY,
        () => problems,
        (v) => (problems = v),
      ),
      toggle(
        "Undeclared",
        "also topics the nodes use outside the design",
        UNDECLARED_KEY,
        () => undeclared,
        (v) => (undeclared = v),
      ),
    );
    const summary = element("div", "topic-summary");
    const note = element("p", "main-empty");
    const tableHost = element("div", "topic-table-host");
    container.append(toolbar, summary, note, tableHost);

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

    // Follows the monitor of every connected session of the mode that offers one; drops the rest.
    function syncFollows() {
      const wanted = new Set();
      if (shown && daemon.offers("monitor.follow"))
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
        snapshots.delete(unit);
        daemon.rpc("monitor.unfollow", { compute_unit: unit }).catch(() => {});
      });
      schedule();
    }

    function accept(unit, snapshot) {
      if (!followed.has(unit)) return;
      snapshots.set(unit, snapshot);
      schedule();
    }

    function schedule() {
      if (timer || !shown) return;
      timer = setTimeout(() => {
        timer = null;
        render();
      }, 100);
    }

    function matches(row) {
      if (problems && row.status === "ok") return false;
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

    function rowElement(row, selectedPath) {
      const tr = element("tr", "topic-row");
      if (row.origin) tr.dataset.node = row.origin;
      const touches =
        selectedPath &&
        (row.origin === selectedPath ||
          row.designSubscribers.includes(selectedPath) ||
          row.subscribers.some((s) => s.node === selectedPath));
      if (touches) tr.classList.add("selected");
      const topicCell = element("td", "topic-name", row.topic);
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

    function render() {
      if (!shown) return;
      const connected = [];
      ofMode.sessions.forEach((session, unit) => {
        if (session.state !== "exited" && session.connected)
          connected.push([unit, session]);
      });
      const monitored = connected.filter(
        ([, session]) => session.control?.monitor,
      );
      if (!daemon.offers("monitor.follow"))
        note.textContent = "This daemon serves no topic rates.";
      else if (!connected.length)
        note.textContent = `No running launch of ${store.get("mode")}.`;
      else if (!monitored.length)
        note.textContent =
          "The running launches have no tracer: launched with --no-tracer, or it is not installed.";
      else note.textContent = "";
      note.hidden = !note.textContent;

      const runningUnits = new Set(connected.map(([unit]) => unit));
      const rows = topicRows(design, observedTopics(snapshots), {
        undeclared,
        runningUnits,
      });
      const counts = new Map();
      rows.forEach((row) =>
        counts.set(row.status, (counts.get(row.status) || 0) + 1),
      );
      summary.replaceChildren(
        element(
          "span",
          "topic-summary-units",
          monitored.length
            ? `${monitored.map(([unit]) => unit).join(", ")} · ${rows.length} topics`
            : `${rows.length} topics`,
        ),
        ...STATUS_ORDER.filter((status) => counts.get(status)).map((status) => {
          const item = element("span", "topic-summary-item");
          item.append(
            statusChip(status),
            element("span", "", String(counts.get(status))),
          );
          return item;
        }),
      );

      const selectedKey = store.get("entity");
      const selectedPath =
        entities.find((e) => e.key === selectedKey)?.member_path || "";
      const table = element("table", "topic-table");
      const head = element("tr");
      [
        ["", ""],
        ["Topic", ""],
        ["Hz", "publish rate, else take rate"],
        [
          "Declared",
          "rate the design declares for the output, with its warn and error rates",
        ],
        [
          "Callback",
          "mean publish time after the thread's latest take or timer fire",
        ],
        ["Comm", "mean take time after the DDS source timestamp"],
        ["Publisher", "the publishing node"],
        ["Subs", "subscribers seen, of the declared ones"],
      ].forEach(([label, title]) => {
        const th = element("th", "", label);
        if (title) th.title = title;
        head.appendChild(th);
      });
      const thead = element("thead");
      thead.appendChild(head);
      table.appendChild(thead);
      groupRows(rows.filter(matches), group).forEach(([label, list]) => {
        const body = element("tbody");
        if (group !== "none") {
          const tr = element("tr", "topic-group");
          const td = element("td", "", "");
          td.colSpan = 8;
          const open = !collapsed.has(label);
          td.append(
            element("span", "topic-caret", open ? "▾" : "▸"),
            element("span", "topic-group-label", label),
          );
          const groupCounts = new Map();
          list.forEach((row) =>
            groupCounts.set(row.status, (groupCounts.get(row.status) || 0) + 1),
          );
          STATUS_ORDER.filter(
            (status) => status !== "ok" && groupCounts.get(status),
          ).forEach((status) => {
            const item = element("span", "topic-summary-item");
            item.append(
              statusChip(status),
              element("span", "", String(groupCounts.get(status))),
            );
            td.appendChild(item);
          });
          td.appendChild(
            element("span", "topic-group-count", `${list.length}`),
          );
          tr.appendChild(td);
          tr.addEventListener("click", () => {
            if (collapsed.has(label)) collapsed.delete(label);
            else collapsed.add(label);
            render();
          });
          body.appendChild(tr);
          if (!open) {
            table.appendChild(body);
            return;
          }
        }
        list.forEach((row) => body.appendChild(rowElement(row, selectedPath)));
        table.appendChild(body);
      });
      const scroll = tableHost.scrollTop;
      tableHost.replaceChildren(table);
      tableHost.scrollTop = scroll;
    }

    tableHost.addEventListener("click", (event) => {
      const row = event.target.closest?.("tr.topic-row");
      if (!row?.dataset.node) return;
      store.set("entity", keyOfNode(row.dataset.node));
    });
    SD.on("daemon.monitor", (event) => {
      if (event.system !== config.systemId || event.mode !== store.get("mode"))
        return;
      accept(event.compute_unit, event);
    });
    SD.on("launch.sessions", () => {
      if (shown) syncFollows();
    });
    store.onChange("entity", schedule);
    store.onChange("mode", () => {
      snapshots.clear();
      entitiesMode = null;
      followed.forEach((unit) =>
        daemon.rpc("monitor.unfollow", { compute_unit: unit }).catch(() => {}),
      );
      followed.clear();
      if (shown) {
        loadDesign().catch((error) => console.error("topic rates:", error));
        syncFollows();
      }
    });

    return {
      show() {
        if (shown) return;
        shown = true;
        loadDesign().catch((error) => console.error("topic rates:", error));
        syncFollows();
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
  launch.groupRows = groupRows;
  launch.TOPIC_STATUS = STATUS;
})();
