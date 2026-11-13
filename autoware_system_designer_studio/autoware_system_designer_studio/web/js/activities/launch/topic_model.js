// Launch: model of the Topics view. The design topics of a mode with their
// declared rates, the rates the runtimes observe, the status and grouping of
// the topic and node rows, the ends of a topic, and the density of a past
// run's samples.
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
      "unseen",
      "declared, no traced endpoint although a unit of its nodes runs",
      "idle",
    ],
    offline: [
      "offline",
      "every node of the topic is on a compute unit that is not running",
      "idle",
    ],
    service: ["service", "a service: its calls are not counted", "idle"],
  };
  // Statuses that tell of no problem.
  const QUIET = new Set(["ok", "service"]);
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
    "service",
  ];

  // Lists of the view: nodes or topics in their namespace tree under the
  // viewer of the selected one, or the flat topic table.
  const LISTS = [
    ["node", "Nodes"],
    ["topic", "Topics"],
    ["table", "Table"],
  ];

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
  const NODE_SORT_KEYS = {
    status: (row) => (row.status ? STATUS_ORDER.indexOf(row.status) : null),
    node: (row) => row.node,
    ins: (row) => row.inputs.length,
    outs: (row) => row.outputs.length,
    unit: (row) => row.unit || null,
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

  // Whether a port is a service's server or client: by its role, else by its
  // type on an export that carries no role.
  function isService(port) {
    if (port.role) return port.role === "server" || port.role === "client";
    return String(port.msg_type || "").includes("/srv/");
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
  // rates are the sums over its publishers that declare one, as its observed rate is the sum of
  // their publishes; a warn or error rate that one of them lacks is none.
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
          service: false,
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
        if (isService(port)) record.service = true;
        if (entity.compute_unit) record.units.add(entity.compute_unit);
        const rates = declaredRates(entity, port);
        record.publishers.push({ path, ...rates, intra });
        if (rates.once) record.once = true;
      });
      (entity.in_ports || []).forEach((port) => {
        const name = portTopic(port);
        if (!name) return;
        const record = entry(name, port.msg_type);
        if (isService(port)) record.service = true;
        if (entity.compute_unit) record.units.add(entity.compute_unit);
        record.subscribers.push(path);
      });
    });
    topics.forEach((record) => {
      const rated = record.publishers.filter((p) => p.declaredHz !== null);
      if (!rated.length) return;
      const total = (field) =>
        rated.every((p) => p[field]) ? sum(rated.map((p) => p[field])) : null;
      record.declaredHz = sum(rated.map((p) => p.declaredHz));
      record.warnHz = total("warnHz");
      record.errorHz = total("errorHz");
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
    const rated = (design?.publishers || []).filter((p) => p.declaredHz);
    if (rated.length > 1) return publishersStatus(rated, row.publishers);
    return row.declaredHz ? rateStatus(row.hz, row) : "ok";
  }

  // A rate against the declared one and its warn and error rates.
  function rateStatus(hz, { declaredHz, warnHz, errorHz }) {
    if (errorHz && hz < errorHz) return "error";
    if (warnHz && hz < warnHz) return "slow";
    if (!warnHz && hz < declaredHz * LOW) return "slow";
    if (hz > declaredHz * HIGH) return "fast";
    return "ok";
  }

  // Status of a topic with several rated publishers: each one's own publish rate against its own
  // declaration, the first in STATUS_ORDER of them; a publisher not seen says nothing.
  function publishersStatus(rated, seen) {
    const found = rated.map((declared) => {
      const own = seen.filter(
        (p) => p.node === declared.path && p.hz !== null && p.hz !== undefined,
      );
      if (!own.length) return "ok";
      const hz = sum(own.map((p) => p.hz));
      if (hz === 0) return declared.intra ? "intra" : "silent";
      return rateStatus(hz, declared);
    });
    return STATUS_ORDER[
      Math.min(...found.map((status) => STATUS_ORDER.indexOf(status)))
    ];
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
        service: !!declared?.service,
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
      row.status = row.service
        ? "service"
        : statusOf(row, declared, runningUnits);
      rows.push(row);
    });
    return rows;
  }

  function segments(path) {
    return String(path || "")
      .split("/")
      .filter(Boolean);
  }

  // Namespace segments a topic row or a node row groups under.
  // The services sit under a group of their own.
  const SERVICES = "(services)";
  const topicGroup = (row) => {
    const namespace = segments(row.topic).slice(0, -1);
    return row.service ? [SERVICES, ...namespace] : namespace;
  };
  const nodeGroup = (row) => segments(row.node).slice(0, -1);

  // Order by `value`, ascending for a positive `dir`, a missing value last
  // either way; ties by `name`.
  function compareBy(value, dir, name) {
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
      return dir * order || name(a).localeCompare(name(b));
    };
  }

  // Topic row order by a column.
  function compareRows(key = "status", dir = 1) {
    return compareBy(SORT_KEYS[key] || SORT_KEYS.status, dir, (r) => r.topic);
  }

  // Node row order by a column.
  function compareNodes(key = "status", dir = 1) {
    return compareBy(
      NODE_SORT_KEYS[key] || NODE_SORT_KEYS.status,
      dir,
      (r) => r.node,
    );
  }

  // Group tree of the rows: { label, path, children, rows, count, statuses },
  // children by label with the services last and rows by `compare`; `pathOf`
  // gives the namespace segments a row sits under, none for the root. A group with no row of its
  // own and a single sub-group merges with it.
  function groupTree(rows, pathOf = () => [], compare = compareRows()) {
    const node = (label, path) => ({
      label,
      path,
      children: new Map(),
      rows: [],
      count: 0,
      statuses: new Map(),
    });
    const root = node("", "");
    rows.forEach((row) => {
      let at = root;
      pathOf(row).forEach((part) => {
        if (!at.children.has(part))
          at.children.set(
            part,
            node(
              part === SERVICES ? "Services" : `/${part}`,
              `${at.path}/${part}`,
            ),
          );
        at = at.children.get(part);
      });
      at.rows.push(row);
    });
    const finish = (at) => {
      at.children = Array.from(at.children.values())
        .map(finish)
        .sort(
          (a, b) =>
            (a.label[0] !== "/") - (b.label[0] !== "/") ||
            a.label.localeCompare(b.label),
        );
      at.rows.sort(compare);
      at.count = at.rows.length;
      at.rows.forEach((row) => {
        if (row.status)
          at.statuses.set(row.status, (at.statuses.get(row.status) || 0) + 1);
      });
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

  // The status first in STATUS_ORDER among the interfaces with a topic, or null.
  function worstStatus(interfaces) {
    const found = interfaces
      .filter((face) => face.row)
      .map((face) => STATUS_ORDER.indexOf(face.row.status));
    return found.length ? STATUS_ORDER[Math.min(...found)] : null;
  }

  // Node rows: one per node of the design, plus, when `undeclared`, the nodes
  // seen outside it and the interfaces seen off the ports:
  // { node, key, unit, declared, inputs, outputs, status }. An interface is
  // { port, topic, row, service, seen, hz, ms }: the port's name (null off the
  // design), its topic's row (null for a port connected to no topic), whether
  // it is a service's, and the node's own take or publish rate with its
  // communication or callback time; topics come ahead of services. The status
  // is the worst of the outputs, else of the inputs.
  function nodeRows(entities, rows, { undeclared = false } = {}) {
    const byTopic = new Map(rows.map((row) => [row.topic, row]));
    const nodes = new Map();
    const SIDES = [
      ["inputs", "in_ports", "subscribers", "comm_ms"],
      ["outputs", "out_ports", "publishers", "callback_ms"],
    ];
    const face = (port, topic, row, service, end, time) => ({
      port,
      topic,
      row,
      service,
      seen: !!end,
      hz: end?.hz ?? null,
      ms: end?.[time] ?? null,
    });
    entities.forEach((entity) => {
      if (entity.entity_type !== "node") return;
      const path = entity.member_path || entity.path;
      const record = {
        node: path,
        key: entity.key || "",
        unit: entity.compute_unit || "",
        declared: true,
        container: entity.launcher?.launch_state === "node_container",
        inputs: [],
        outputs: [],
      };
      nodes.set(path, record);
      SIDES.forEach(([side, ports, ends, time]) =>
        (entity[ports] || []).forEach((port) => {
          const topic = portTopic(port);
          const row = topic ? byTopic.get(topic) : null;
          if (topic && !row) return;
          const end = row?.[ends].find((e) => e.node === path);
          record[side].push(
            face(port.name, topic, row, isService(port), end, time),
          );
        }),
      );
    });
    if (undeclared)
      rows.forEach((row) =>
        SIDES.forEach(([side, , ends, time]) =>
          row[ends].forEach((end) => {
            if (!nodes.has(end.node))
              nodes.set(end.node, {
                node: end.node,
                key: "",
                unit: end.unit || "",
                declared: false,
                inputs: [],
                outputs: [],
              });
            const record = nodes.get(end.node);
            if (record[side].some((f) => f.topic === row.topic)) return;
            record[side].push(
              face(null, row.topic, row, row.service, end, time),
            );
          }),
        ),
      );
    nodes.forEach((record) => {
      record.inputs.sort((a, b) => a.service - b.service);
      record.outputs.sort((a, b) => a.service - b.service);
      record.status = worstStatus(record.outputs) || worstStatus(record.inputs);
    });
    return [...nodes.values()];
  }

  // Source and consumer nodes of a topic row, declared ones first:
  // { sources, consumers } of { node, unit, declared, seen, hz, ms }; `unitOf`
  // gives the compute unit of a declared node without an endpoint.
  function topicEnds(row, unitOf = () => "") {
    const ends = (declared, seen, time) => {
      const list = declared.map((node) => ({ node, declared: true }));
      seen.forEach((end) => {
        if (!list.some((item) => item.node === end.node))
          list.push({ node: end.node, declared: false });
      });
      return list.map((item) => {
        const end = seen.find((e) => e.node === item.node);
        return {
          ...item,
          unit: end?.unit || unitOf(item.node) || "",
          seen: !!end,
          hz: end?.hz ?? null,
          ms: end?.[time] ?? null,
        };
      });
    };
    return {
      sources: ends(row.designPublishers, row.publishers, "callback_ms"),
      consumers: ends(row.designSubscribers, row.subscribers, "comm_ms"),
    };
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

  // Share of the run's duration elapsed at sample number `index`.
  function timeShare(times, index) {
    const total = times[times.length - 1] - times[0];
    return total > 0 ? (times[index] - times[0]) / total : 0;
  }

  // Number of the sample nearest to a share of the run's duration.
  function sampleAtShare(times, share) {
    const t = times[0] + share * (times[times.length - 1] - times[0]);
    let best = 0;
    times.forEach((time, index) => {
      if (Math.abs(time - t) < Math.abs(times[best] - t)) best = index;
    });
    return best;
  }

  // Closed step outline of the density in a width by height box: the rate of a
  // sample spans the interval ending at it, scaled to the peak of the run.
  function densityPath(times, density, width, height) {
    const peak = density.reduce((a, b) => Math.max(a, b), 0);
    if (!(peak > 0) || times.length < 2) return "";
    const round = (value) => Math.round(value * 10) / 10;
    let d = `M0,${height}`;
    for (let i = 1; i < times.length; i++) {
      const y = round(height * (1 - (density[i] || 0) / peak));
      d += `V${y}H${round(width * timeShare(times, i))}`;
    }
    return `${d}V${height}Z`;
  }

  // One timeline over the rates records of a launch's runs, `records` as
  // [{ times, density }]: the samples of every record, with the density at each
  // the sum of the records whose run spans it.
  function launchTimeline(records) {
    const times = [...new Set(records.flatMap((r) => r.times))].sort(
      (a, b) => a - b,
    );
    const density = times.map((t) =>
      records.reduce((total, { times: own, density: rates = [] }) => {
        if (!(own.length > 1 && own[0] < t && t <= own[own.length - 1]))
          return total;
        return total + (rates[own.findIndex((time) => time >= t)] || 0);
      }, 0),
    );
    return { times, density };
  }

  // Sample numbers { start, sample } of a record's span covering the times
  // `from_ns` to `to_ns`; null for a record outside them.
  function recordSpan(times, from_ns, to_ns) {
    const last = times.length - 1;
    if (last < 1 || times[0] >= to_ns || times[last] <= from_ns) return null;
    let start = 0;
    times.forEach((time, index) => {
      if (time <= from_ns) start = index;
    });
    const end = times.findIndex((time) => time >= to_ns);
    return { start, sample: end < 0 ? last : end };
  }

  launch.topicModel = {
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
    isService,
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
  };
  launch.TOPIC_STATUS = STATUS;
})();
