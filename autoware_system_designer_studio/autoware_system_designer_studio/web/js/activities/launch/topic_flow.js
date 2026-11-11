// Launch: diagrams of the Topics view's viewer, drawn left to right. A node
// stands between the topics it subscribes and the topics it publishes, one
// port per topic on the facing edge; a topic stands between its publish
// servers and its subscribe clients. Nodes are square blocks and topics
// rounded tinted ones, in the centre as on the cards. Cards and ports share
// one row pitch, so a card's connector meets its port, or the edge of the
// topic, on a straight line. A topic card opens the topic, a node card the
// node.
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
    return row.declaredHz
      ? `${hz(row.hz)} / ${hz(row.declaredHz)} Hz`
      : `${hz(row.hz)} Hz`;
  }

  function limitsText(row) {
    const { hz } = launch.topicLabels;
    return parts(
      row.warnHz && `warn below ${hz(row.warnHz)} Hz`,
      row.errorHz && `error below ${hz(row.errorHz)} Hz`,
    );
  }

  // Card of a `kind` ("node" or "topic") on the `side` ("in" or "out") of the centre.
  function card(kind, side, title, onOpen) {
    const node = SD.widgets.element(
      "div",
      `flow-card flow-${kind} flow-${side}`,
    );
    node.title = title;
    if (onOpen) {
      node.classList.add("openable");
      node.addEventListener("click", onOpen);
    }
    return node;
  }

  // Topic of a node's interface: its status, rate against the declared one,
  // the node's own callback or communication time, its type and the nodes at
  // its other end.
  function topicCard(face, side, openTopic) {
    const { ms, node: shortNode, statusChip } = launch.topicLabels;
    const row = face.row;
    if (!row) {
      const empty = card("topic", side, "connected to no topic");
      empty.classList.add("unconnected");
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
        ? "no server"
        : "no client";
    const time =
      face.ms !== null && `${input ? "comm" : "callback"} ${ms(face.ms)}`;
    const own =
      face.seen && face.hz !== null
        ? `${input ? "take" : "publish"} ${launch.topicLabels.hz(face.hz)} Hz`
        : "";
    const node = card(
      "topic",
      side,
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
      line(
        "",
        span("flow-rate", parts(rateText(row), time)),
        span("flow-muted", parts(row.msgType.split("/").pop(), peerText)),
      ),
    );
    return node;
  }

  // Node at an end of a topic: its unit, its own rate and callback or
  // communication time, and its path.
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
      "node",
      side,
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
        span(end.seen ? "flow-rate" : "flow-muted", parts(state, time)),
        span("flow-muted flow-path", end.node),
      ),
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

  // Subscribed topics, the node with its ports, published topics.
  function nodeDiagram(node, { openTopic }) {
    const { element } = SD.widgets;
    const { statusChip } = launch.topicLabels;
    const root = element("div", "flow");
    const [inHead, inBody] = column(
      "flow-left",
      `Subscribe · ${node.inputs.length}`,
      node.inputs.map((face) => topicCard(face, "in", openTopic)),
      "no subscription",
    );
    const [outHead, outBody] = column(
      "flow-right",
      `Publish · ${node.outputs.length}`,
      node.outputs.map((face) => topicCard(face, "out", openTopic)),
      "no publisher",
    );
    const head = element("div", "flow-center-head flow-node");
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
    const body = element("div", "flow-center-body flow-node flow-node-ports");
    body.append(ports(node.inputs, "in"), ports(node.outputs, "out"));
    root.append(inHead, head, outHead, inBody, body, outBody);
    return root;
  }

  // Publish servers, the topic's detail, subscribe clients.
  function topicDiagram(row, ends, { openNode }) {
    const { element } = SD.widgets;
    const { hz, ms, statusChip } = launch.topicLabels;
    const root = element("div", "flow");
    const [inHead, inBody] = column(
      "flow-left",
      `Publish servers · ${ends.sources.length}`,
      ends.sources.map((end) => nodeCard(end, "in", openNode)),
      "no publish server",
    );
    const [outHead, outBody] = column(
      "flow-right",
      `Subscribe clients · ${ends.consumers.length}`,
      ends.consumers.map((end) => nodeCard(end, "out", openNode)),
      "no subscribe client",
    );
    const head = element("div", "flow-center-head flow-topic");
    const name = span("flow-center-name", row.topic);
    if (!row.declared) name.classList.add("undeclared");
    head.title = row.topic;
    head.append(
      line("", name, statusChip(row.status)),
      line("flow-muted", span("", row.msgType || "outside the design")),
    );
    const body = element("div", "flow-center-body flow-topic");
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
