// Launch: diagrams of the Topics view's viewer, drawn left to right. A node
// stands between its input topics and its output topics, one port per topic
// on the facing edge; a topic stands between its source nodes and its
// consumer nodes. Cards and ports share one row pitch, so a card's connector
// meets its port, or the edge of the topic, on a straight line. A topic card
// opens the topic, a node card the node.
(function () {
  const SD = window.SD;
  const launch = (SD.activities.launch ||= {});

  function parts(...items) {
    return items.filter(Boolean).join(" · ");
  }

  function line(className, ...children) {
    const node = SD.widgets.element("div", `flow-line ${className}`.trim());
    node.append(...children.filter(Boolean));
    return node;
  }

  function span(className, text) {
    return SD.widgets.element("span", className, text);
  }

  // Rate against the declared one.
  function rateText(row) {
    const { hz } = launch.topicLabels;
    const rate = `${hz(row.hz)} Hz`;
    return row.declaredHz ? `${rate} / ${hz(row.declaredHz)} declared` : rate;
  }

  function limitsText(row) {
    const { hz } = launch.topicLabels;
    return parts(
      row.warnHz && `warn below ${hz(row.warnHz)} Hz`,
      row.errorHz && `error below ${hz(row.errorHz)} Hz`,
    );
  }

  function card(className, title, onOpen) {
    const node = SD.widgets.element("div", `flow-card ${className}`);
    node.title = title;
    if (onOpen) {
      node.classList.add("openable");
      node.addEventListener("click", onOpen);
    }
    return node;
  }

  // Topic of a node's interface: its status, rate and type, the node's own
  // callback or communication time, and the nodes at its other end.
  function topicCard(face, side, openTopic) {
    const { ms, node: shortNode, statusChip } = launch.topicLabels;
    const row = face.row;
    if (!row) {
      const empty = card(`flow-${side} unconnected`, "connected to no topic");
      empty.append(line("flow-muted", span("", "not connected")));
      return empty;
    }
    const input = side === "in";
    const others = input ? row.designPublishers : row.designSubscribers;
    const seenOthers = (input ? row.publishers : row.subscribers).map(
      (end) => end.node,
    );
    const peers = [...new Set([...others, ...seenOthers])];
    const peerText = peers.length
      ? `${input ? "from" : "to"} ${
          peers.length === 1 ? shortNode(peers[0]) : `${peers.length} nodes`
        }`
      : input
        ? "no source"
        : "no consumer";
    const time =
      face.ms !== null && `${input ? "comm" : "callback"} ${ms(face.ms)}`;
    const own =
      face.seen && face.hz !== null
        ? `${input ? "take" : "publish"} ${launch.topicLabels.hz(face.hz)} Hz`
        : "";
    const node = card(
      `flow-${side}`,
      [
        row.topic,
        row.msgType,
        parts(rateText(row), limitsText(row)),
        parts(own, time),
        peers.length ? `${input ? "from" : "to"}\n  ${peers.join("\n  ")}` : "",
      ]
        .filter(Boolean)
        .join("\n"),
      () => openTopic(row),
    );
    const name = span("flow-name", row.topic);
    if (!row.declared || face.port === null) name.classList.add("undeclared");
    node.append(
      line("", name, statusChip(row.status)),
      line("", span("flow-rate", rateText(row)), time && span("", time)),
      line(
        "flow-muted",
        span("", parts(row.msgType.split("/").pop(), peerText)),
      ),
    );
    return node;
  }

  // Node at an end of a topic: its own rate and callback or communication time.
  function nodeCard(end, side, openNode) {
    const { hz, ms, node: shortNode } = launch.topicLabels;
    const source = side === "in";
    const time =
      end.ms !== null && `${source ? "callback" : "comm"} ${ms(end.ms)}`;
    const state = !end.seen
      ? "not seen"
      : end.hz === null
        ? "new"
        : `${hz(end.hz)} Hz`;
    const node = card(
      `flow-${side}`,
      [
        end.node,
        end.unit,
        parts(state, time),
        end.declared ? "" : "outside the design",
      ]
        .filter(Boolean)
        .join("\n"),
      () => openNode(end.node),
    );
    const name = span("flow-name", shortNode(end.node));
    if (!end.declared) name.classList.add("undeclared");
    node.append(
      line("", name, end.unit && span("flow-unit", end.unit)),
      line(
        "",
        span(end.seen ? "flow-rate" : "flow-muted", state),
        time && span("", time),
      ),
      line("flow-muted", span("", end.node)),
    );
    return node;
  }

  function column(className, title, cards, none) {
    const { element } = SD.widgets;
    const head = element("div", `flow-col-title ${className}`, title);
    const body = element("div", `flow-col ${className}`);
    if (cards.length) body.append(...cards);
    else body.appendChild(element("div", "flow-none", none));
    return [head, body];
  }

  function ports(faces, side) {
    const { element } = SD.widgets;
    const list = element("div", `flow-ports flow-${side}`);
    faces.forEach((face) => {
      const port = element("div", "flow-port", face.port ?? "–");
      port.title =
        face.port === null ? "seen off the design's ports" : face.port;
      if (!face.row) port.classList.add("unconnected");
      list.appendChild(port);
    });
    return list;
  }

  // Input topics, the node with its ports, output topics.
  function nodeDiagram(node, { openTopic }) {
    const { element } = SD.widgets;
    const { statusChip } = launch.topicLabels;
    const root = element("div", "flow");
    const [inHead, inBody] = column(
      "flow-left",
      `Input topics · ${node.inputs.length}`,
      node.inputs.map((face) => topicCard(face, "in", openTopic)),
      "no input",
    );
    const [outHead, outBody] = column(
      "flow-right",
      `Output topics · ${node.outputs.length}`,
      node.outputs.map((face) => topicCard(face, "out", openTopic)),
      "no output",
    );
    const head = element("div", "flow-center-head");
    const name = span(
      "flow-center-name",
      node.node.split("/").filter(Boolean).pop() || node.node,
    );
    if (!node.declared) name.classList.add("undeclared");
    head.title = node.node;
    head.append(
      line("", name, node.status && statusChip(node.status)),
      line("flow-muted", span("", parts(node.node, node.unit))),
    );
    const body = element("div", "flow-center-body flow-node-ports");
    body.append(ports(node.inputs, "in"), ports(node.outputs, "out"));
    root.append(inHead, head, outHead, inBody, body, outBody);
    return root;
  }

  // Source nodes, the topic's detail, consumer nodes.
  function topicDiagram(row, ends, { openNode }) {
    const { element } = SD.widgets;
    const { hz, ms, statusChip } = launch.topicLabels;
    const root = element("div", "flow");
    const [inHead, inBody] = column(
      "flow-left",
      `Sources · ${ends.sources.length}`,
      ends.sources.map((end) => nodeCard(end, "in", openNode)),
      "no source",
    );
    const [outHead, outBody] = column(
      "flow-right",
      `Consumers · ${ends.consumers.length}`,
      ends.consumers.map((end) => nodeCard(end, "out", openNode)),
      "no consumer",
    );
    const head = element("div", "flow-center-head");
    const name = span("flow-center-name", row.topic);
    if (!row.declared) name.classList.add("undeclared");
    head.title = row.topic;
    head.append(
      line("", name, statusChip(row.status)),
      line("flow-muted", span("", row.msgType || "outside the design")),
    );
    const body = element("div", "flow-center-body");
    const detail = element("dl", "flow-detail");
    const [, meaning] = launch.TOPIC_STATUS[row.status];
    [
      ["Status", meaning],
      [
        "Rate",
        row.hz === null
          ? ""
          : `${hz(row.hz)} Hz${row.rateFrom === "take" ? " (take rate)" : ""}`,
      ],
      [
        "Declared",
        row.declaredHz
          ? parts(`${hz(row.declaredHz)} Hz`, limitsText(row))
          : "",
      ],
      ["Callback", ms(row.callbackMs)],
      ["Comm", ms(row.commMs)],
      ["Silent", row.silentS ? `${row.silentS} s` : ""],
    ].forEach(([label, value]) => {
      if (!value) return;
      detail.append(element("dt", "", label), element("dd", "", value));
    });
    body.appendChild(detail);
    root.append(inHead, head, outHead, inBody, body, outBody);
    return root;
  }

  launch.topicFlow = { nodeDiagram, topicDiagram };
})();
