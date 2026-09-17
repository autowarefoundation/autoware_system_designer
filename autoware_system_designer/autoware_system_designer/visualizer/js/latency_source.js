// Latency Source Module
// Loads a measurement file and answers the timing model's cost queries from
// it. Records are keyed by node path and process or topic, never by unique_id:
// ids are name hashes and change whenever the design is edited. One converter
// per producer; only the designer's own file shape is implemented, CARET's raw
// output is a reserved seam.

(function () {
  const SCHEMA = "autoware_system_designer/latency/1";
  const T = window.TimingModel;

  // ── Converters, one per `source` ────────────────────────────────────────────

  // The designer's own shape: already { processes: [...], links: [...] }.
  function fromDesigner(json) {
    return json;
  }

  // CARET is the measurement path: the designer emits the CARET architecture
  // and target paths (builder/export/caret_export.py), CARET measures, and its
  // callback and communication latencies come back through here as
  // processes[] and links[]. Not implemented; a CARET result is expected to be
  // exported in the designer's shape until it is.
  function fromCaret() {
    throw new Error(
      "CARET result conversion is not implemented; export the run as " +
        `${SCHEMA} with source: "caret"`,
    );
  }

  const CONVERTERS = { designer: fromDesigner, caret: fromCaret };

  // ── Parsing ─────────────────────────────────────────────────────────────────

  function processKey(nodePath, process) {
    return `${nodePath}|${process}`;
  }

  function linkKey(topic, publisher, subscriber) {
    return `${topic}|${publisher || "*"}|${subscriber || "*"}`;
  }

  function checkRecord(record, index, kind, required) {
    required.forEach((field) => {
      if (record[field] === undefined || record[field] === null) {
        throw new Error(`${kind}[${index}] lacks ${field}`);
      }
    });
    if (record.min_ms > record.max_ms) {
      throw new Error(`${kind}[${index}] has min_ms above max_ms`);
    }
  }

  // A parsed file: records indexed for lookup, plus what the toolbar reports.
  function fromJson(json, label = "measurement") {
    if (!json || typeof json !== "object") {
      throw new Error("measurement is not a JSON object");
    }
    if (json.schema !== SCHEMA) {
      // A file without the designer's schema tag came from a producer whose
      // converter has to shape it first.
      const converter = CONVERTERS[json.source];
      if (!converter || converter === fromDesigner) {
        throw new Error(`unknown measurement schema ${json.schema ?? "(none)"}`);
      }
      json = converter(json);
    }

    const processes = new Map();
    (json.processes || []).forEach((record, index) => {
      checkRecord(record, index, "processes", ["node_path", "process", "min_ms", "max_ms"]);
      processes.set(processKey(record.node_path, record.process), record);
    });
    const links = new Map();
    (json.links || []).forEach((record, index) => {
      checkRecord(record, index, "links", ["topic", "min_ms", "max_ms"]);
      links.set(linkKey(record.topic, record.publisher, record.subscriber), record);
    });

    return {
      schema: SCHEMA,
      label,
      mode: json.mode ?? null,
      source: json.source ?? "designer",
      processes,
      links,
      matched: null,
      costs(graph) {
        return costsFor(this, graph);
      },
    };
  }

  // ── Cost provider ───────────────────────────────────────────────────────────

  function topicOf(event) {
    const topic = event.port?.topic;
    if (Array.isArray(topic) && topic.length) return `/${topic.join("/")}`;
    if (typeof topic === "string" && topic) return topic;
    return null;
  }

  // exec answers a process gate from processes[], comm an output→input edge
  // from links[]; a link record may leave publisher or subscriber open.
  function costsFor(measurement, graph) {
    const matched = { processes: new Set(), links: new Set() };
    const exec = (event) => {
      if (event.kind !== "process") return null;
      const owner = graph.ownerOf(event.id);
      if (!owner?.path) return null;
      const key = processKey(owner.path, event.name);
      const record = measurement.processes.get(key);
      if (!record) return null;
      matched.processes.add(key);
      return T.fromRecord(record, "measured");
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
    measurement.matched = {
      processes: matched.processes.size,
      processesTotal: measurement.processes.size,
      links: matched.links.size,
      linksTotal: measurement.links.size,
    };
    measurement.label =
      `${measurement.label.replace(/ \(.*\)$/, "")} ` +
      `(${measurement.matched.processes}/${measurement.matched.processesTotal} processes, ` +
      `${measurement.matched.links}/${measurement.matched.linksTotal} links matched)`;
    return { exec, comm };
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

  const LatencySource = { SCHEMA, CONVERTERS, fromJson, fromFile, loadBundled };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = LatencySource;
  } else {
    window.LatencySource = LatencySource;
  }
})();
