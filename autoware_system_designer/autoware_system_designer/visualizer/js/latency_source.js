// Latency Source Module
// Loads a measurement file and answers the timing model's cost queries from
// it. Records are keyed by node path and topic (or process name in the first
// file version), never by unique_id: ids are name hashes and change whenever
// the design is edited. The runtime writes latency/2; latency/1 stays readable.
// A latency/2 file also yields the recorded graph: the event graph of what the
// run showed, at node unit, with every cost measured.

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
      recordedGraph(designGraph) {
        return recordedGraph(this, designGraph);
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
      `${measurement.matched.links}/${measurement.matched.linksTotal} links matched` +
      `${clockNote(measurement.run)})`;
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

  // ── Recorded graph ──────────────────────────────────────────────────────────

  // The event graph a latency/2 run showed, at node unit. Every timer of a node
  // is a clock root; every output is a process gate whose run is the output's
  // exec; the ports are the topics. A gate is fed by its detected trigger and
  // by every input its response table names; a link joins a publish to the
  // take it was matched with. Node instances come from the design where the
  // path is known, so colours and panels stay the design's.
  const R_TIMER_TYPE = "periodic";

  function recordedIds(path) {
    return {
      timer: (period) => `r:${path}|timer:${period}`,
      gate: (topic) => `r:${path}|run:${topic}`,
      pub: (topic) => `r:${path}|pub:${topic}`,
      sub: (topic) => `r:${path}|sub:${topic}`,
    };
  }

  function periodKey(period) {
    return period === null || period === undefined ? "?" : `${period}`;
  }

  // Sampling delay of a gate for a message it does not fire on: uniform over
  // the gate's own measured period.
  function samplingOf(rateHz) {
    if (!(rateHz > 0)) return T.summary({ source: "unmeasured" });
    return T.uniform(0, 1000 / rateHz);
  }

  function recordedGraph(measurement, designGraph) {
    if (measurement.schema !== SCHEMA) {
      throw new Error("the recorded graph needs a latency/2 file");
    }
    const edgeInfo = new Map(); // "fromId>toId" → what the edge stands for
    const execOf = new Map(); // gate id → exec record
    const triggerOf = new Map(); // gate id → trigger record
    const publishers = new Map(); // topic → [node path]
    const synthesized = new Map(); // node path → synthesized instance fields

    measurement.nodes.forEach((node, path) => {
      (node.outputs || []).forEach((output) => {
        if (!publishers.has(output.topic)) publishers.set(output.topic, []);
        publishers.get(output.topic).push(path);
      });
    });

    const edgeKey = (from, to) => `${from}>${to}`;
    const addEdge = (from, to, info) => {
      const key = edgeKey(from, to);
      if (!edgeInfo.has(key)) edgeInfo.set(key, { from, to, ...info });
      return edgeInfo.get(key);
    };

    measurement.nodes.forEach((node, path) => {
      const ids = recordedIds(path);
      const timers = new Map(); // period key → timer event
      const subs = new Map(); // topic → input event
      const gates = [];
      const pubs = [];

      const sub = (topic) => {
        if (!subs.has(topic)) {
          subs.set(topic, {
            unique_id: ids.sub(topic),
            name: topic,
            type: "on_input",
            trigger_ids: [],
            action_ids: [],
          });
        }
        return subs.get(topic);
      };
      (node.inputs || []).forEach((input) => sub(input.topic));

      (node.timers || []).forEach((timer) => {
        const key = periodKey(timer.period_ms);
        if (!timers.has(key)) {
          timers.set(key, {
            unique_id: ids.timer(key),
            name: `timer ${key} ms`,
            type: R_TIMER_TYPE,
            frequency: timer.rate_hz ?? null,
            period_ms: timer.period_ms ?? null,
            trigger_ids: [],
            action_ids: [],
          });
        } else if ((timer.rate_hz ?? 0) > (timers.get(key).frequency ?? 0)) {
          timers.get(key).frequency = timer.rate_hz;
        }
      });

      (node.outputs || []).forEach((output) => {
        const trigger = output.trigger || { kind: "unknown" };
        const gateId = ids.gate(output.topic);
        const gate = {
          unique_id: gateId,
          name: output.topic,
          type: trigger.kind === "unknown" ? null : trigger.kind,
          frequency: output.rate_hz ?? null,
          trigger_ids: [],
          action_ids: [ids.pub(output.topic)],
        };
        if (output.exec) execOf.set(gateId, output.exec);
        triggerOf.set(gateId, { ...trigger, share: trigger.share ?? null });

        // The gate's own period sets the sampling delay of messages it does
        // not fire on: its timer's under a timer, its output rate otherwise.
        let sampleRate = output.rate_hz ?? null;
        if (trigger.kind === "timer") {
          const timer = timers.get(periodKey(trigger.period_ms));
          if (timer) {
            timer.action_ids.push(gateId);
            gate.trigger_ids.push(timer.unique_id);
            addEdge(timer.unique_id, gateId, { kind: "timer" });
            sampleRate = timer.frequency ?? sampleRate;
          }
        } else if (trigger.kind === "input" && trigger.topic) {
          const input = sub(trigger.topic);
          input.action_ids.push(gateId);
          gate.trigger_ids.push(input.unique_id);
          addEdge(input.unique_id, gateId, {
            kind: "trigger",
            share: trigger.share ?? null,
            intra: Boolean(trigger.intra_process),
          });
        }
        (output.response || []).forEach((response) => {
          const input = sub(response.from);
          const key = edgeKey(input.unique_id, gateId);
          if (!edgeInfo.has(key)) {
            input.action_ids.push(gateId);
            gate.trigger_ids.push(input.unique_id);
            addEdge(input.unique_id, gateId, {
              kind: "sampled",
              rate: sampleRate,
            });
          }
          edgeInfo.get(key).response = response;
        });
        gates.push(gate);
        pubs.push({
          name: output.topic,
          topic: output.topic,
          event: {
            unique_id: ids.pub(output.topic),
            name: output.topic,
            type: "to_output",
            trigger_ids: [gateId],
            action_ids: [],
          },
        });
      });

      synthesized.set(path, {
        events: [...timers.values(), ...gates],
        out_ports: pubs,
        subs,
      });
    });

    // Links: a publish matched to a take. A record without a publisher names
    // the topic's only recorded publisher, else it stays unattached.
    const linkEdge = (topic, publisher, subscriber, record) => {
      const from = publisher ?? single(publishers.get(topic));
      if (!from || !synthesized.has(from) || !synthesized.has(subscriber))
        return;
      const pub = synthesized
        .get(from)
        .out_ports.find((port) => port.topic === topic);
      if (!pub) return;
      const input = synthesized.get(subscriber).subs.get(topic);
      if (!input) return;
      if (!pub.event.action_ids.includes(input.unique_id)) {
        pub.event.action_ids.push(input.unique_id);
        input.trigger_ids.push(pub.event.unique_id);
      }
      addEdge(pub.event.unique_id, input.unique_id, {
        kind: record.intra_process ? "intra" : "link",
        link: record.intra_process ? null : record,
      });
    };
    measurement.links.forEach((record) =>
      linkEdge(record.topic, record.publisher, record.subscriber, record),
    );
    measurement.intra.forEach((key) => {
      const [topic, publisher, subscriber] = key.split("|");
      linkEdge(
        topic,
        publisher === "*" ? null : publisher,
        subscriber === "*" ? null : subscriber,
        { intra_process: true },
      );
    });

    // The design's instance tree carries the nodes' guides and panels; nodes
    // the design does not place are appended under the root.
    const placed = new Set();
    const instanceOf = (path, base) => {
      const fields = synthesized.get(path);
      placed.add(path);
      return {
        ...base,
        in_ports: [...fields.subs.values()].map((event) => ({
          name: event.name,
          topic: event.name,
          event,
        })),
        out_ports: fields.out_ports,
        events: fields.events,
        children: [],
      };
    };
    const visit = (instance) => {
      if (instance.path && synthesized.has(instance.path)) {
        return instanceOf(instance.path, instance);
      }
      return {
        ...instance,
        in_ports: [],
        out_ports: [],
        events: [],
        children: (instance.children || []).map(visit),
      };
    };
    const designRoot = designGraph.instances.get(
      [...designGraph.instances.keys()][0],
    )?.data;
    const root = designRoot
      ? visit(designRoot)
      : { unique_id: "r:root", name: "recorded", path: "/", children: [] };
    synthesized.forEach((fields, path) => {
      if (placed.has(path)) return;
      root.children.push(
        instanceOf(path, {
          unique_id: `r:node:${path}`,
          name: path.split("/").filter(Boolean).pop() || path,
          path,
          entity_type: "node",
        }),
      );
    });

    const graph = new designGraph.constructor().build(root);

    const info = (fromId, toId) => edgeInfo.get(edgeKey(fromId, toId)) || null;
    const costs = {
      wait: () => T.ZERO,
      exec(event) {
        if (event.kind !== "process" || event.type === R_TIMER_TYPE) {
          return T.ZERO;
        }
        const record = execOf.get(event.id);
        return record ? T.fromRecord(record, "measured") : T.UNMEASURED;
      },
      // A link costs what was measured; a message a gate does not fire on
      // waits for the gate's next run; a trigger costs nothing of its own.
      comm(edge, from, to) {
        const hit = info(from.id, to.id);
        if (!hit) return T.ZERO;
        if (hit.kind === "link") return T.fromRecord(hit.link, "measured");
        if (hit.kind === "intra") return T.summary({ source: "intra_process" });
        if (hit.kind === "sampled") return samplingOf(hit.rate);
        return T.ZERO;
      },
    };

    const counts = {
      timers: [...graph.events.values()].filter(
        (event) => event.kind === "process" && event.type === R_TIMER_TYPE,
      ).length,
      outputs: execOf.size,
      links: [...edgeInfo.values()].filter(
        (edge) => edge.kind === "link" || edge.kind === "intra",
      ).length,
      sampled: [...edgeInfo.values()].filter((edge) => edge.kind === "sampled")
        .length,
    };
    return {
      graph,
      costs,
      edgeInfo: info,
      triggerOf: (gateId) => triggerOf.get(gateId) || null,
      sampling: samplingOf,
      counts,
      label:
        `${counts.timers} timers, ${counts.outputs} outputs, ` +
        `${counts.links} links, ${counts.sampled} sampled inputs` +
        clockNote(measurement.run),
    };
  }

  function single(list) {
    return list && list.length === 1 ? list[0] : null;
  }

  // ── Loaders ─────────────────────────────────────────────────────────────────

  async function fromFile(file) {
    const text = await file.text();
    return fromJson(JSON.parse(text), file.name);
  }

  // The bundle may ship data/<mode>_latency.json beside the design data; its
  // absence is the normal case. A page opened from file:// cannot fetch JSON,
  // so the file's script twin (data/<mode>_latency.js, assigning
  // window.latencyData[mode]) is loaded there, and wherever the fetch fails.
  const SCRIPT_GLOBAL = "latencyData";

  async function loadBundled(mode) {
    const label = `${mode}_latency.json`;
    const fetched = await fetchBundled(mode);
    if (fetched) return fromJson(fetched, label);
    const scripted = await loadBundledScript(mode);
    return scripted ? fromJson(scripted, label) : null;
  }

  async function fetchBundled(mode) {
    if (typeof fetch !== "function" || window.location?.protocol === "file:") {
      return null;
    }
    const url = `data/${mode}_latency.json`;
    try {
      const response = await fetch(url, { cache: "no-store" });
      return response.ok ? await response.json() : null;
    } catch (error) {
      console.warn(`Latency file ${url} not fetched:`, error.message);
      return null;
    }
  }

  function loadBundledScript(mode) {
    const doc = typeof document !== "undefined" ? document : null;
    if (!doc?.head) return Promise.resolve(null);
    const known = window[SCRIPT_GLOBAL]?.[mode];
    if (known) return Promise.resolve(known);
    return new Promise((resolve) => {
      const script = doc.createElement("script");
      script.src = `data/${mode}_latency.js`;
      script.onload = () => resolve(window[SCRIPT_GLOBAL]?.[mode] ?? null);
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
    loadBundled,
    clockNote,
    outputTopicsOf,
    recordedGraph,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = LatencySource;
  } else {
    window.LatencySource = LatencySource;
  }
})();
