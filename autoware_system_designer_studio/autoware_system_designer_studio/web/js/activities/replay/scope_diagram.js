// Replay: the scope diagram, drawn from the replay's own graph. The bag block
// lists the recorded topics that feed the scope; the boundary frame holds the
// scope's nodes in nested namespace boxes, in the colours of their component,
// with one chip per boundary input on its left side. An edge from a bag topic
// to an input chip is the feed, a remap where the two names differ. A chip or
// bag topic opens the input's sources and selects the input; the selected
// input's chip, bag topic, edges and ports stay emphasized. An output port of
// a node toggles the recording of its topic.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const SVG_NS = ElkCanvas.SVG_NS;

  const FONT = 11;
  const SMALL = 9;
  const PAD = 6;
  // Node box: a head of its name over one row per port.
  const NODE_HEAD = 18;
  const NODE_MIN_WIDTH = 90;
  const ROW = 13;
  const PORT = 5;
  // Chip of the boundary and row of the bag.
  const CHIP = 15;
  const CHIP_GAP = 2;
  const LAYER_GAP = 28;
  // Growth of a namespace box's marks per level above the innermost.
  const LEVEL_SCALE = 1.35;
  // Id of the undrawn port and edge that space a bag without feeds.
  const SPACER = "bag-spacer";

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

  // Name of a bag row: whole where the topic is remapped, else its last two
  // segments, the input chip beside it carrying the whole name.
  function rowName(row) {
    const segments = row.name.split("/").filter(Boolean);
    return row.name !== row.input || segments.length <= 2
      ? row.name
      : `…/${segments.slice(-2).join("/")}`;
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
      // Topic of the selected input, and each input chip's box in the
      // layer's coordinates.
      this.selected = null;
      this.chipAt = new Map();
      this.layer = null;
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
      this.layer = null;
      this.byTopic.clear();
      this.chipAt.clear();
    }

    // Emphasizes the elements of the selected input's topic and mutes the
    // rest of the boundary.
    markSelected() {
      this.byTopic.forEach((items, topic) =>
        items.forEach((item) =>
          item.classList.toggle("selected", topic === this.selected),
        ),
      );
      this.layer?.classList.toggle(
        "replay-g-focused",
        this.byTopic.has(this.selected),
      );
    }

    // Pans the selected input's chip into the view where it lies outside.
    reveal() {
      const box = this.chipAt.get(this.selected);
      if (!box || !this.container.clientHeight) return;
      const { k, x, y } = this.transform;
      const width = this.container.clientWidth;
      const height = this.container.clientHeight;
      const left = x + box.x * k;
      const top = y + box.y * k;
      const inside =
        left >= 0 &&
        top >= 0 &&
        left + box.width * k <= width &&
        top + box.height * k <= height;
      if (inside) return;
      this.transform.x = width / 2 - (box.x + box.width / 2) * k;
      this.transform.y = height / 2 - (box.y + box.height / 2) * k;
      this.updateTransform();
    }

    select(topic) {
      const changed = topic !== this.selected;
      this.selected = topic;
      this.markSelected();
      if (changed && topic) this.reveal();
    }

    width(text, size = FONT) {
      return Math.ceil(this.measureTextWidth(text, size));
    }

    // ── Layout graph ────────────────────────────────────────────────────────

    // Marks of a namespace box at `depth` of `levels`: the outer a box, the
    // larger its label, border and padding.
    boxStyle(depth, levels) {
      const scale = Math.pow(LEVEL_SCALE, levels - 1 - depth);
      const font = Math.round(SMALL * scale);
      return {
        font,
        pad: Math.round(PAD * scale),
        head: font + Math.round(PAD * scale),
        border: +(0.8 * scale).toFixed(1),
      };
    }

    nodeBox(node) {
      const side = (ports) =>
        Math.max(0, ...ports.map((port) => this.width(port.name, SMALL)));
      const rows = Math.max(node.inputs.length, node.outputs.length);
      const width = Math.max(
        NODE_MIN_WIDTH,
        this.width(node.name) + 2 * PAD,
        side(node.inputs) + side(node.outputs) + 5 * PAD,
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
        height: NODE_HEAD + rows * ROW + (rows ? 2 : 0),
        properties: { "org.eclipse.elk.portConstraints": "FIXED_POS" },
        ports: [
          ...node.inputs.map((item, index) => port(item, index, -PORT / 2)),
          ...node.outputs.map((item, index) =>
            port(item, index, width - PORT / 2),
          ),
        ],
      };
    }

    // Layout node of a namespace box or of the node an item names.
    itemBox(item, model, nodes) {
      if (item.node) return this.nodeBox(nodes.get(item.node));
      const style = this.boxStyle(item.depth, model.levels);
      return {
        id: item.id,
        children: item.items.map((inner) => this.itemBox(inner, model, nodes)),
        properties: {
          "org.eclipse.elk.padding": `[top=${style.head + style.pad},left=${style.pad},bottom=${style.pad},right=${style.pad}]`,
          "org.eclipse.elk.nodeSize.constraints": "MINIMUM_SIZE",
          "org.eclipse.elk.nodeSize.minimum": `(${this.width(item.label, style.font) + 2 * style.pad},0)`,
        },
      };
    }

    toElk(model) {
      const stacked = {
        "org.eclipse.elk.portConstraints": "FIXED_SIDE",
        "org.eclipse.elk.portAlignment.default": "CENTER",
        "org.eclipse.elk.spacing.portPort": String(CHIP_GAP),
      };
      const chipWidth =
        Math.max(0, ...model.inputs.map((item) => this.width(item.topic))) +
        2 * PAD;
      const rowWidth = (row) =>
        this.width(rowName(row)) +
        this.width(String(row.count), SMALL) +
        3 * PAD;
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
      const edges = [...model.edges];
      // An undrawn edge keeps a bag without feeds clear of the input chips.
      if (!edges.some((edge) => edge.kind === "feed") && model.inputs.length) {
        bag.ports.push({
          id: SPACER,
          width: 0,
          height: 0,
          properties: { "org.eclipse.elk.port.side": "EAST" },
        });
        edges.push({ id: SPACER, from: SPACER, to: model.inputs[0].id });
      }
      const nodes = new Map(model.nodes.map((node) => [node.id, node]));
      const scope = {
        id: "scope",
        properties: {
          ...stacked,
          "org.eclipse.elk.padding": `[top=${2 * PAD},left=${3 * PAD},bottom=${2 * PAD},right=${2 * PAD}]`,
        },
        ports: model.inputs.map((item) => ({
          id: item.id,
          width: chipWidth,
          height: CHIP,
          properties: { "org.eclipse.elk.port.side": "WEST" },
        })),
        children: model.boxes.map((item) => this.itemBox(item, model, nodes)),
      };
      // The bag, the boundary and the namespace boxes are laid out as one
      // graph: an edge joins ports across any of their borders.
      return {
        id: "root",
        layoutOptions: {
          "org.eclipse.elk.algorithm": "layered",
          "org.eclipse.elk.direction": "RIGHT",
          "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
          "org.eclipse.elk.hierarchyHandling": "INCLUDE_CHILDREN",
          "org.eclipse.elk.layered.layering.strategy": "NETWORK_SIMPLEX",
          "org.eclipse.elk.spacing.nodeNode": String(PAD),
          "org.eclipse.elk.layered.spacing.nodeNodeBetweenLayers":
            String(LAYER_GAP),
          "org.eclipse.elk.spacing.edgeNode": "4",
          "org.eclipse.elk.layered.spacing.edgeNodeBetweenLayers": "6",
          "org.eclipse.elk.spacing.edgeEdge": "3",
          "org.eclipse.elk.layered.spacing.edgeEdgeBetweenLayers": "3",
          "org.eclipse.elk.padding": `[top=${4 * PAD},left=${2 * PAD},bottom=${2 * PAD},right=${2 * PAD}]`,
        },
        children: [bag, scope],
        edges: edges.map((edge) => ({
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
    // had it on screen: boxes under edges under nodes and chips.
    draw(graph, model) {
      const [bag, scope] = graph.children;
      if (this.frameAt) {
        this.transform.x += (this.frameAt.x - scope.x) * this.transform.k;
        this.transform.y += (this.frameAt.y - scope.y) * this.transform.k;
      }
      this.frameAt = { x: scope.x, y: scope.y };
      const { layer } = this.createCanvas();
      this.layer = layer;
      this.byTopic.clear();
      this.chipAt.clear();
      const boxes = svg("g");
      const marks = svg("g");
      // Origin of every box the layout nests, in the layer's coordinates.
      const origin = new Map([["root", { x: 0, y: 0 }]]);
      const placed = (item, parent) => {
        const at = { x: parent.x + item.x, y: parent.y + item.y };
        origin.set(item.id, at);
        return { ...item, ...at };
      };
      const nodes = new Map(model.nodes.map((node) => [node.id, node]));
      const spaces = new Map();
      const index = (items) =>
        items.forEach((item) => {
          if (item.node) return;
          spaces.set(item.id, item);
          index(item.items);
        });
      index(model.boxes);
      const walk = (item, parent) => {
        const at = placed(item, parent);
        if (nodes.has(item.id)) {
          marks.appendChild(this.drawNode(at, nodes.get(item.id)));
          return;
        }
        boxes.appendChild(this.drawSpace(at, spaces.get(item.id), model));
        (item.children || []).forEach((child) => walk(child, at));
      };
      const frame = placed(scope, origin.get("root"));
      boxes.appendChild(this.drawFrame(frame, model));
      (scope.children || []).forEach((child) => walk(child, frame));
      const inputs = new Map(model.inputs.map((input) => [input.id, input]));
      (scope.ports || []).forEach((port) =>
        marks.appendChild(
          this.drawInput(placed(port, frame), inputs.get(port.id)),
        ),
      );
      layer.append(
        boxes,
        ...this.drawEdges(graph.edges, model, origin),
        this.drawBag(placed(bag, origin.get("root")), model),
        marks,
      );
      this.graphBBox = layer.getBBox();
      this.markSelected();
    }

    group(node, className) {
      return svg(
        "g",
        { transform: `translate(${node.x},${node.y})` },
        className,
      );
    }

    // Colours of a component for the theme rules of the element.
    tint(node, guide) {
      if (!guide) return node;
      Object.entries({
        "--c": guide.color,
        "--m": guide.medium_color,
        "--t": guide.text_color,
        "--dc": guide.dark_color,
        "--dm": guide.dark_medium_color,
        "--dt": guide.dark_text_color,
      }).forEach(([name, value]) => {
        if (value) node.style.setProperty(name, value);
      });
      return node;
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
          label(rowName(row), { x: PAD, y: middle }, "replay-g-topic"),
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

    drawFrame(box, model) {
      const g = this.group(box, "replay-g-scope");
      g.append(
        label(model.scopeTitle, { x: 0, y: -PAD }, "replay-g-caption"),
        svg(
          "rect",
          { width: box.width, height: box.height, rx: 6 },
          "replay-g-frame",
        ),
      );
      return g;
    }

    // Namespace box: its label in the head, shortened to the box.
    drawSpace(box, space, model) {
      const style = this.boxStyle(space.depth, model.levels);
      const g = this.tint(this.group(box, "replay-g-space"), space.guide);
      const name = label(
        "",
        { x: style.pad, y: style.pad / 2 + style.head / 2 },
        "replay-g-spacename",
      );
      name.style.fontSize = `${style.font}px`;
      this._truncateSVGText(
        name,
        space.label,
        box.width - 2 * style.pad,
        style.font,
      );
      const rect = svg("rect", {
        width: box.width,
        height: box.height,
        rx: 3,
      });
      rect.style.strokeWidth = style.border;
      g.append(rect, name);
      tooltip(g, [space.label]);
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
      this.chipAt.set(input.topic, port);
      const chip = this.chip(
        port,
        input.topic,
        `replay-g-chip input ${input.status}`,
      );
      tooltip(chip, [
        input.topic,
        input.type,
        replay.model.INPUT_NOTE[input.status],
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
      const g = this.tint(
        this.group(box, `replay-g-node${node.container ? " container" : ""}`),
        node.guide,
      );
      g.append(
        svg("rect", { width: box.width, height: box.height, rx: 3 }),
        label(node.name, { x: PAD, y: NODE_HEAD / 2 }, "replay-g-name"),
      );
      tooltip(g, [node.path, node.container ? "container" : ""]);
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

    // Edge paths in the layer's coordinates; the layout gives each edge in
    // those of its `container`. An edge from an input chip starts at the
    // frame.
    drawEdges(edges, model, origin) {
      const byId = new Map(model.edges.map((edge) => [edge.id, edge]));
      return (edges || [])
        .filter((edge) => edge.sections?.length && byId.has(edge.id))
        .map((edge) => {
          const data = byId.get(edge.id);
          const at = origin.get(edge.container) || origin.get("root");
          const points = edge.sections.flatMap((section) => [
            section.startPoint,
            ...(section.bendPoints || []),
            section.endPoint,
          ]);
          if (data.kind === "input")
            points[0] = { ...points[0], x: Math.max(points[0].x, 0) };
          const path = svg(
            "path",
            {
              d: points
                .map(
                  (point, i) =>
                    `${i ? "L" : "M"}${at.x + point.x},${at.y + point.y}`,
                )
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

  // `handlers` is { choices(input), choose(input, value), record(topic, on),
  // select(topic) }.
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
      input(input, event) {
        handlers.select(input.topic);
        openMenu(input, event);
      },
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

    // Emphasizes the input of `topic`, or none for null; kept across redraws.
    function select(topic) {
      graph.select(topic);
    }

    return { element: pane, show, select };
  }

  replay.scopeDiagram = scopeDiagram;
})();
