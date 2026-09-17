// Latency Source Module
// Loads a measurement file and answers the timing model's cost queries from
// it. Records are keyed by node path and topic (or process name in the first
// file version), never by unique_id: ids are name hashes and change whenever
// the design is edited. The runtime writes latency/2; latency/1 stays readable.

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

  // latency/2: nodes[] with per-output exec, links[] with intra-process
  // markers, chains[] measured end to end, declared_diff per node.
  function parseV2(json, measurement) {
    (json.nodes || []).forEach((node, index) => {
      checkRecord(node, index, "nodes", ["node_path"]);
      measurement.nodes.set(node.node_path, node);
      (node.outputs || []).forEach((output, outIndex) => {
        checkRecord(output, outIndex, `nodes[${index}].outputs`, ["topic"]);
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
      links: new Map(),
      intra: new Set(),
      chains: [],
      diffs: new Map(),
      nodes: new Map(),
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

  // exec answers a process gate: by process name from latency/1, by the topic
  // of an output the gate feeds from latency/2 (ports are trusted, process
  // names are not). comm answers an output→input edge from links[]; a link
  // record may leave publisher or subscriber open, and an intra-process link
  // costs nothing of its own.
  function costsFor(measurement, graph) {
    const matched = { processes: new Set(), links: new Set() };
    const execRecord = (event) => {
      if (event.kind !== "process") return null;
      const owner = graph.ownerOf(event.id);
      if (!owner?.path) return null;
      if (measurement.schema === SCHEMA_V1) {
        const key = processKey(owner.path, event.name);
        return measurement.processes.has(key)
          ? { key, record: measurement.processes.get(key) }
          : null;
      }
      for (const output of outputsOf(graph, event)) {
        const topic = topicOf(output);
        if (!topic) continue;
        const key = outputKey(owner.path, topic);
        if (measurement.outputs.has(key)) {
          return { key, record: measurement.outputs.get(key) };
        }
      }
      return null;
    };
    const exec = (event) => {
      const hit = execRecord(event);
      if (!hit) return null;
      matched.processes.add(hit.key);
      return T.fromRecord(hit.record, "measured");
    };
    const comm = (edge, from, to) => {
      if (from.kind !== "output" || to.kind !== "input") return null;
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
      if (intra) {
        matched.links.add(intra);
        return T.summary({ source: "intra_process" });
      }
      const key = candidates.find((k) => measurement.links.has(k));
      if (!key) return null;
      matched.links.add(key);
      return T.fromRecord(measurement.links.get(key), "measured");
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
      `${measurement.matched.links}/${measurement.matched.linksTotal} links matched)`;
    return { exec, comm };
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

  async function fromFile(file) {
    const text = await file.text();
    return fromJson(JSON.parse(text), file.name);
  }

  // The bundle may ship data/<mode>_latency.json beside the design data; its
  // absence is the normal case.
  async function loadBundled(mode) {
    if (typeof fetch !== "function" || window.location.protocol === "file:") {
      return null;
    }
    const url = `data/${mode}_latency.json`;
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (!response.ok) return null;
      return fromJson(await response.json(), `${mode}_latency.json`);
    } catch (error) {
      console.warn(`Latency file ${url} not loaded:`, error.message);
      return null;
    }
  }

  const LatencySource = {
    SCHEMA,
    SCHEMA_V1,
    fromJson,
    fromFile,
    loadBundled,
    outputTopicsOf,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = LatencySource;
  } else {
    window.LatencySource = LatencySource;
  }
})();
