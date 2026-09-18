// Info Panel Module
// Renders the sidebar detail view for the element selected in a diagram.

(function () {
  // Keys carried by layout/rendering or shown in a dedicated card.
  const IGNORED_INFO_KEYS = new Set([
    "id",
    "children",
    "edges",
    "ports",
    "layoutOptions",
    "width",
    "height",
    "x",
    "y",
    "source",
    "target",
    "sections",
    "vis_guide",
    "source_file",
    "in_ports",
    "out_ports",
    "parameters",
    "topic",
    "global_topic",
    "event",
    "chain",
    "latency",
    "chains",
    "measurement",
  ]);

  // Parameter source -> badge label; the matching colors live in css/styles.css.
  const PARAM_TYPE_LABELS = {
    DEFAULT: "default",
    DEFAULT_FILE: "def-file",
    OVERRIDE: "override",
    OVERRIDE_FILE: "ovr-file",
    MODE: "mode",
    MODE_FILE: "mode-file",
    GLOBAL: "global",
  };

  const isEmpty = (value) =>
    !value ||
    (Array.isArray(value) && value.length === 0) ||
    (typeof value === "object" && Object.keys(value).length === 0);

  function element(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function card(title, ...children) {
    const box = element("div", "info-card");
    box.appendChild(element("div", "info-title", title));
    children.forEach((child) => box.appendChild(child));
    return box;
  }

  function scalarText(value) {
    if (typeof value === "object" && value !== null) {
      return Array.isArray(value) ? `[${value.length}]` : "{...}";
    }
    return String(value);
  }

  // ── Cards ───────────────────────────────────────────────────────────────────

  function topicCard(topic) {
    const row = element("div", "info-row");
    const value = Array.isArray(topic) ? "/" + topic.join("/") : topic;
    row.appendChild(element("span", "info-value info-value-full", value));
    return card("Topic", row);
  }

  function infoCard(data) {
    const rows = Object.entries(data)
      .filter(([key]) => !IGNORED_INFO_KEYS.has(key))
      .map(([key, value]) => {
        const row = element("div", "info-row");
        row.appendChild(element("span", "info-label", `${key}:`));
        row.appendChild(element("span", "info-value", scalarText(value)));
        return row;
      });
    return rows.length ? card("Info", ...rows) : null;
  }

  function portGroup(title, ports) {
    const group = element("div", "info-group");
    group.appendChild(element("div", "info-subtitle", title));
    ports.forEach((port) => {
      const entry = element("div", "port-entry");
      entry.appendChild(element("div", "port-name", port.name || "Unnamed"));
      const type = port.msg_type || port.type || "";
      if (type) entry.appendChild(element("div", "port-type", type));
      group.appendChild(entry);
    });
    return group;
  }

  // Ports reaching the same global topic; they carry no link of their own.
  function globalTopicCard(globalTopic) {
    const groups = [];
    const peerGroup = (title, peers) => {
      const group = element("div", "info-group");
      group.appendChild(element("div", "info-subtitle", title));
      peers.forEach((peer) => {
        const entry = element("div", "port-entry");
        entry.appendChild(element("div", "port-name", peer.name));
        if (peer.path)
          entry.appendChild(element("div", "port-type", peer.path));
        group.appendChild(entry);
      });
      return group;
    };

    if (globalTopic.publishers.length) {
      groups.push(peerGroup("Publishers", globalTopic.publishers));
    }
    if (globalTopic.subscribers.length) {
      groups.push(peerGroup("Subscribers", globalTopic.subscribers));
    }
    if (!groups.length) return null;
    return card(`Global Topic ${globalTopic.topic}`, ...groups);
  }

  // One event of the trigger graph: what fires it and at what rate.
  function eventCard(event) {
    const rows = [
      ["kind", event.kind],
      ["type", event.type],
      ["rate", event.rate],
      ["warn rate", event.warn_rate],
      ["error rate", event.error_rate],
      ["timeout", event.timeout],
    ]
      .filter(([, value]) => value !== null && value !== undefined)
      .map(([key, value]) => {
        const row = element("div", "info-row");
        row.appendChild(element("span", "info-label", `${key}:`));
        row.appendChild(element("span", "info-value", scalarText(value)));
        return row;
      });

    if (event.mismatch) {
      const row = element("div", "info-row");
      row.appendChild(element("span", "info-label", "mixed trigger rates:"));
      row.appendChild(
        element("span", "info-value chain-warn", event.mismatch.join(" / ")),
      );
      rows.push(row);
    }
    return rows.length ? card("Event", ...rows) : null;
  }

  function chainEntry(item) {
    const entry = element("div", "port-entry");
    const head = item.hops ? `+${item.hops}  ${item.name}` : item.name;
    entry.appendChild(element("div", "port-name", head));
    if (item.path) entry.appendChild(element("div", "port-type", item.path));
    entry.appendChild(
      element("div", "port-type", `${item.type} · ${item.rate}`),
    );
    return entry;
  }

  function chainGroup(title, items, total, limit) {
    const group = element("div", "info-group");
    group.appendChild(element("div", "info-subtitle", `${title} (${total})`));
    items.forEach((item) => group.appendChild(chainEntry(item)));
    if (total > limit) {
      group.appendChild(
        element("div", "port-type", `… ${total - limit} more not listed`),
      );
    }
    return group;
  }

  // Hop-ordered walk of the trigger graph in both directions from one event.
  // A report that is not about a single event passes no clocks.
  function chainCard(chain) {
    const groups = [];

    if (chain.clocks) {
      const clockGroup = element("div", "info-group");
      clockGroup.appendChild(element("div", "info-subtitle", "Driven by"));
      if (chain.clocks.length) {
        chain.clocks.forEach((clock) =>
          clockGroup.appendChild(
            chainEntry({
              name: clock.name,
              path: clock.path,
              type: "clock",
              rate: clock.rate,
              hops: 0,
            }),
          ),
        );
      } else {
        clockGroup.appendChild(
          element("div", "port-type chain-warn", "no clock reaches this event"),
        );
      }
      groups.push(clockGroup);
    }

    if (chain.upstream_total) {
      groups.push(
        chainGroup(
          chain.upstream_label || "Causes",
          chain.upstream,
          chain.upstream_total,
          chain.limit,
        ),
      );
    }
    if (chain.downstream_total) {
      groups.push(
        chainGroup(
          chain.downstream_label || "Effects",
          chain.downstream,
          chain.downstream_total,
          chain.limit,
        ),
      );
    }
    return card(chain.title || "Chain", ...groups);
  }

  // Costs of one hop or gate as min / mean ± sd / max, each row naming where
  // its numbers came from, then the branches a gate folded.
  function latencyCard(latency) {
    const children = [];
    if (latency.state === "logical") {
      children.push(
        element(
          "div",
          "port-type",
          `logical view · rank ${latency.rank ?? "—"}`,
        ),
      );
    }
    latency.rows.forEach((row) => {
      const line = element("div", "latency-row");
      line.appendChild(element("span", "latency-label", row.label));
      const value = element("span", "latency-value", row.value);
      if (row.source && row.source !== "none") {
        const source = element("span", "latency-source", row.source);
        if (row.count) source.textContent += ` n=${row.count}`;
        value.appendChild(source);
      }
      line.appendChild(value);
      children.push(line);
    });
    if (latency.arrives) {
      const line = element("div", "latency-row");
      line.appendChild(element("span", "latency-label", "arrives"));
      line.appendChild(element("span", "latency-value", latency.arrives));
      children.push(line);
    }
    if (latency.on?.length) {
      children.push(
        element("div", "port-type", `on the ${latency.on.join(", ")} chain`),
      );
    }
    if (latency.unknownType) {
      children.push(
        element(
          "div",
          "port-type latency-flag",
          "type not declared — folded as or",
        ),
      );
    }
    if (latency.diff?.length) children.push(diffGroup(latency.diff));
    if (latency.branches?.length > 1) {
      const group = element("div", "info-group");
      group.appendChild(
        element(
          "div",
          "info-subtitle",
          `${latency.fold === "max" ? "and — every branch" : "or — first branch"} (${latency.branches.length})`,
        ),
      );
      latency.branches.forEach((branch) => {
        const entry = element("div", "port-entry");
        const head = branch.via.length
          ? `${branch.name}  ← ${branch.via.join("/")}`
          : branch.name;
        entry.appendChild(element("div", "port-name", head));
        entry.appendChild(element("div", "port-type", branch.value));
        group.appendChild(entry);
      });
      children.push(group);
    }
    return card("Latency", ...children);
  }

  // Declared trigger of each output beside what the measurement observed.
  function diffGroup(rows) {
    const group = element("div", "info-group");
    group.appendChild(element("div", "info-subtitle", "declared vs observed"));
    rows.forEach((row) => {
      const entry = element("div", "port-entry");
      const head = element("div", "port-name", `${row.output} · ${row.status}`);
      if (row.status !== "match") head.classList.add("latency-flag");
      entry.appendChild(head);
      const detail = [`declared ${row.declared}`, `observed ${row.observed}`];
      if (row.note) detail.push(row.note);
      entry.appendChild(element("div", "port-type", detail.join(" · ")));
      group.appendChild(entry);
    });
    return group;
  }

  const rate = (value) =>
    value === null || value === undefined
      ? "—"
      : `${Number(value).toFixed(1)} Hz`;

  // A node's measured record: input, timer and output rates, the trigger and
  // process time of each output, and the declared diff.
  function measurementCard(record) {
    const children = [];
    if (record.process) {
      const process = record.process;
      const parts = [
        process.state,
        process.pids?.length ? `pid ${process.pids.join(", ")}` : null,
        process.exit
          ? `exit code ${process.exit.code ?? "?"}${process.exit.at ? ` at ${process.exit.at}` : ""}`
          : null,
        process.last_record ? `last record ${process.last_record}` : null,
      ].filter(Boolean);
      children.push(
        element(
          "div",
          process.state === "running" ? "port-type" : "port-type latency-flag",
          parts.join(" · "),
        ),
      );
    }
    if (record.notes) {
      Object.entries(record.notes).forEach(([note, topics]) => {
        const group = element("div", "info-group");
        group.appendChild(
          element("div", "info-subtitle", note.replaceAll("_", " ")),
        );
        topics.forEach((topic) =>
          group.appendChild(element("div", "port-type", topic)),
        );
        children.push(group);
      });
    }
    const list = (title, items, describe) => {
      if (!items?.length) return;
      const group = element("div", "info-group");
      group.appendChild(element("div", "info-subtitle", title));
      items.forEach((item) => {
        const entry = element("div", "port-entry");
        const [name, detail] = describe(item);
        entry.appendChild(element("div", "port-name", name));
        if (detail) entry.appendChild(element("div", "port-type", detail));
        group.appendChild(entry);
      });
      children.push(group);
    };
    list("inputs", record.inputs, (input) => [
      input.topic,
      [
        rate(input.rate_hz),
        input.intra_process ? "intra-process" : null,
        input.duplicate_count
          ? `${input.duplicate_count} duplicate takes`
          : null,
      ]
        .filter(Boolean)
        .join(" · "),
    ]);
    list("timers", record.timers, (timer) => [
      `${timer.period_ms ?? "?"} ms`,
      rate(timer.rate_hz),
    ]);
    list("outputs", record.outputs, (output) => {
      const parts = [rate(output.rate_hz)];
      if (output.trigger) {
        const t = output.trigger;
        const what =
          t.kind === "timer"
            ? `timer ${t.period_ms ?? "?"} ms`
            : t.kind === "input"
              ? `input ${t.topic}${t.intra_process ? " (intra-process)" : ""}`
              : "unknown trigger";
        parts.push(`${what} ${Math.round((t.share ?? 0) * 100)}%`);
      }
      if (output.exec) {
        parts.push(
          `exec ${output.exec.min_ms} / ${output.exec.mean_ms} / ${output.exec.max_ms} ms`,
        );
      }
      return [output.topic, parts.join(" · ")];
    });
    if (record.declared_diff?.length)
      children.push(diffGroup(record.declared_diff));
    if (!children.length) return null;
    return card("Measurement", ...children);
  }

  // Enumerated chains; each entry selects the chain it names.
  function chainsCard(chains) {
    const items = chains.items.map((item) => {
      const entry = element("div", "port-entry chain-item");
      entry.appendChild(element("div", "port-name", item.label));
      if (item.detail)
        entry.appendChild(element("div", "port-type", item.detail));
      entry.onclick = () => {
        chains.items.forEach((other) =>
          other.element?.classList.remove("active"),
        );
        entry.classList.add("active");
        item.onSelect?.();
      };
      item.element = entry;
      return entry;
    });
    return card(chains.title, ...items);
  }

  function interfaceCard(data) {
    const inPorts = data.in_ports || [];
    const outPorts = data.out_ports || [];
    if (!inPorts.length && !outPorts.length) return null;

    const groups = [];
    if (inPorts.length) groups.push(portGroup("Input Ports", inPorts));
    if (outPorts.length) groups.push(portGroup("Output Ports", outPorts));
    return card("Interface", ...groups);
  }

  // Badge naming the parameter's source; links into the editor when the
  // source carries a file location.
  function sourceBadge(parameterType, source) {
    const label =
      PARAM_TYPE_LABELS[parameterType] ||
      (parameterType || "").toLowerCase() ||
      "—";

    if (source && source.file_path) {
      const badge = window.createSourceLink(source.file_path, source.line || 1);
      badge.className = "param-badge";
      badge.textContent = label;
      badge.dataset.type = parameterType || "";
      return badge;
    }

    const badge = element("span", "param-badge", label);
    badge.dataset.type = parameterType || "";
    badge.title = parameterType || "";
    return badge;
  }

  function parameterEntry(name, value, badge) {
    const entry = element("div", "param-entry");
    const head = element("div", "param-head");

    const nameEl = element("span", "param-name", name);
    if (badge) {
      nameEl.appendChild(document.createTextNode(" "));
      nameEl.appendChild(badge);
    }
    head.appendChild(nameEl);
    head.appendChild(element("span", "param-value", scalarText(value)));

    entry.appendChild(head);
    return entry;
  }

  // Parameters arrive either as a plain name -> value map or as records
  // carrying the resolved source.
  function parameterCard(parameters) {
    if (isEmpty(parameters)) return null;

    const entries = Array.isArray(parameters)
      ? parameters.map((param) =>
          parameterEntry(
            param.name,
            param.value,
            sourceBadge(param.parameter_type, param.source),
          ),
        )
      : Object.entries(parameters).map(([name, value]) =>
          parameterEntry(name, value, null),
        );

    return card("Parameter", ...entries);
  }

  function sourceCard(filePath) {
    const row = element("div", "info-row");
    row.appendChild(window.createSourceLink(filePath, 1));
    return card("Source", row);
  }

  // ── Entry point ─────────────────────────────────────────────────────────────

  function render(panel, data, type) {
    panel.innerHTML = "";

    const cards = [
      data.topic ? topicCard(data.topic) : null,
      data.global_topic ? globalTopicCard(data.global_topic) : null,
      data.event ? eventCard(data.event) : null,
      data.latency ? latencyCard(data.latency) : null,
      data.measurement ? measurementCard(data.measurement) : null,
      data.chains ? chainsCard(data.chains) : null,
      data.chain ? chainCard(data.chain) : null,
      infoCard(data),
      interfaceCard(data),
      parameterCard(data.parameters),
    ].filter(Boolean);

    if (!cards.length) {
      cards.push(
        card(
          `${type} Details`,
          element("div", "info-row", "No details available"),
        ),
      );
    }
    if (data.source_file) {
      cards.unshift(sourceCard(data.source_file));
    }

    cards.forEach((entry) => panel.appendChild(entry));
  }

  window.InfoPanel = { render };
})();
