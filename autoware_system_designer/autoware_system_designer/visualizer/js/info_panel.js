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
