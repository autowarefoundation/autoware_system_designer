// Latency Source Module
// Loads a measurement file and answers the timing model's cost queries from
// it. Records are keyed by node path and topic (or process name in the first
// file version), never by unique_id: ids are name hashes and change whenever
// the design is edited. The runtime writes latency/2; latency/1 stays readable.
// The chains come from the design's event graph; the file supplies the time:
// a gate's run from the exec of the output it feeds, the wait of an input
// before that run from the node's measured in→out response, a link's
// transport from the take it was matched with.

(function () {
  const SCHEMA_V1 = "autoware_system_designer/latency/1";
  const SCHEMA = "autoware_system_designer/latency/2";
  const SCHEMAS = new Set([SCHEMA_V1, SCHEMA]);
  const T = window.TimingModel;

  // ── Keys ────────────────────────────────────────────────────────────────────

  function processKey(nodePath, process) {
    return `${nodePath}|${process}`;
  }

  function outputKey(nodePath, topic) {
    return `${nodePath}|${topic}`;
  }

  function linkKey(topic, publisher, subscriber) {
    return `${topic}|${publisher || "*"}|${subscriber || "*"}`;
  }

  function responseKey(nodePath, outTopic, inTopic) {
    return `${nodePath}|${outTopic}|${inTopic}`;
  }

  function checkRecord(record, index, kind, required) {
    if (!record || typeof record !== "object") {
      throw new Error(`${kind}[${index}] is not an object`);
    }
    required.forEach((field) => {
      if (record[field] === undefined || record[field] === null) {
        throw new Error(`${kind}[${index}] lacks ${field}`);
      }
    });
    if (
      record.min_ms !== undefined &&
      record.max_ms !== undefined &&
      record.min_ms > record.max_ms
    ) {
      throw new Error(`${kind}[${index}] has min_ms above max_ms`);
    }
  }

  // ── Parsing ─────────────────────────────────────────────────────────────────

  // latency/1: processes[] keyed by node path and process name.
  function parseV1(json, measurement) {
    (json.processes || []).forEach((record, index) => {
      checkRecord(record, index, "processes", [
        "node_path",
        "process",
        "min_ms",
        "max_ms",
      ]);
      measurement.processes.set(
        processKey(record.node_path, record.process),
        record,
      );
    });
    (json.links || []).forEach((record, index) => {
      checkRecord(record, index, "links", ["topic", "min_ms", "max_ms"]);
      measurement.links.set(
        linkKey(record.topic, record.publisher, record.subscriber),
        record,
      );
    });
  }

  function isRate(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  // latency/2: nodes[] with per-output exec, links[] with intra-process
  // markers, chains[] measured end to end, declared_diff per node; every
  // output, input and timer carries the rate the run observed.
  function parseV2(json, measurement) {
    (json.nodes || []).forEach((node, index) => {
      checkRecord(node, index, "nodes", ["node_path"]);
      measurement.nodes.set(node.node_path, node);
      // An intra-process input the tracer saw no take on has no observed rate.
      (node.inputs || []).forEach((input) => {
        if (input?.intra_process && !input.count) return;
        if (input?.topic && isRate(input.rate_hz)) {
          measurement.inputRates.set(
            outputKey(node.node_path, input.topic),
            input.rate_hz,
          );
        }
      });
      measurement.timers.set(
        node.node_path,
        (node.timers || []).filter((timer) => isRate(timer?.rate_hz)),
      );
      (node.outputs || []).forEach((output, outIndex) => {
        checkRecord(output, outIndex, `nodes[${index}].outputs`, ["topic"]);
        if (isRate(output.rate_hz)) {
          measurement.outputRates.set(
            outputKey(node.node_path, output.topic),
            output.rate_hz,
          );
        }
        if (output.exec) {
          checkRecord(output.exec, outIndex, `nodes[${index}].outputs.exec`, [
            "min_ms",
            "max_ms",
          ]);
          measurement.outputs.set(outputKey(node.node_path, output.topic), {
            ...output.exec,
            trigger: output.trigger || null,
          });
        }
        (output.response || []).forEach((response, resIndex) => {
          checkRecord(response, resIndex, `nodes[${index}].outputs.response`, [
            "from",
            "min_ms",
            "max_ms",
          ]);
          measurement.responses.set(
            responseKey(node.node_path, output.topic, response.from),
            response,
          );
        });
      });
      if (node.declared_diff?.length) {
        measurement.diffs.set(node.node_path, node.declared_diff);
      }
    });
    (json.links || []).forEach((record, index) => {
      checkRecord(record, index, "links", ["topic"]);
      const key = linkKey(record.topic, record.publisher, record.subscriber);
      if (record.intra_process) {
        measurement.intra.add(key);
        return;
      }
      checkRecord(record, index, "links", ["min_ms", "max_ms"]);
      measurement.links.set(key, record);
    });
    (json.chains || []).forEach((record, index) => {
      checkRecord(record, index, "chains", ["from", "to", "min_ms", "max_ms"]);
      measurement.chains.push(record);
    });
    (json.unobserved_nodes || []).forEach((path) =>
      measurement.unobserved.add(path),
    );
  }

  // A parsed file: records indexed for lookup, plus what the toolbar reports.
  function fromJson(json, label = "measurement") {
    if (!json || typeof json !== "object") {
      throw new Error("measurement is not a JSON object");
    }
    if (!SCHEMAS.has(json.schema)) {
      throw new Error(`unknown measurement schema ${json.schema ?? "(none)"}`);
    }
    const measurement = {
      schema: json.schema,
      label,
      mode: json.mode ?? null,
      run: json.run ?? null,
      processes: new Map(),
      outputs: new Map(),
      responses: new Map(),
      links: new Map(),
      intra: new Set(),
      unobserved: new Set(),
      chains: [],
      diffs: new Map(),
      nodes: new Map(),
      inputRates: new Map(),
      outputRates: new Map(),
      timers: new Map(),
      matched: null,
      costs(graph) {
        return costsFor(this, graph);
      },
      chainFor(fromNode, toNode, toTopic) {
        return chainFor(this, fromNode, toNode, toTopic);
      },
      diffFor(nodePath, topics) {
        return diffFor(this, nodePath, topics);
      },
      nodeRecord(nodePath) {
        return this.nodes.get(nodePath) || null;
      },
      hopInfo(graph, edge, from, to) {
        return hopInfo(this, graph, edge, from, to);
      },
      triggerOf(graph, event) {
        return triggerOf(this, graph, event);
      },
      deadReason(graph, event) {
        return deadReason(this, graph, event);
      },
      rateOf(graph, event) {
        return rateOf(this, graph, event);
      },
    };
    if (json.schema === SCHEMA_V1) parseV1(json, measurement);
    else parseV2(json, measurement);
    return measurement;
  }

  // ── Graph helpers ───────────────────────────────────────────────────────────

  function topicOf(event) {
    const topic = event?.port?.topic;
    if (Array.isArray(topic) && topic.length) return `/${topic.join("/")}`;
    if (typeof topic === "string" && topic) return topic;
    return null;
  }

  // Output events of the gate's node that the gate feeds, directly or through
  // further process gates of the same node.
  function outputsOf(graph, event, depth = 2) {
    const found = [];
    const seen = new Set([event.id]);
    const walk = (id, left) => {
      (graph.succ.get(id) || []).forEach((nextId) => {
        if (seen.has(nextId)) return;
        seen.add(nextId);
        const next = graph.events.get(nextId);
        if (!next || next.ownerId !== event.ownerId) return;
        if (next.kind === "output") found.push(next);
        else if (next.kind === "process" && left > 1) walk(nextId, left - 1);
      });
    };
    walk(event.id, depth);
    return found;
  }

  function outputTopicsOf(graph, event) {
    return outputsOf(graph, event)
      .map(topicOf)
      .filter((topic) => topic);
  }

  // ── Cost provider ───────────────────────────────────────────────────────────

  // The output record a process gate is measured through: the first output it
  // feeds that the run published (ports are trusted, process names are not).
  function outputRecord(measurement, graph, event) {
    if (event.kind !== "process") return null;
    const owner = graph.ownerOf(event.id);
    if (!owner?.path) return null;
    if (measurement.schema === SCHEMA_V1) {
      const key = processKey(owner.path, event.name);
      return measurement.processes.has(key)
        ? { key, topic: null, record: measurement.processes.get(key) }
        : null;
    }
    for (const output of outputsOf(graph, event)) {
      const topic = topicOf(output);
      if (!topic) continue;
      const key = outputKey(owner.path, topic);
      if (measurement.outputs.has(key)) {
        return { key, topic, record: measurement.outputs.get(key) };
      }
    }
    return null;
  }

  // The node's measured in→out time from an input topic to the output a gate
  // feeds: the response row of that output.
  function responseRecord(measurement, graph, input, gate) {
    if (measurement.schema === SCHEMA_V1) return null;
    const owner = graph.ownerOf(gate.id);
    const inTopic = topicOf(input);
    if (!owner?.path || !inTopic) return null;
    for (const output of outputsOf(graph, gate)) {
      const outTopic = topicOf(output);
      if (!outTopic) continue;
      const record = measurement.responses.get(
        responseKey(owner.path, outTopic, inTopic),
      );
      if (record) return { record, outTopic, inTopic };
    }
    return null;
  }

  // What a link record says about an output→input edge, if anything.
  function linkRecord(measurement, graph, from, to) {
    const topic = topicOf(to) || topicOf(from);
    if (!topic) return null;
    const publisher = graph.ownerOf(from.id)?.path;
    const subscriber = graph.ownerOf(to.id)?.path;
    const candidates = [
      linkKey(topic, publisher, subscriber),
      linkKey(topic, null, subscriber),
      linkKey(topic, publisher, null),
      linkKey(topic, null, null),
    ];
    const intra = candidates.find((k) => measurement.intra.has(k));
    if (intra) return { key: intra, intra: true, record: null };
    const key = candidates.find((k) => measurement.links.has(k));
    return key
      ? { key, intra: false, record: measurement.links.get(key) }
      : null;
  }

  // Why a process gate never ran in the recorded run, or null: its node was
  // never observed, its process ended inside the window or never got past
  // construction, or every output the gate feeds was declared and never
  // published. Read from the node records, so a gate the file has no exec for
  // stays merely unmeasured.
  function deadReason(measurement, graph, event) {
    if (event.kind !== "process" || measurement.schema === SCHEMA_V1) {
      return null;
    }
    const owner = graph.ownerOf(event.id);
    const path = owner?.path;
    if (!path) return null;
    if (measurement.unobserved.has(path)) return "node not observed in the run";
    const process = measurement.nodes.get(path)?.process;
    if (process?.state === "exited") {
      const code = process.exit?.code;
      const at = process.exit?.at ? ` at ${process.exit.at}` : "";
      return `process exited${code === undefined || code === null ? "" : ` (code ${code})`}${at}`;
    }
    if (process?.state === "not_initialized") {
      return "node never left construction: no endpoint of its own";
    }
    const topics = outputTopicsOf(graph, event);
    if (!topics.length) return null;
    const rows = measurement.diffs.get(path);
    if (!rows) return null;
    const status = new Map(rows.map((row) => [row.output, row.status]));
    return topics.every((topic) => status.get(topic) === "unobserved")
      ? `output never published (${topics.join(", ")})`
      : null;
  }

  // exec answers a process gate from the exec of the output it feeds, or the
  // dead marker when the record says the gate never ran. comm
  // answers an output→input edge from links[] (a record may leave publisher or
  // subscriber open; an intra-process link costs nothing of its own) and an
  // input→gate edge from the node's response: the time from that input's
  // arrival to the publish, less the run itself, is the wait the message
  // spent before the gate ran. wait is nothing at a gate the run observed:
  // its sampling sits on the input edges and a timer's phase is not part of
  // a measured chain.
  function costsFor(measurement, graph) {
    const matched = { processes: new Set(), links: new Set() };
    const exec = (event) => {
      const reason = deadReason(measurement, graph, event);
      if (reason) return T.dead(reason);
      const hit = outputRecord(measurement, graph, event);
      if (!hit) return null;
      matched.processes.add(hit.key);
      return T.fromRecord(hit.record, "measured");
    };
    const wait = (event) =>
      outputRecord(measurement, graph, event) ||
      deadReason(measurement, graph, event)
        ? T.summary()
        : null;
    const comm = (edge, from, to) => {
      if (from.kind === "output" && to.kind === "input") {
        const hit = linkRecord(measurement, graph, from, to);
        if (!hit) return null;
        matched.links.add(hit.key);
        return hit.intra
          ? T.summary({ source: "intra_process" })
          : T.fromRecord(hit.record, "measured");
      }
      if (from.kind === "input" && to.kind === "process") {
        const response = responseRecord(measurement, graph, from, to);
        const run = outputRecord(measurement, graph, to);
        if (!response || !run) return null;
        return T.sampling(
          T.fromRecord(response.record, "measured"),
          T.fromRecord(run.record, "measured"),
        );
      }
      return null;
    };

    // Coverage is counted once over the whole graph so the toolbar can say how
    // much of the design the file speaks for.
    graph.events.forEach((event) => exec(event));
    graph.edgeList.forEach((edge) =>
      comm(edge, graph.events.get(edge.from), graph.events.get(edge.to)),
    );
    const v1 = measurement.schema === SCHEMA_V1;
    measurement.matched = {
      processes: matched.processes.size,
      processesTotal: v1
        ? measurement.processes.size
        : measurement.outputs.size,
      links: matched.links.size,
      linksTotal: measurement.links.size + measurement.intra.size,
    };
    const unit = v1 ? "processes" : "outputs";
    measurement.label =
      `${measurement.label.replace(/ \(.*\)$/, "")} ` +
      `(${measurement.matched.processes}/${measurement.matched.processesTotal} ${unit}, ` +
      `${measurement.matched.links}/${measurement.matched.linksTotal} links matched` +
      `${clockNote(measurement.run)})`;
    return { exec, wait, comm };
  }

  // The records behind one edge of a hop: the link it rode, or the response
  // and run its wait was taken from.
  function hopInfo(measurement, graph, edge, from, to) {
    if (from.kind === "output" && to.kind === "input") {
      const hit = linkRecord(measurement, graph, from, to);
      if (!hit) return null;
      return hit.intra ? { intra: true } : { link: hit.record };
    }
    if (from.kind === "input" && to.kind === "process") {
      const response = responseRecord(measurement, graph, from, to);
      if (!response) return null;
      const run = outputRecord(measurement, graph, to);
      return {
        response: response.record,
        run: run?.record ?? null,
        outTopic: response.outTopic,
      };
    }
    return null;
  }

  // The rate the run observed for an event, or null: a port from its topic's
  // take or publish count, a periodic gate from the timer nearest its declared
  // period, any other process gate from the first output it feeds. The design
  // propagates a declared rate through the graph; this is what actually ran.
  function rateOf(measurement, graph, event) {
    if (measurement.schema === SCHEMA_V1 || !event) return null;
    const owner = graph.ownerOf(event.id);
    const path = owner?.path;
    if (!path) return null;
    const hit = (rate_hz, via) => (isRate(rate_hz) ? { rate_hz, via } : null);
    if (event.kind === "input") {
      const topic = topicOf(event);
      return topic
        ? hit(measurement.inputRates.get(outputKey(path, topic)), "input")
        : null;
    }
    if (event.kind === "output") {
      const topic = topicOf(event);
      return topic
        ? hit(measurement.outputRates.get(outputKey(path, topic)), "output")
        : null;
    }
    if (event.kind !== "process") return null;
    if (event.type === "periodic" && event.frequency > 0) {
      const period = 1000 / event.frequency;
      const timer = (measurement.timers.get(path) || [])
        .filter((t) => isRate(t.period_ms))
        .sort(
          (a, b) =>
            Math.abs(a.period_ms - period) - Math.abs(b.period_ms - period),
        )[0];
      if (timer) return hit(timer.rate_hz, `timer ${timer.period_ms} ms`);
    }
    for (const output of outputsOf(graph, event)) {
      const topic = topicOf(output);
      const rate = topic && measurement.outputRates.get(outputKey(path, topic));
      if (isRate(rate)) return hit(rate, `output ${topic}`);
    }
    return null;
  }

  // The trigger the run detected for the output a gate feeds.
  function triggerOf(measurement, graph, event) {
    const hit = outputRecord(measurement, graph, event);
    return hit?.record?.trigger ?? null;
  }

  // ── Measured chains and the declared diff ───────────────────────────────────

  // The measured end-to-end record from a timer of `fromNode` to `toNode`,
  // to `toTopic` when given, else the deepest terminal chain into that node.
  function chainFor(measurement, fromNode, toNode, toTopic = null) {
    if (!fromNode || !toNode) return null;
    const candidates = measurement.chains.filter(
      (chain) =>
        chain.from.startsWith(`${fromNode}:timer:`) &&
        (toTopic
          ? chain.to === `${toNode}:${toTopic}`
          : chain.to.startsWith(`${toNode}:`)),
    );
    if (!candidates.length) return null;
    candidates.sort(
      (a, b) =>
        Number(Boolean(b.terminal)) - Number(Boolean(a.terminal)) ||
        (b.hops ?? 0) - (a.hops ?? 0),
    );
    const record = candidates[0];
    return {
      record,
      summary: T.fromRecord(record, "measured"),
      hops: record.hops ?? null,
      from: record.from,
      to: record.to,
    };
  }

  function diffFor(measurement, nodePath, topics = null) {
    const rows = measurement.diffs.get(nodePath) || [];
    if (!topics) return rows;
    const wanted = new Set(topics);
    return rows.filter((row) => wanted.has(row.output));
  }

  // ── Loaders ─────────────────────────────────────────────────────────────────

  // The latency file is one object, either bare JSON or the script the bundle
  // serves: `window.latencyData["<mode>"] = {...};`, which a page opened from
  // file:// can load where it cannot fetch.
  const SCRIPT_GLOBAL = "latencyData";
  const SCRIPT_ASSIGN = new RegExp(
    `window\\.${SCRIPT_GLOBAL}\\[("(?:[^"\\\\]|\\\\.)*")\\]\\s*=\\s*`,
  );

  function parseText(text) {
    const trimmed = text.trim();
    const assign = SCRIPT_ASSIGN.exec(trimmed);
    if (!assign) return JSON.parse(trimmed);
    const body = trimmed.slice(assign.index + assign[0].length);
    return JSON.parse(body.replace(/;\s*$/, ""));
  }

  async function fromFile(file) {
    return fromJson(parseText(await file.text()), file.name);
  }

  // The bundle may ship data/<mode>_latency.js beside the design data; its
  // absence is the normal case.
  function loadBundled(mode) {
    const doc = typeof document !== "undefined" ? document : null;
    if (!doc?.head) return Promise.resolve(null);
    const label = `${mode}_latency.js`;
    const known = window[SCRIPT_GLOBAL]?.[mode];
    if (known) return Promise.resolve(fromJson(known, label));
    return new Promise((resolve) => {
      const script = doc.createElement("script");
      script.src = `data/${label}`;
      script.onload = () => {
        const data = window[SCRIPT_GLOBAL]?.[mode];
        resolve(data ? fromJson(data, label) : null);
      };
      script.onerror = () => resolve(null);
      doc.head.appendChild(script);
    });
  }

  // How the run counted time; only a ROS-time run says so.
  function clockNote(run) {
    const clock = run?.clock;
    if (!clock || clock.base !== "ros") return "";
    const rate =
      typeof clock.rate === "number"
        ? ` ×${Number(clock.rate.toFixed(3))}`
        : "";
    return `, ROS time${rate}`;
  }

  const LatencySource = {
    SCHEMA,
    SCHEMA_V1,
    fromJson,
    fromFile,
    parseText,
    loadBundled,
    clockNote,
    outputTopicsOf,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = LatencySource;
  } else {
    window.LatencySource = LatencySource;
  }
})();
