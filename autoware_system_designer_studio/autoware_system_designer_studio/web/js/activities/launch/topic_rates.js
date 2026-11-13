// Launch: live topic rates of a mode. One row per topic, joining the design
// (publishing and subscribing nodes, the rate and the warn and error rates
// its output port declares) with the runtimes' counters of every compute unit
// running the mode (publish and take rates, callback and communication times).
// The list is one of three: the nodes in their namespace tree, the topics in
// the topic namespace tree with the services in a group of their own, or the
// flat topic table; each sorts by a column
// within its groups and filters by status. Above the node and topic lists a
// viewer draws the selected one: a node between the topics it subscribes and
// publishes with the services it calls and serves, a topic between its
// publishers and subscribers, a service between its server and clients; a card of the diagram opens
// its topic or node in the other list. The
// rates flow only while the view is shown: it follows
// the monitor of each connected session and releases it when hidden; the last
// rates of a launch stay shown after it ended, until its unit is launched
// again. A selector beside the title shows a past launch instead, from the
// rates records the runtime leaves in the log directories of its units' runs,
// over the whole launch or the span between two samples, brushed over the
// launch's event density. A topic row click selects the topic and
// its publishing node as the store's `entity`, a node row click the node; the
// topic rows of the interfaces of the topic's publishers (else subscribers),
// or of the entity's node, are marked by direction, the selected topic in the
// stronger shade of its own.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});
  const {
    STATUS,
    QUIET,
    STATUS_ORDER,
    LISTS,
    SORT_KEYS,
    NODE_SORT_KEYS,
    designTopics,
    observedTopics,
    mean,
    topicRows,
    segments,
    SERVICES,
    topicGroup,
    nodeGroup,
    compareRows,
    compareNodes,
    groupTree,
    nodeRows,
    topicEnds,
    topicNodes,
    flowMarks,
    hzLabel,
    msLabel,
    shortNode,
    timeShare,
    sampleAtShare,
    densityPath,
    launchTimeline,
    recordSpan,
  } = launch.topicModel;

  const LIST_KEY = "workbench.topicList";
  const UNDECLARED_KEY = "workbench.topicUndeclared";
  const STATUSES_KEY = "workbench.topicStatuses";
  const SORT_KEY = "workbench.topicSort";
  const VIEWER_KEY = "workbench.topicViewerHeight";
  const NODE_SORT_KEY = "workbench.topicNodeSort";

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

  const NODE_COLUMNS = [
    ["Status", "status", "worst status of the outputs, else of the inputs"],
    ["Node", "node", ""],
    ["Sub", "ins", "subscribed topics, + services called"],
    ["Pub", "outs", "published topics, + services served"],
    ["Unit", "unit", "compute unit"],
  ];

  // Sort keys of the columns holding figures.
  const NUMERIC = new Set([
    "hz",
    "declared",
    "callback",
    "comm",
    "subs",
    "ins",
    "outs",
  ]);

  function statusChip(status) {
    const [label, meaning, token] = STATUS[status];
    const { element } = SD.widgets;
    const chip = element("span", `topic-status ${token}`);
    chip.append(element("span", `state-dot ${token}`), label);
    chip.title = meaning;
    return chip;
  }

  // View box of the density strip.
  const DENSITY_WIDTH = 1000;
  const DENSITY_HEIGHT = 100;

  // Sort column and direction kept under `key`.
  function storedSort(key, keys) {
    let [column, dir] = String(SD.storage.get(key) || "").split(":");
    if (!keys[column]) column = "status";
    return { key: column, dir: dir === "-1" ? -1 : 1 };
  }

  function topicRates(container, config, store) {
    const { element, segmented } = SD.widgets;
    const { nodeDiagram, topicDiagram } = launch.topicFlow;
    const daemon = SD.daemon;
    const ofMode = launch.scope(config.systemId, () => store.get("mode"));

    let list = SD.storage.get(LIST_KEY);
    if (!LISTS.some(([value]) => value === list)) list = "node";
    let undeclared = SD.storage.get(UNDECLARED_KEY) === "true";
    // Statuses shown; every status when empty.
    const statuses = new Set(
      String(SD.storage.get(STATUSES_KEY) || "")
        .split(",")
        .filter((status) => STATUS[status]),
    );
    const sorts = {
      topic: storedSort(SORT_KEY, SORT_KEYS),
      node: storedSort(NODE_SORT_KEY, NODE_SORT_KEYS),
    };
    let query = "";
    // Groups opened or closed by hand, by list and path: true for open.
    const folds = new Map();
    // The selected topic, kept in the store under `topic`, and the entity
    // selected with it.
    let selectedTopic = store.get("topic") || null;
    let topicEntity = selectedTopic === null ? null : store.get("entity");
    // The selected node's member path, and the entity it stands for.
    let selectedNode = null;
    let nodeEntity = "";
    // Whether the next render unfolds the groups over the selected row and scrolls it into view.
    let reveal = false;
    // Whether the view, just shown, has yet to open on the selection.
    let opening = false;

    let entities = [];
    let design = new Map();
    let entitiesMode = null;
    // Unit -> { job, data }: the latest snapshot of the unit's launch of the shown mode.
    const snapshots = new Map();
    // The past launch shown instead of the live sessions, or null:
    // { name, runs, records, start, sample, result, shown, units, error }. `runs` are its runs'
    // summaries, `records` run name -> { times, density } of each read record, `result` the
    // launch timeline { times, density, start, sample }, `shown` the rates of the runs over the
    // span by unit and `units` their units. `start` and `sample` are the numbers of the span's
    // first and end samples on the timeline, null for the first and the last of the launch.
    let run = null;
    let runSeq = 0;
    // Units whose monitor this view follows.
    const followed = new Set();
    // Unit -> its latest follow or unfollow request; the next one leaves once it is answered, so
    // the daemon sees them in the order they were made.
    const requests = new Map();
    let shown = false;
    let timer = null;

    // -- toolbar
    const toolbar = element("div", "topic-toolbar");
    const listSwitch = segmented(LISTS, (value) => {
      setList(value);
      render();
    });
    listSwitch.mark(list);
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
      return { element: wrapper, input };
    };
    const undeclaredToggle = toggle(
      "Undeclared",
      "also topics and nodes seen outside the design",
      UNDECLARED_KEY,
      () => undeclared,
      (v) => (undeclared = v),
    );
    toolbar.append(
      element("span", "topic-toolbar-label", "List"),
      listSwitch.element,
      filter,
      undeclaredToggle.element,
    );
    // -- past runs: the selector beside the title, and the span brushed over the run's density
    const unitsOf = launch.modeUnits(config.launchCommands);
    const runs = launch.runSelector({
      systemId: config.systemId,
      unitsOf,
      offered: "launch.run_rates",
      filter: (r) => r.rates && (!r.mode || r.mode === store.get("mode")),
      onChange: (value) => setRun(value),
    });
    const runSelect = runs.element;
    runSelect.title = "Live sessions, or the rates records of a past launch";
    const runBar = element("div", "topic-run-bar");
    const spanLabel = element("span", "topic-toolbar-label", "Span");
    spanLabel.title =
      "Publishes, takes and timer fires per second over the run; " +
      "drag across it to choose the span, its ends or its body to adjust it";
    const brush = element("div", "topic-run-brush");
    const SVG_NS = "http://www.w3.org/2000/svg";
    const densitySvg = document.createElementNS(SVG_NS, "svg");
    densitySvg.setAttribute("class", "topic-run-density");
    densitySvg.setAttribute(
      "viewBox",
      `0 0 ${DENSITY_WIDTH} ${DENSITY_HEIGHT}`,
    );
    densitySvg.setAttribute("preserveAspectRatio", "none");
    const densityArea = document.createElementNS(SVG_NS, "path");
    densitySvg.appendChild(densityArea);
    const range = element("div", "topic-run-range");
    const spanHandle = (label) => {
      const handle = element("div", "topic-run-handle");
      handle.tabIndex = 0;
      handle.setAttribute("role", "slider");
      handle.setAttribute("aria-label", label);
      return handle;
    };
    const fromHandle = spanHandle("Span start");
    const toHandle = spanHandle("Span end");
    range.append(fromHandle, toHandle);
    brush.append(densitySvg, range);
    const runTime = element("span", "topic-run-time");
    const runHover = element("span", "topic-run-hover");
    const wholeRun = element("button", "topic-run-whole", "Whole run");
    wholeRun.type = "button";
    runBar.append(spanLabel, brush, runTime, runHover, wholeRun);
    runBar.hidden = true;
    // Sample numbers { from, to } of a span picked on the brush and not loaded yet.
    let picked = null;
    let pickTimer = null;
    // The brush gesture under way: { anchor } stretches the span from a fixed sample,
    // { grab, from, to } shifts it whole.
    let brushDrag = null;

    function spanEnds() {
      return picked || { from: run.result.start, to: run.result.sample };
    }

    function drawSpan() {
      const { times } = run.result;
      const { from, to } = spanEnds();
      const left = 100 * timeShare(times, from);
      range.style.left = `${left}%`;
      range.style.width = `${100 * timeShare(times, to) - left}%`;
      const seconds = Math.round((times[to] - times[from]) / 1e9);
      runTime.textContent = `${clock(times[from])} – ${clock(times[to])} · ${seconds} s`;
      [
        [fromHandle, from],
        [toHandle, to],
      ].forEach(([handle, index]) => {
        handle.setAttribute("aria-valuemin", "0");
        handle.setAttribute("aria-valuemax", String(times.length - 1));
        handle.setAttribute("aria-valuenow", String(index));
        handle.setAttribute("aria-valuetext", clock(times[index]));
      });
    }

    function pickSpan(from, to) {
      picked = { from, to };
      drawSpan();
    }

    function loadPicked() {
      if (!picked || !run) return;
      run.start = picked.from;
      run.sample = picked.to;
      loadRun();
    }

    function brushShare(clientX) {
      const box = brush.getBoundingClientRect();
      const share = (clientX - box.left) / Math.max(box.width, 1);
      return Math.min(Math.max(share, 0), 1);
    }

    brush.addEventListener("pointerdown", (event) => {
      if (!run?.result || event.button !== 0) return;
      const { from, to } = spanEnds();
      const at = sampleAtShare(run.result.times, brushShare(event.clientX));
      if (event.target === fromHandle) brushDrag = { anchor: to };
      else if (event.target === toHandle) brushDrag = { anchor: from };
      else if (event.target === range) brushDrag = { grab: at, from, to };
      else brushDrag = { anchor: at };
      if (event.target === fromHandle || event.target === toHandle)
        event.target.focus();
      brush.setPointerCapture(event.pointerId);
      brush.classList.add("dragging");
      event.preventDefault();
    });
    brush.addEventListener("pointermove", (event) => {
      if (!run?.result) return;
      const { times, density = [] } = run.result;
      const share = brushShare(event.clientX);
      if (!brushDrag) {
        const t = times[0] + share * (times[times.length - 1] - times[0]);
        const index = Math.max(
          times.findIndex((time) => time >= t),
          1,
        );
        runHover.textContent = `${clock(t)} · ${Math.round(density[index] || 0)} /s`;
        return;
      }
      const at = sampleAtShare(times, share);
      if ("grab" in brushDrag) {
        const shift = Math.min(
          Math.max(at - brushDrag.grab, -brushDrag.from),
          times.length - 1 - brushDrag.to,
        );
        pickSpan(brushDrag.from + shift, brushDrag.to + shift);
      } else if (at !== brushDrag.anchor) {
        pickSpan(
          Math.min(at, brushDrag.anchor),
          Math.max(at, brushDrag.anchor),
        );
      }
    });
    brush.addEventListener("pointerleave", () => {
      runHover.textContent = "";
    });
    const endBrush = () => {
      if (!brushDrag) return;
      brushDrag = null;
      brush.classList.remove("dragging");
      loadPicked();
    };
    brush.addEventListener("pointerup", endBrush);
    brush.addEventListener("pointercancel", endBrush);
    // Arrow keys move a focused end by one sample, Home and End to the farthest it reaches.
    [fromHandle, toHandle].forEach((handle) => {
      handle.addEventListener("keydown", (event) => {
        if (!run?.result) return;
        const last = run.result.times.length - 1;
        const step = {
          ArrowLeft: -1,
          ArrowDown: -1,
          ArrowRight: 1,
          ArrowUp: 1,
          Home: -last,
          End: last,
        }[event.key];
        if (!step) return;
        event.preventDefault();
        const { from, to } = spanEnds();
        if (handle === fromHandle)
          pickSpan(Math.min(Math.max(from + step, 0), to - 1), to);
        else pickSpan(from, Math.min(Math.max(to + step, from + 1), last));
        clearTimeout(pickTimer);
        pickTimer = setTimeout(() => {
          pickTimer = null;
          loadPicked();
        }, 150);
      });
    });
    wholeRun.addEventListener("click", () => {
      if (!run) return;
      picked = null;
      run.start = null;
      run.sample = null;
      loadRun();
    });

    const summary = element("div", "topic-summary");
    const note = element("p", "main-empty");
    const viewer = element("div", "topic-viewer");
    const tableHost = element("div", "topic-table-host");
    const divider = element("div", "topic-divider");
    container.append(
      toolbar,
      runBar,
      summary,
      note,
      viewer,
      divider,
      tableHost,
    );

    // Dragging the divider sets the viewer's height, kept across visits.
    const viewerHeight = parseInt(SD.storage.get(VIEWER_KEY), 10);
    if (viewerHeight) viewer.style.height = `${viewerHeight}px`;
    let drag = null;
    divider.addEventListener("pointerdown", (event) => {
      drag = {
        y: event.clientY,
        height: viewer.getBoundingClientRect().height,
      };
      divider.setPointerCapture(event.pointerId);
      divider.classList.add("resizing");
      event.preventDefault();
    });
    divider.addEventListener("pointermove", (event) => {
      if (!drag) return;
      const height = drag.height + event.clientY - drag.y;
      viewer.style.height = `${Math.max(0, height)}px`;
    });
    const endDrag = () => {
      if (!drag) return;
      drag = null;
      divider.classList.remove("resizing");
      SD.storage.set(
        VIEWER_KEY,
        String(Math.round(viewer.getBoundingClientRect().height)),
      );
    };
    divider.addEventListener("pointerup", endDrag);
    divider.addEventListener("pointercancel", endDrag);

    function setList(value) {
      list = value;
      SD.storage.set(LIST_KEY, value);
      listSwitch.mark(value);
    }

    function clock(t_ns) {
      if (!t_ns) return "";
      const d = new Date(t_ns / 1e6);
      const pad = (n) => String(n).padStart(2, "0");
      return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    }

    // Shows the live sessions (an empty value) or a past launch.
    function setRun(value) {
      runSeq += 1;
      const group = runs.pick(value);
      run = group
        ? {
            name: value,
            runs: group.runs,
            records: new Map(),
            start: null,
            sample: null,
            result: null,
            shown: new Map(),
            units: new Set(),
            error: "",
          }
        : null;
      runBar.hidden = !run;
      picked = null;
      range.style.width = "0";
      runTime.textContent = "";
      densityArea.setAttribute("d", "");
      syncFollows();
      if (run) loadRun();
      else render();
    }

    // Rates of the launch's runs over the span: the whole runs at first, whose records lay out the
    // timeline; then each run whose record covers part of the span, over that part.
    function loadRun() {
      const seq = ++runSeq;
      const { records, result } = run;
      const asked = [];
      run.runs.forEach((summary) => {
        const params = { run: summary.run };
        if (result) {
          const record = records.get(summary.run);
          if (!record) return;
          const { times } = result;
          const span = recordSpan(
            record.times,
            times[run.start ?? 0],
            times[run.sample ?? times.length - 1],
          );
          if (!span) return;
          Object.assign(params, span);
        }
        asked.push({ summary, params });
      });
      launch
        .readRuns(asked, "launch.run_rates", ({ params }) => params)
        .then((answers) => {
          if (seq !== runSeq) return;
          const { error } = answers;
          const read = answers.read.map(({ run: { summary }, result }) => ({
            summary,
            rates: result,
          }));
          if (!run.result) {
            if (!read.length) {
              run.error = error || "The launch recorded no topic rates.";
              render();
              return;
            }
            read.forEach(({ summary, rates }) =>
              records.set(summary.run, {
                times: rates.times,
                density: rates.density || [],
              }),
            );
            const timeline = launchTimeline([...records.values()]);
            run.result = {
              ...timeline,
              start: 0,
              sample: timeline.times.length - 1,
            };
            densityArea.setAttribute(
              "d",
              densityPath(
                timeline.times,
                timeline.density,
                DENSITY_WIDTH,
                DENSITY_HEIGHT,
              ),
            );
          }
          const last = run.result.times.length - 1;
          run.result.start = run.start ?? 0;
          run.result.sample = run.sample ?? last;
          run.error = read.length
            ? ""
            : error || "No run recorded rates over this span.";
          run.shown = new Map();
          run.units = new Set();
          read.forEach(({ summary, rates }) => {
            const unit = rates.compute_unit || summary.compute_unit || "run";
            let key = unit;
            for (let n = 2; run.shown.has(key); n++) key = `${unit} (${n})`;
            run.shown.set(key, rates);
            run.units.add(unit);
          });
          if (!brushDrag && !pickTimer) picked = null;
          drawSpan();
          render();
        });
    }

    async function loadDesign() {
      const mode = store.get("mode");
      if (entitiesMode === mode) return;
      entities = await launch.modeEntities(config.dataBase, mode);
      design = designTopics(entities);
      entitiesMode = mode;
      followEntity(store.get("entity"));
      openSelection();
      render();
    }

    // A view just shown opens on the list of the selection, the topic before
    // the node, with the selected row revealed.
    function openSelection() {
      if (!opening || entitiesMode !== store.get("mode")) return;
      opening = false;
      if (selectedTopic !== null) setList("topic");
      else if (selectedNode) setList("node");
      else return;
      reveal = true;
    }

    function entityOfNode(path) {
      return entities.find(
        (e) => e.entity_type === "node" && e.member_path === path,
      );
    }

    function keyOfNode(path) {
      return entityOfNode(path)?.key || "";
    }

    // The node list follows the store's entity when that is a node.
    function followEntity(key) {
      const entity = entities.find(
        (e) => e.entity_type === "node" && e.key === key,
      );
      selectedNode = entity ? entity.member_path : null;
      nodeEntity = key;
    }

    function selectTopic(topic, origin) {
      selectedTopic = topic;
      topicEntity = origin ? keyOfNode(origin) : "";
      store.set("topic", topic);
      store.set("entity", topicEntity);
      followEntity(topicEntity);
    }

    function dropTopic() {
      selectedTopic = null;
      topicEntity = null;
      store.set("topic", "");
    }

    function selectNode(path) {
      selectedNode = path;
      nodeEntity = path ? keyOfNode(path) : "";
      store.set("entity", nodeEntity);
    }

    // Opens a topic of the node diagram in the topic list.
    function openTopic(row) {
      setList("topic");
      selectTopic(row.topic, row.origin);
      reveal = true;
      render();
    }

    // Opens a node of the topic diagram in the node list; a node outside the
    // design is listed with the undeclared ones.
    function openNode(path) {
      if (!entityOfNode(path) && !undeclared) {
        undeclared = true;
        undeclaredToggle.input.checked = true;
        SD.storage.set(UNDECLARED_KEY, "true");
      }
      setList("node");
      selectNode(path);
      reveal = true;
      render();
    }

    function monitorRpc(unit, method) {
      const request = (requests.get(unit) || Promise.resolve()).then(() =>
        daemon.rpc(method, { compute_unit: unit, client: daemon.clientId }),
      );
      requests.set(
        unit,
        request.catch(() => {}),
      );
      return request;
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
        monitorRpc(unit, "monitor.follow")
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
        monitorRpc(unit, "monitor.unfollow").catch(() => {});
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

    // A node matches by the status of any of its interfaces, and by its path or their topics.
    function matchesNode(row) {
      const faces = [...row.inputs, ...row.outputs].filter((face) => face.row);
      if (statuses.size && !faces.some((face) => statuses.has(face.row.status)))
        return false;
      if (!query) return true;
      const needle = query.toLowerCase();
      return (
        row.node.toLowerCase().includes(needle) ||
        faces.some((face) => face.topic.toLowerCase().includes(needle))
      );
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
      missing.forEach((path) => lines.push(`sub ${path} (unseen)`));
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

    // Topics of a node's side, with its services after a plus.
    function faceCount(faces) {
      const services = faces.filter((face) => face.service).length;
      const topics = faces.length - services;
      return services ? `${topics} +${services}` : String(topics);
    }

    // Cell under the status and name heads: the row's status chip, or its
    // blank slot, before the name.
    function nameCell(status, name) {
      const cell = element("td", "topic-name-cell");
      cell.colSpan = 2;
      cell.append(
        status ? statusChip(status) : element("span", "topic-status"),
        name,
      );
      return cell;
    }

    function nodeRowElement(row) {
      const tr = element("tr", "topic-row");
      tr.dataset.node = row.node;
      if (row.node === selectedNode) tr.classList.add("chosen");
      const name = element(
        "span",
        "topic-name",
        segments(row.node).pop() || row.node,
      );
      name.title = row.node;
      if (!row.declared) name.classList.add("undeclared");
      if (row.container) {
        name.classList.add("container");
        name.title = `${row.node}\nnode container`;
      }
      tr.append(
        nameCell(row.status, name),
        element("td", "topic-num", faceCount(row.inputs)),
        element("td", "topic-num", faceCount(row.outputs)),
        element("td", "topic-node", row.unit),
      );
      return tr;
    }

    function rowElement(row, flow) {
      const tr = element("tr", "topic-row");
      tr.dataset.topic = row.topic;
      if (row.origin) tr.dataset.node = row.origin;
      if (flow) tr.classList.add(`flow-${flow}`);
      if (row.topic === selectedTopic) tr.classList.add("chosen");
      const topicCell = element("span", "topic-name", row.topic);
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
        row.service
          ? String(designSubs)
          : designSubs && subs < designSubs
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
      tr.append(
        nameCell(row.status, topicCell),
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
        const from = result.times[result.start];
        const to = result.times[result.sample];
        return {
          shown: run.shown,
          units: run.units,
          heading: `${launch.unitsLabel(run.units, unitsOf(run.runs[0].mode, run.runs[0].deploy))} · ${Math.round((to - from) / 1e9)} s from ${clock(from)} to ${clock(to)}`,
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

    // Head of `columns`, sorting by `sort` kept under `storageKey`.
    function headElement(columns, sort, storageKey) {
      const head = element("tr");
      columns.forEach(([label, key, title]) => {
        const th = element("th", "topic-sortable", label);
        if (title) th.title = title;
        if (NUMERIC.has(key)) th.classList.add("topic-num");
        if (key === sort.key) {
          th.classList.add("sorted");
          th.appendChild(
            element("span", "topic-sort-mark", sort.dir > 0 ? "▲" : "▼"),
          );
        }
        th.addEventListener("click", () => {
          sort.dir = key === sort.key ? -sort.dir : 1;
          sort.key = key;
          SD.storage.set(storageKey, `${sort.key}:${sort.dir}`);
          render();
        });
        head.appendChild(th);
      });
      const thead = element("thead");
      thead.appendChild(head);
      return thead;
    }

    function groupElement(node, depth, open, key, span) {
      const tr = element("tr", "topic-group");
      const td = element("td", "", "");
      td.colSpan = span;
      td.style.setProperty("--depth", depth);
      td.append(
        element("span", "topic-caret", open ? "▾" : "▸"),
        element("span", "topic-group-label", node.label),
      );
      STATUS_ORDER.filter(
        (status) => !QUIET.has(status) && node.statuses.get(status),
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
        folds.set(key, !open);
        render();
      });
      return tr;
    }

    // The group "/" of the rows the root of a tree holds beside its sub-groups.
    function rootGroup(root) {
      const statuses = new Map();
      root.rows.forEach((row) => {
        if (row.status)
          statuses.set(row.status, (statuses.get(row.status) || 0) + 1);
      });
      return {
        label: "/",
        path: "/",
        children: [],
        rows: root.rows,
        count: root.rows.length,
        statuses,
      };
    }

    // Whether a group left untouched is open: a top group is closed and the
    // groups under it open; the services group is open, the groups right under
    // it closed and the deeper ones open. A filter text opens every group.
    function groupOpen(key, path, depth) {
      if (query) return true;
      if (folds.has(key)) return folds.get(key);
      const services = `/${SERVICES}`;
      return path === services || path.startsWith(`${services}/`)
        ? depth !== 1
        : depth > 0;
    }

    // A tree node's own rows drawn by `rowOf(row)`, then its sub-groups, each
    // level indented by depth. Root rows beside sub-groups sit in the group "/".
    function appendTree(body, node, depth, span, rowOf) {
      const children =
        !depth && node.rows.length && node.children.length
          ? [rootGroup(node), ...node.children]
          : node.children;
      if (children === node.children)
        node.rows.forEach((row) => {
          const tr = rowOf(row);
          tr.style.setProperty("--depth", depth);
          body.appendChild(tr);
        });
      children.forEach((child) => {
        const key = `${list}:${child.path}`;
        const open = groupOpen(key, child.path, depth);
        body.appendChild(groupElement(child, depth, open, key, span));
        if (open) appendTree(body, child, depth + 1, span, rowOf);
      });
    }

    // Unfolds the groups over a row sitting under the namespace `parts`, the
    // group "/" for none.
    function unfold(parts) {
      if (!parts.length) folds.set(`${list}:/`, true);
      let path = "";
      parts.forEach((part) => {
        path += `/${part}`;
        folds.set(`${list}:${path}`, true);
      });
    }

    function hint(text) {
      return element("p", "topic-viewer-hint", text);
    }

    // The diagram of the selected node or topic over its list.
    function renderViewer(rows, nodes) {
      viewer.hidden = list === "table";
      divider.hidden = viewer.hidden;
      if (viewer.hidden) return;
      let content;
      if (list === "node") {
        const node = nodes.find((row) => row.node === selectedNode);
        content = node
          ? nodeDiagram(node, { openTopic })
          : hint(
              selectedNode
                ? `${selectedNode} is not listed.`
                : "Select a node to see the topics it subscribes and publishes.",
            );
      } else {
        const row = rows.find((r) => r.topic === selectedTopic);
        content = row
          ? topicDiagram(
              row,
              topicEnds(row, (path) => entityOfNode(path)?.compute_unit),
              { openNode },
            )
          : hint(
              "Select a topic to see its publishers and subscribers, or a service to see its server and clients.",
            );
      }
      const [top, left] = [viewer.scrollTop, viewer.scrollLeft];
      viewer.replaceChildren(content);
      viewer.scrollTop = top;
      viewer.scrollLeft = left;
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
        filterAction.title = "every status but ok and service";
        filterAction.addEventListener("click", () =>
          setStatuses(STATUS_ORDER.filter((status) => !QUIET.has(status))),
        );
      }

      const nodes =
        list === "node" ? nodeRows(entities, rows, { undeclared }) : [];

      // Nodes whose interfaces are marked: the selected topic's, else the entity's.
      const selected = rows.find((row) => row.topic === selectedTopic) || null;
      const selectedKey = store.get("entity");
      const entityPath =
        entities.find((e) => e.key === selectedKey)?.member_path || "";
      const marked =
        list === "node"
          ? new Set()
          : selected
            ? topicNodes(selected)
            : new Set(entityPath ? [entityPath] : []);
      const marks = flowMarks(rows, marked);
      const legend = element("span", "topic-flow-legend");
      if (marked.size) {
        const of =
          marked.size === 1
            ? shortNode([...marked][0])
            : `${marked.size} nodes`;
        legend.title = [...marked].join("\n");
        legend.append(
          element("span", "topic-flow-swatch flow-in"),
          element("span", "", "subscribe"),
          element("span", "topic-flow-swatch flow-out"),
          element("span", "", `publish of ${of}`),
        );
      }

      const services = rows.filter((row) => row.service).length;
      const counted = [`${rows.length - services} topics`];
      if (services) counted.push(`${services} services`);
      if (list === "node") counted.unshift(`${nodes.length} nodes`);
      if (view.heading) counted.unshift(view.heading);
      summary.replaceChildren(
        element("span", "topic-summary-units", counted.join(" · ")),
        ...STATUS_ORDER.filter((status) => counts.get(status)).map((status) =>
          statusFilterItem(status, counts.get(status)),
        ),
        filterAction,
        legend,
      );

      renderViewer(rows, nodes);

      const table = element("table", "topic-table");
      const body = element("tbody");
      if (list === "node") {
        const sort = sorts.node;
        table.classList.add("node-list");
        if (reveal && selectedNode) unfold(nodeGroup({ node: selectedNode }));
        table.appendChild(headElement(NODE_COLUMNS, sort, NODE_SORT_KEY));
        appendTree(
          body,
          groupTree(
            nodes.filter(matchesNode),
            nodeGroup,
            compareNodes(sort.key, sort.dir),
          ),
          0,
          NODE_COLUMNS.length,
          nodeRowElement,
        );
      } else {
        const sort = sorts.topic;
        if (reveal && selected) unfold(topicGroup(selected));
        table.appendChild(headElement(COLUMNS, sort, SORT_KEY));
        appendTree(
          body,
          groupTree(
            rows.filter(matches),
            list === "topic" ? topicGroup : undefined,
            compareRows(sort.key, sort.dir),
          ),
          0,
          COLUMNS.length,
          (row) => rowElement(row, marks.get(row.topic)),
        );
      }
      table.appendChild(body);
      const scroll = tableHost.scrollTop;
      tableHost.replaceChildren(table);
      tableHost.scrollTop = scroll;
      if (reveal) {
        reveal = false;
        table
          .querySelector("tr.chosen")
          ?.scrollIntoView?.({ block: "nearest" });
      }
    }

    // A row click selects its node, or its topic and publishing node; a second click drops them.
    tableHost.addEventListener("click", (event) => {
      const row = event.target.closest?.("tr.topic-row");
      if (!row) return;
      if (list === "node") {
        selectNode(row.dataset.node === selectedNode ? null : row.dataset.node);
      } else if (row.dataset.topic === selectedTopic) {
        dropTopic();
        store.set("entity", "");
      } else {
        selectTopic(row.dataset.topic, row.dataset.node);
      }
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
      runs.load();
    });
    // The daemon drops every follow with the last event stream; a reconnected page follows anew.
    SD.on("daemon.connection", ({ connected }) => {
      if (!connected) return;
      followed.clear();
      syncFollows();
    });
    // An entity selected elsewhere drops the selected topic and moves the selected node.
    store.onChange("entity", (key) => {
      if (selectedTopic !== null && key !== topicEntity) dropTopic();
      if (key !== nodeEntity) {
        followEntity(key);
        reveal = Boolean(selectedNode);
      }
      schedule();
    });
    // A topic selected elsewhere, a port's on a diagram, is shown in the topic list.
    store.onChange("topic", (name) => {
      if ((name || null) === selectedTopic) return;
      selectedTopic = name || null;
      topicEntity = name ? store.get("entity") : null;
      if (name) {
        setList("topic");
        reveal = true;
      }
      schedule();
    });
    SD.on("selection.clear", () => {
      dropTopic();
      selectedNode = null;
      nodeEntity = "";
      schedule();
    });
    store.onChange("mode", () => {
      snapshots.clear();
      dropTopic();
      selectedNode = null;
      nodeEntity = "";
      if (run) setRun("");
      entitiesMode = null;
      followed.forEach((unit) =>
        monitorRpc(unit, "monitor.unfollow").catch(() => {}),
      );
      followed.clear();
      if (shown) {
        loadDesign().catch((error) => console.error("topic rates:", error));
        syncFollows();
        runs.load();
      }
    });

    return {
      runSelector: runSelect,
      show() {
        if (shown) return;
        shown = true;
        opening = true;
        openSelection();
        loadDesign().catch((error) => console.error("topic rates:", error));
        syncFollows();
        runs.load();
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
  launch.topicLabels = {
    hz: hzLabel,
    ms: msLabel,
    node: shortNode,
    statusChip,
  };
})();
