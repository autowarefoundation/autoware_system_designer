// Replay: the scope diagram, drawn from the replay's own graph. The bag block
// lists the recorded topics that feed the scope; the boundary frame holds the
// scope's nodes, with one chip per boundary input on its left side. An edge
// from a bag topic to an input chip is the feed, a remap where the two names
// differ. A chip or bag topic opens the input's sources; an output port of a
// node toggles the recording of its topic.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const SVG_NS = ElkCanvas.SVG_NS;

  const FONT = 11;
  const SMALL = 9;
  const PAD = 8;
  // Node box: a head of namespace and name over one row per port.
  const NODE_HEAD = 32;
  const NODE_MIN_WIDTH = 120;
  const ROW = 16;
  const PORT = 6;
  // Chip of the boundary and row of the bag.
  const CHIP = 18;
  const CHIP_GAP = 4;
  const LAYER_GAP = 48;
  // Id of the undrawn port and edge that space a bag without feeds.
  const SPACER = "bag-spacer";

  const STATUS = {
    fed: "in the bag",
    remapped: "remapped",
    accepted: "accepted unfed",
    type: "other type in the bag",
    unpublished: "no publisher, not in the bag",
    missing: "not in the bag",
    unknown: "no bag read",
  };

  function svg(tag, attrs = {}, className = "") {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) =>
      node.setAttribute(key, value),
    );
    if (className) node.setAttribute("class", className);
    return node;
  }

  function label(text, attrs, className) {
    const node = svg("text", attrs, className);
    node.textContent = text;
    return node;
  }

  function tooltip(node, lines) {
    const title = svg("title");
    title.textContent = lines.filter(Boolean).join("\n");
    node.appendChild(title);
    return node;
  }

  function chevron(x, y, size, className) {
    return svg(
      "polygon",
      {
        points: `${x},${y} ${x + size},${y + size / 2} ${x},${y + size}`,
      },
      className,
    );
  }

  class ScopeGraph extends ElkCanvas {
    // `handlers` is { input(input, event), record(topic, on) }.
    constructor(container, handlers) {
      super(container);
      this.handlers = handlers;
      // Topic -> drawn elements lit together.
      this.byTopic = new Map();
      // Viewport as last fitted, and where the boundary frame is drawn.
      this.fittedView = null;
      this.frameAt = null;
      this.elkReady = this.initElk();
    }

    fit() {
      this.fitToScreen();
      this.fittedView = { ...this.transform };
    }

    // Whether the viewer moved the viewport off the fitted one.
    moved() {
      const fitted = this.fittedView;
      return (
        !fitted ||
        ["x", "y", "k"].some((key) => fitted[key] !== this.transform[key])
      );
    }

    onContainerResize() {
      if (!this.moved() && this.container.clientHeight) this.fit();
    }

    clear() {
      this.createCanvas();
      this.frameAt = null;
    }

    width(text, size = FONT) {
      return Math.ceil(this.measureTextWidth(text, size));
    }

    // ── Layout graph ────────────────────────────────────────────────────────

    nodeBox(node) {
      const side = (ports) =>
        Math.max(0, ...ports.map((port) => this.width(port.name, SMALL)));
      const rows = Math.max(node.inputs.length, node.outputs.length);
      const width = Math.max(
        NODE_MIN_WIDTH,
        this.width(node.name) + 2 * PAD,
        this.width(node.namespace, SMALL) + 2 * PAD,
        side(node.inputs) + side(node.outputs) + 4 * PAD,
      );
      const port = (item, index, x) => ({
        id: item.id,
        width: PORT,
        height: PORT,
        x,
        y: NODE_HEAD + index * ROW + (ROW - PORT) / 2,
      });
      return {
        id: node.id,
        width,
        height: NODE_HEAD + rows * ROW + (rows ? PAD / 2 : 0),
        properties: { "org.eclipse.elk.portConstraints": "FIXED_POS" },
        ports: [
          ...node.inputs.map((item, index) => port(item, index, -PORT / 2)),
          ...node.outputs.map((item, index) =>
            port(item, index, width - PORT / 2),
          ),
        ],
      };
    }

    toElk(model) {
      const stacked = {
        "org.eclipse.elk.portConstraints": "FIXED_SIDE",
        "org.eclipse.elk.portAlignment.default": "CENTER",
        "org.eclipse.elk.spacing.portPort": String(CHIP_GAP),
      };
      const chips = (items, side) => {
        const width =
          Math.max(0, ...items.map((item) => this.width(item.topic))) + 2 * PAD;
        return items.map((item) => ({
          id: item.id,
          width,
          height: CHIP,
          properties: { "org.eclipse.elk.port.side": side },
        }));
      };
      const rowWidth = (row) =>
        this.width(row.name) + this.width(String(row.count), SMALL) + 3 * PAD;
      const bag = {
        id: "bag",
        width: Math.max(
          NODE_MIN_WIDTH,
          this.width(model.bagNote, SMALL) + 2 * PAD,
          ...model.bag.map(rowWidth),
        ),
        height: Math.max(1, model.bag.length) * (CHIP + CHIP_GAP) + PAD,
        // Left of the boundary also while no topic feeds it.
        properties: {
          ...stacked,
          "org.eclipse.elk.layered.layering.layerConstraint": "FIRST_SEPARATE",
        },
        ports: model.bag.map((row) => ({
          id: row.id,
          width: PORT,
          height: CHIP,
          properties: { "org.eclipse.elk.port.side": "EAST" },
        })),
      };
      const feeds = model.edges.filter((edge) => edge.kind === "feed");
      // An undrawn edge keeps a bag without feeds clear of the input chips.
      if (!feeds.length && model.inputs.length) {
        bag.ports.push({
          id: SPACER,
          width: 0,
          height: 0,
          properties: { "org.eclipse.elk.port.side": "EAST" },
        });
        feeds.push({ id: SPACER, from: SPACER, to: model.inputs[0].id });
      }
      const inner = model.edges.filter((edge) => edge.kind !== "feed");
      const scope = {
        id: "scope",
        properties: {
          ...stacked,
          "org.eclipse.elk.padding": `[top=${3 * PAD},left=${3 * PAD},bottom=${3 * PAD},right=${3 * PAD}]`,
          "org.eclipse.elk.spacing.nodeNode": String(2 * PAD),
          "org.eclipse.elk.layered.spacing.nodeNodeBetweenLayers":
            String(LAYER_GAP),
        },
        ports: chips(model.inputs, "WEST"),
        children: model.nodes.map((node) => this.nodeBox(node)),
        edges: inner.map((edge) => ({
          id: edge.id,
          sources: [edge.from],
          targets: [edge.to],
        })),
      };
      return {
        id: "root",
        layoutOptions: {
          "org.eclipse.elk.algorithm": "layered",
          "org.eclipse.elk.direction": "RIGHT",
          "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
          "org.eclipse.elk.layered.layering.strategy": "NETWORK_SIMPLEX",
          "org.eclipse.elk.layered.spacing.nodeNodeBetweenLayers":
            String(LAYER_GAP),
          "org.eclipse.elk.padding": `[top=${4 * PAD},left=${2 * PAD},bottom=${2 * PAD},right=${2 * PAD}]`,
        },
        children: [bag, scope],
        edges: feeds.map((edge) => ({
          id: edge.id,
          sources: [edge.from],
          targets: [edge.to],
        })),
      };
    }

    async layout(model) {
      await this.elkReady;
      return this.elk.layout(this.toElk(model));
    }

    // ── Drawing ─────────────────────────────────────────────────────────────

    // Draws a laid-out graph with the boundary frame where the last drawing
    // had it on screen.
    draw(graph, model) {
      const [bag, scope] = graph.children;
      if (this.frameAt) {
        this.transform.x += (this.frameAt.x - scope.x) * this.transform.k;
        this.transform.y += (this.frameAt.y - scope.y) * this.transform.k;
      }
      this.frameAt = { x: scope.x, y: scope.y };
      const { layer } = this.createCanvas();
      this.byTopic.clear();
      layer.append(
        this.drawBag(bag, model),
        this.drawScope(scope, model),
        ...this.drawEdges(graph.edges, model),
      );
      this.graphBBox = layer.getBBox();
    }

    group(node, className) {
      return svg(
        "g",
        { transform: `translate(${node.x},${node.y})` },
        className,
      );
    }

    // Element of a topic: lit with every other element of the topic while the
    // pointer is on one.
    ofTopic(node, topic) {
      if (!this.byTopic.has(topic)) this.byTopic.set(topic, []);
      this.byTopic.get(topic).push(node);
      const light = (on) =>
        this.byTopic
          .get(topic)
          .forEach((item) => item.classList.toggle("lit", on));
      node.addEventListener("mouseenter", () => light(true));
      node.addEventListener("mouseleave", () => light(false));
      return node;
    }

    clickable(node, handler) {
      node.classList.add("replay-g-action");
      node.addEventListener("click", (event) => {
        if (this.hasDragged) return;
        event.stopPropagation();
        handler(event);
      });
      return node;
    }

    drawBag(box, model) {
      const g = this.group(box, "replay-g-bag");
      g.append(
        label(model.bagTitle, { x: 0, y: -PAD }, "replay-g-caption"),
        svg("rect", { width: box.width, height: box.height, rx: 4 }),
      );
      if (!model.bag.length)
        g.appendChild(
          label(
            model.bagNote,
            { x: box.width / 2, y: box.height / 2, "text-anchor": "middle" },
            "replay-g-note",
          ),
        );
      const rows = new Map(model.bag.map((row) => [row.id, row]));
      const inputs = new Map(model.inputs.map((input) => [input.topic, input]));
      (box.ports || []).forEach((port) => {
        const row = rows.get(port.id);
        if (!row) return;
        const middle = port.y + port.height / 2;
        const item = svg("g", {}, `replay-g-row ${row.status}`);
        item.append(
          svg("rect", {
            x: 2,
            y: port.y,
            width: box.width - 4,
            height: port.height,
            rx: 3,
          }),
          label(row.name, { x: PAD, y: middle }, "replay-g-topic"),
          label(
            String(row.count),
            { x: box.width - PAD - PORT, y: middle, "text-anchor": "end" },
            "replay-g-count",
          ),
          chevron(
            box.width - PORT / 2,
            middle - PORT / 2,
            PORT,
            "replay-g-port",
          ),
        );
        tooltip(item, [
          row.name,
          row.type,
          `${row.count} messages`,
          row.name === row.input ? "" : `fed as ${row.input}`,
        ]);
        this.ofTopic(item, row.input);
        this.clickable(item, (event) =>
          this.handlers.input(inputs.get(row.input), event),
        );
        g.appendChild(item);
      });
      return g;
    }

    drawScope(box, model) {
      const g = this.group(box, "replay-g-scope");
      g.append(
        label(model.scopeTitle, { x: 0, y: -PAD }, "replay-g-caption"),
        svg(
          "rect",
          { width: box.width, height: box.height, rx: 6 },
          "replay-g-frame",
        ),
        ...this.drawEdges(box.edges, model),
      );
      const nodes = new Map(model.nodes.map((node) => [node.id, node]));
      (box.children || []).forEach((child) =>
        g.appendChild(this.drawNode(child, nodes.get(child.id))),
      );
      const inputs = new Map(model.inputs.map((input) => [input.id, input]));
      (box.ports || []).forEach((port) =>
        g.appendChild(this.drawInput(port, inputs.get(port.id))),
      );
      return g;
    }

    chip(port, text, className) {
      const g = this.group(port, className);
      g.append(
        svg("rect", {
          width: port.width,
          height: port.height,
          rx: port.height / 2,
        }),
        label(text, { x: PAD, y: port.height / 2 }, "replay-g-topic"),
      );
      return g;
    }

    drawInput(port, input) {
      const chip = this.chip(
        port,
        input.topic,
        `replay-g-chip input ${input.status}`,
      );
      tooltip(chip, [
        input.topic,
        input.type,
        STATUS[input.status],
        input.status === "remapped" ? `recorded as ${input.recordedName}` : "",
        input.status === "type" ? `recorded as ${input.recordedType}` : "",
        input.publishers.length
          ? `published by ${input.publishers.join(", ")}`
          : "no publisher in the design",
        `subscribed by ${input.subscribers.join(", ")}`,
      ]);
      this.ofTopic(chip, input.topic);
      return this.clickable(chip, (event) => this.handlers.input(input, event));
    }

    drawNode(box, node) {
      const g = this.group(
        box,
        `replay-g-node${node.container ? " container" : ""}`,
      );
      g.append(
        svg("rect", { width: box.width, height: box.height, rx: 4 }),
        label(
          node.container ? "container" : node.namespace,
          { x: PAD, y: 10 },
          "replay-g-namespace",
        ),
        label(node.name, { x: PAD, y: 22 }, "replay-g-name"),
      );
      tooltip(g, [node.path]);
      const position = new Map(
        (box.ports || []).map((port) => [port.id, port]),
      );
      const drawPort = (port, output) => {
        const at = position.get(port.id);
        const middle = at.y + at.height / 2;
        const item = svg(
          "g",
          {},
          `replay-g-nodeport ${output ? "output" : "input"}` +
            (port.boundary ? " boundary" : "") +
            (port.recorded ? " recorded" : ""),
        );
        item.append(
          // Hit area of the row's half of the box.
          svg(
            "rect",
            {
              x: output ? box.width / 2 : 0,
              y: middle - ROW / 2,
              width: box.width / 2,
              height: ROW,
            },
            "replay-g-hit",
          ),
          chevron(at.x, at.y, PORT, "replay-g-port"),
          label(
            port.name,
            output
              ? { x: box.width - PAD, y: middle, "text-anchor": "end" }
              : { x: PAD, y: middle },
            "replay-g-portname",
          ),
        );
        tooltip(item, [
          port.name,
          port.topic,
          port.type,
          output
            ? `${port.boundary ? "boundary output" : "internal topic"}, ` +
              (port.recorded
                ? "recorded; click to stop recording it"
                : "not recorded; click to record it")
            : "",
        ]);
        this.ofTopic(item, port.topic);
        if (output)
          this.clickable(item, () =>
            this.handlers.record(port.topic, !port.recorded),
          );
        g.appendChild(item);
      };
      node.inputs.forEach((port) => drawPort(port, false));
      node.outputs.forEach((port) => drawPort(port, true));
      return g;
    }

    drawEdges(edges, model) {
      const byId = new Map(model.edges.map((edge) => [edge.id, edge]));
      return (edges || [])
        .filter((edge) => edge.sections?.length && byId.has(edge.id))
        .map((edge) => {
          const data = byId.get(edge.id);
          const points = edge.sections.flatMap((section) => [
            section.startPoint,
            ...(section.bendPoints || []),
            section.endPoint,
          ]);
          const path = svg(
            "path",
            {
              d: points
                .map((point, i) => `${i ? "L" : "M"}${point.x},${point.y}`)
                .join(" "),
            },
            `replay-g-edge ${data.kind} ${data.status}`,
          );
          if (!this.byTopic.has(data.topic)) this.byTopic.set(data.topic, []);
          this.byTopic.get(data.topic).push(path);
          return path;
        });
    }
  }

  // `handlers` is { choices(input), choose(input, value), record(topic, on) }.
  function scopeDiagram(handlers) {
    const pane = element("div", "replay-diagram");
    const canvas = element("div", "diagram-canvas replay-graph");
    const empty = element(
      "p",
      "replay-graph-empty",
      "Check nodes or modules in the scope tree to draw the scope and its boundary.",
    );
    const summary = element("div", "replay-graph-summary");
    const fit = element("button", "panel-button replay-graph-fit", "Fit");
    fit.type = "button";
    fit.title = "Fit the scope in the view";
    const menu = element("div", "replay-menu");
    menu.hidden = true;
    const loading = element("div", "diagram-loading");
    loading.append(
      element("div", "spinner"),
      element("div", "diagram-loading-text", "Laying out the scope..."),
    );
    loading.hidden = true;
    pane.append(canvas, empty, summary, fit, menu, loading);

    function closeMenu() {
      menu.hidden = true;
    }

    // Sources of `input` beside the pointer; picking one closes the list.
    function openMenu(input, event) {
      const head = element("div", "replay-menu-head");
      head.append(
        element("span", "replay-menu-topic", input.topic),
        element("span", "replay-menu-type", input.type),
      );
      const list = element("div", "replay-menu-list");
      handlers.choices(input).forEach((choice) => {
        const item = element(
          "button",
          `replay-menu-item${choice.current ? " current" : ""}`,
        );
        item.type = "button";
        item.append(
          element("span", "replay-menu-label", choice.label),
          element("span", "replay-menu-note", choice.note),
        );
        item.addEventListener("click", () => {
          closeMenu();
          if (!choice.current) handlers.choose(input, choice.value);
        });
        list.appendChild(item);
      });
      menu.replaceChildren(head, list);
      menu.hidden = false;
      const frame = pane.getBoundingClientRect();
      const left = Math.min(
        event.clientX - frame.left,
        frame.width - menu.offsetWidth - 4,
      );
      const top = Math.min(
        event.clientY - frame.top,
        frame.height - menu.offsetHeight - 4,
      );
      menu.style.left = `${Math.max(4, left)}px`;
      menu.style.top = `${Math.max(4, top)}px`;
    }

    const graph = new ScopeGraph(canvas, {
      input: openMenu,
      record: handlers.record,
    });
    document.addEventListener("mousedown", (event) => {
      if (!menu.contains(event.target)) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeMenu();
    });
    fit.addEventListener("click", () => graph.fit());

    // Model drawn or being laid out, serialized, and the scope and bag the
    // viewport was last fitted to.
    let shown = "";
    let fitted = null;
    let drawnKey = null;
    const fitKey = (model) => `${model.key}\n${model.bagTitle}`;

    function fitOnce(key) {
      if (fitted === key || drawnKey !== key || !canvas.clientHeight) return;
      graph.fit();
      fitted = key;
    }

    // Draws `model` (model.graph with its titles), or the empty hint for null.
    // A new scope or bag is fitted; otherwise a viewport the viewer moved
    // stays, and one still as fitted follows the graph.
    async function show(model) {
      const next = model ? JSON.stringify(model) : "";
      empty.hidden = !!model;
      fit.hidden = summary.hidden = !model;
      if (next === shown) {
        if (model) fitOnce(fitKey(model));
        return;
      }
      shown = next;
      closeMenu();
      if (!model) {
        graph.clear();
        drawnKey = fitted = null;
        loading.hidden = true;
        return;
      }
      summary.textContent = model.summary;
      summary.classList.toggle("blocked", model.blocked);
      loading.hidden = fitted === fitKey(model);
      try {
        const laid = await graph.layout(model);
        if (next !== shown) return;
        if (!graph.moved()) fitted = null;
        graph.draw(laid, model);
        drawnKey = fitKey(model);
        fitOnce(drawnKey);
      } catch (error) {
        console.error("replay scope diagram:", error);
        if (next === shown) graph.showError(`Scope diagram: ${error.message}`);
      } finally {
        if (next === shown) loading.hidden = true;
      }
    }

    return { element: pane, show };
  }

  replay.scopeDiagram = scopeDiagram;
})();
