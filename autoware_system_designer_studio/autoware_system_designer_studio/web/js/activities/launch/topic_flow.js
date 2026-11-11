// Launch: diagrams of the Topics view's viewer, drawn left to right. A node
// stands between the topics it subscribes and the topics it publishes, one
// port per topic on the facing edge, the services it calls and serves below
// them; a topic stands between its publishers and its subscribers, a service
// between its server and its clients. Nodes are square blocks, topics rounded
// tinted ones and services dashed ones of another tint, in the centre as on
// the cards. Cards and ports share
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

  // Card of a `kind` ("node", "topic" or "service") on the `side` ("in" or "out") of the centre.
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

  // Topic or service of a node's interface. A topic: its status, rate against
  // the declared one, the node's own callback or communication time, its type
  // and the nodes at its other end. A service: its type and those nodes.
  function topicCard(face, side, openTopic) {
    const { ms, node: shortNode, statusChip } = launch.topicLabels;
    const row = face.row;
    const kind = face.service ? "service" : "topic";
    if (!row) {
      const empty = card(kind, side, `connected to no ${kind}`);
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
    const [toward, none] = face.service
      ? input
        ? ["served by", "no server"]
        : ["called by", "no client"]
      : input
        ? ["from", "no publisher"]
        : ["to", "no subscriber"];
    const peerText = peers.length
      ? `${toward} ${
          peers.length === 1 ? shortNode(peers[0]) : `${peers.length} nodes`
        }`
      : none;
    const time =
      face.ms !== null && `${input ? "comm" : "callback"} ${ms(face.ms)}`;
    const own =
      face.seen && face.hz !== null
        ? `${input ? "take" : "publish"} ${launch.topicLabels.hz(face.hz)} Hz`
        : "";
    const rate = face.service ? "" : rateText(row);
    const node = card(
      kind,
      side,
      [
        row.topic,
        row.msgType,
        parts(rate, limitsText(row)),
        parts(own, time),
        peers.length ? `${toward}\n  ${peers.join("\n  ")}` : "",
      ]
        .filter(Boolean)
        .join("\n"),
      () => openTopic(row),
    );
    const name = span("flow-name", row.topic);
    if (!row.declared || face.port === null) name.classList.add("undeclared");
    const type = row.msgType.split("/").pop();
    node.append(
      line("", name, statusChip(row.status)),
      face.service
        ? line("", span("", type), span("flow-muted", peerText))
        : line(
            "",
            span("flow-rate", parts(rate, time)),
            span("flow-muted", parts(type, peerText)),
          ),
    );
    return node;
  }

  // Node at an end of a topic or service: its unit and path, and at a topic
  // its own rate and callback or communication time.
  function nodeCard(end, side, service, openNode) {
    const { hz, ms, node: shortNode } = launch.topicLabels;
    const source = side === "in";
    const time =
      end.ms !== null && `${source ? "callback" : "comm"} ${ms(end.ms)}`;
    const state = service
      ? ""
      : !end.seen
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
        state &&
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

  // Cards and ports of a node's side, row for row: its topics, then its
  // services under a section row titled `title`.
  function nodeSide(faces, side, title, openTopic) {
    const { element } = SD.widgets;
    const cards = [];
    const list = element("div", `flow-ports flow-${side}`);
    const services = faces.filter((face) => face.service).length;
    faces.forEach((face, index) => {
      if (face.service && !faces[index - 1]?.service) {
        cards.push(element("div", "flow-section", `${title} · ${services}`));
        list.appendChild(element("div", "flow-section"));
      }
      cards.push(topicCard(face, side, openTopic));
      const port = element("div", "flow-port", face.port ?? "–");
      port.title =
        face.port === null ? "seen off the design's ports" : face.port;
      if (!face.row) port.classList.add("unconnected");
      if (face.service) port.classList.add("service");
      list.appendChild(port);
    });
    return { cards, ports: list, topics: faces.length - services };
  }

  // Subscribed topics and called services, the node with its ports,
  // published topics and served services.
  function nodeDiagram(node, { openTopic }) {
    const { element } = SD.widgets;
    const { statusChip } = launch.topicLabels;
    const root = element("div", "flow");
    const left = nodeSide(node.inputs, "in", "Client", openTopic);
    const right = nodeSide(node.outputs, "out", "Server", openTopic);
    const [inHead, inBody] = column(
      "flow-left",
      `Subscribe · ${left.topics}`,
      left.cards,
      "no subscription",
    );
    const [outHead, outBody] = column(
      "flow-right",
      `Publish · ${right.topics}`,
      right.cards,
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
    body.append(left.ports, right.ports);
    root.append(inHead, head, outHead, inBody, body, outBody);
    return root;
  }

  // Publishers, the topic's detail, subscribers; for a service its server,
  // its detail and its clients.
  function topicDiagram(row, ends, { openNode }) {
    const { element } = SD.widgets;
    const { hz, ms, statusChip } = launch.topicLabels;
    const root = element("div", "flow");
    const kind = row.service ? "service" : "topic";
    const [from, to, noFrom, noTo] = row.service
      ? ["Server", "Client", "no server", "no client"]
      : ["Publish", "Subscribe", "no publisher", "no subscriber"];
    const [inHead, inBody] = column(
      "flow-left",
      `${from} · ${ends.sources.length}`,
      ends.sources.map((end) => nodeCard(end, "in", row.service, openNode)),
      noFrom,
    );
    const [outHead, outBody] = column(
      "flow-right",
      `${to} · ${ends.consumers.length}`,
      ends.consumers.map((end) => nodeCard(end, "out", row.service, openNode)),
      noTo,
    );
    const head = element("div", `flow-center-head flow-${kind}`);
    const name = span("flow-center-name", row.topic);
    if (!row.declared) name.classList.add("undeclared");
    head.title = row.topic;
    head.append(
      line("", name, statusChip(row.status)),
      line("flow-muted", span("", row.msgType || "outside the design")),
    );
    const body = element("div", `flow-center-body flow-${kind}`);
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
