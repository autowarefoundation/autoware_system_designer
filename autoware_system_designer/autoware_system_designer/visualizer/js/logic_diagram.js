// Logic Diagram Module
// Event-propagation view of a system: every event is a vertex, trigger relations
// are the edges, and the chain of causes behind any event is traceable from it.

(function () {
  const SVG_NS = ElkCanvas.SVG_NS;

  // Frequency is propagated from clock roots only, so these types start a chain.
  const CLOCK_TYPES = new Set(["periodic", "once"]);

  // Trigger semantics carried by shape: the gate's type is what decides how many
  // of its inputs have to fire before it does.
  const GATE_SHAPES = {
    and: "and",
    or: "or",
    periodic: "clock",
    once: "tag",
  };

  const UPSTREAM_COLOR = "green";
  const DOWNSTREAM_COLOR = "orange";
  const CHAIN_LIST_LIMIT = 40;

  // Legend entries, in the order the chain reads.
  const LEGEND = [
    ["clock", "periodic / once — chain root"],
    ["and", "and — waits for every trigger"],
    ["or", "or — fires on any trigger"],
    ["box", "on_input / on_trigger — plain relay"],
    ["unknown", "type not declared"],
  ];

  class LogicDiagramModule extends ElkCanvas {
    // ── Initialization ──────────────────────────────────────────────────────────

    constructor(container, options = {}) {
      super(container, options);

      this.rootData = null;
      this.events = new Map(); // eventId → event record
      this.instances = new Map(); // instanceId → { data, depth }
      this.succ = new Map(); // eventId → [eventId] it triggers
      this.pred = new Map(); // eventId → [eventId] triggering it
      this.edgeList = []; // { id, from, to, cross }
      this.edgeIdByKey = new Map(); // "from>to" → edgeId
      this.clocksOf = new Map(); // eventId → Set(clock root id)
      this.clockRootIds = [];
      this.activeIds = new Set(); // events carrying at least one trigger relation
      this.gateIds = new Set();
      this.groups = new Map(); // instanceId → <g>
      this.groupDepth = new Map();
      this.currentGraph = null;
      this.showUnlinked = false;
      this.traceMode = "both";
      this.selectedId = null;

      this.init();
    }

    async init() {
      try {
        await this.initElk();
        await this.loadAndRender();
      } catch (error) {
        console.error("Error loading logic diagram:", error);
        this.showError(`Error loading logic diagram: ${error.message}`);
      }
    }

    async loadAndRender() {
      if (!window.logicDiagramData?.[this.options.mode]) {
        await this.loadDataScript(this.options.mode, "logic_diagram");
      }
      const data = window.logicDiagramData?.[this.options.mode];
      if (!data) {
        throw new Error(
          `No logic diagram data available for mode: ${this.options.mode}`,
        );
      }
      this.rootData = data;
      this.buildEventModel(data);
      await this.layoutAndRender();
    }

    // ── Event model ─────────────────────────────────────────────────────────────

    buildEventModel(root) {
      this.events.clear();
      this.instances.clear();
      this.succ.clear();
      this.pred.clear();
      this.edgeIdByKey.clear();
      this.edgeList = [];
      this.activeIds.clear();

      const addEvent = (event, instance, kind, port) => {
        if (!event?.unique_id || this.events.has(event.unique_id)) return;
        this.events.set(String(event.unique_id), {
          id: String(event.unique_id),
          name: event.name || "event",
          type: event.type || null,
          kind,
          ownerId: String(instance.unique_id),
          port: port || null,
          frequency: event.frequency ?? null,
          warn_rate: event.warn_rate ?? null,
          error_rate: event.error_rate ?? null,
          timeout: event.timeout ?? null,
          triggers: (event.trigger_ids || []).map(String),
          actions: (event.action_ids || []).map(String),
        });
      };

      const visit = (instance, depth) => {
        if (!instance?.unique_id) return;
        this.instances.set(String(instance.unique_id), {
          data: instance,
          depth,
        });
        (instance.in_ports || []).forEach((port) =>
          addEvent(port.event, instance, "input", port),
        );
        (instance.out_ports || []).forEach((port) =>
          addEvent(port.event, instance, "output", port),
        );
        (instance.events || []).forEach((event) =>
          addEvent(event, instance, "gate", null),
        );
        (instance.children || []).forEach((child) => visit(child, depth + 1));
      };
      visit(root, 0);

      // trigger_ids and action_ids are the same relation read from either end.
      const link = (fromId, toId) => {
        if (fromId === toId) return;
        const from = this.events.get(fromId);
        const to = this.events.get(toId);
        if (!from || !to) return;
        const key = `${fromId}>${toId}`;
        if (this.edgeIdByKey.has(key)) return;

        const id = `le_${this.edgeList.length}`;
        this.edgeIdByKey.set(key, id);
        this.edgeList.push({
          id,
          from: fromId,
          to: toId,
          cross: from.ownerId !== to.ownerId,
        });
        if (!this.succ.has(fromId)) this.succ.set(fromId, []);
        this.succ.get(fromId).push(toId);
        if (!this.pred.has(toId)) this.pred.set(toId, []);
        this.pred.get(toId).push(fromId);
      };

      this.events.forEach((event) => {
        event.triggers.forEach((triggerId) => link(triggerId, event.id));
        event.actions.forEach((actionId) => link(event.id, actionId));
      });

      this.events.forEach((event, id) => {
        if (this.succ.has(id) || this.pred.has(id)) this.activeIds.add(id);
      });

      this._computeClocks();
    }

    // An event no clock root reaches is one nothing paces; the builder leaves its
    // frequency unset for the same reason.
    _computeClocks() {
      this.clocksOf.clear();
      this.clockRootIds = [...this.events.values()]
        .filter((event) => CLOCK_TYPES.has(event.type))
        .map((event) => event.id);

      this.clockRootIds.forEach((rootId) => {
        const stack = [rootId];
        const seen = new Set();
        while (stack.length) {
          const id = stack.pop();
          if (seen.has(id)) continue;
          seen.add(id);
          if (!this.clocksOf.has(id)) this.clocksOf.set(id, new Set());
          this.clocksOf.get(id).add(rootId);
          (this.succ.get(id) || []).forEach((next) => {
            if (!seen.has(next)) stack.push(next);
          });
        }
      });
    }

    _isVisible(eventId) {
      return this.showUnlinked || this.activeIds.has(eventId);
    }

    // An `and` gate fires at the slowest of its triggers, so triggers arriving at
    // different rates mean the declared rate cannot hold for all of them.
    _rateMismatch(event) {
      if (event.type !== "and") return null;
      const rates = new Set(
        (this.pred.get(event.id) || [])
          .map((id) => this.events.get(id)?.frequency)
          .filter((frequency) => frequency !== null && frequency !== undefined),
      );
      return rates.size > 1 ? [...rates].sort((a, b) => a - b) : null;
    }

    // ── Labels ──────────────────────────────────────────────────────────────────

    rateLabel(frequency) {
      if (frequency === null || frequency === undefined) return "";
      if (frequency === 0) return "once";
      return `${Number(frequency.toFixed(3))}Hz`;
    }

    eventLabel(event) {
      const rate = this.rateLabel(event.frequency);
      return rate ? `${event.name} ${rate}` : event.name;
    }

    gateSubLabel(event) {
      return this.rateLabel(event.frequency) || event.type || "?";
    }

    // ── ELK graph ───────────────────────────────────────────────────────────────

    buildElkGraph() {
      this.gateIds.clear();
      // Gates sit one level below the leaf instances that own them.
      this.maxDepth = this.findMaxDepth(this.rootData) + 1;

      const visiblePorts = (ports) =>
        (ports || []).filter(
          (port) =>
            port.event?.unique_id &&
            this._isVisible(String(port.event.unique_id)),
        );

      const addPorts = (node, ports, side, style) => {
        ports.forEach((port) => {
          const event = this.events.get(String(port.event.unique_id));
          const text = this.eventLabel(event);
          node.ports.push({
            id: event.id,
            width: style.portSize,
            height: style.portSize,
            properties: { "org.eclipse.elk.port.side": side },
            labels: [
              {
                text,
                width: this.measureTextWidth(text, style.portLabelFontSz),
                height: style.portSize,
              },
            ],
          });
        });
      };

      const gateNode = (event, style) => {
        this.gateIds.add(event.id);
        const nameWidth = this.measureTextWidth(event.name, style.fontSize);
        const subWidth = this.measureTextWidth(
          this.gateSubLabel(event),
          style.nsSize,
        );
        return {
          id: event.id,
          width: Math.round(
            Math.max(
              style.nodeWidth * 0.6,
              Math.max(nameWidth, subWidth) + style.fontSize * 2,
            ),
          ),
          height: Math.round(style.nodeBaseH * 0.75),
        };
      };

      const convert = (instance, depth) => {
        if (!instance?.unique_id) return null;
        const style = this.getLayerStyle(depth);
        const gateStyle = this.getLayerStyle(depth + 1);

        const ins = visiblePorts(instance.in_ports);
        const outs = visiblePorts(instance.out_ports);
        const gates = (instance.events || [])
          .filter(
            (event) =>
              event.unique_id && this._isVisible(String(event.unique_id)),
          )
          .map((event) => this.events.get(String(event.unique_id)));
        const children = (instance.children || [])
          .map((child) => convert(child, depth + 1))
          .filter(Boolean);

        if (!ins.length && !outs.length && !gates.length && !children.length) {
          return null;
        }

        const node = {
          id: String(instance.unique_id),
          labels: [
            { text: instance.namespace || "" },
            { text: instance.name || String(instance.unique_id) },
          ],
          namespace: instance.namespace || "",
          children: [],
          ports: [],
          properties: {
            "org.eclipse.elk.portConstraints": "FIXED_SIDE",
            "org.eclipse.elk.nodeLabels.placement": "H_CENTER V_TOP",
            "org.eclipse.elk.portLabels.placement": "INSIDE",
            "org.eclipse.elk.portAlignment.default": "CENTER",
            "org.eclipse.elk.spacing.portPort": String(style.portSpacing),
            "org.eclipse.elk.spacing.nodeNode": String(style.nodeSpacing),
            "org.eclipse.elk.spacing.edgeNode": String(style.edgeNodeSpacing),
            "org.eclipse.elk.layered.spacing.edgeNodeBetweenLayers": String(
              style.edgeNodeBetweenLayers,
            ),
            "org.eclipse.elk.spacing.edgeEdge": String(style.edgeEdgeSpacing),
            "org.eclipse.elk.layered.spacing.edgeEdgeBetweenLayers": String(
              style.edgeEdgeBetweenLayers,
            ),
            "org.eclipse.elk.padding": `[top=${style.elkPadding},left=${style.elkPadding},bottom=${style.elkPadding},right=${style.elkPadding}]`,
          },
        };

        addPorts(node, ins, "WEST", style);
        addPorts(node, outs, "EAST", style);

        node.children = [
          ...children,
          ...gates.map((event) => gateNode(event, gateStyle)),
        ];

        if (!node.children.length) {
          node.width = this.instanceWidth(instance, ins, outs, style);
          node.height = Math.max(
            style.nodeBaseH,
            style.nodeBaseH +
              Math.max(ins.length, outs.length) *
                (style.portSize + style.portSpacing * 2),
          );
        }
        return node;
      };

      const root = convert(this.rootData, 0) || {
        id: String(this.rootData.unique_id || "root"),
        children: [],
        ports: [],
      };
      delete root.width;
      delete root.height;

      root.edges = this.edgeList
        .filter(
          (edge) => this._isVisible(edge.from) && this._isVisible(edge.to),
        )
        .map((edge) => ({
          id: edge.id,
          sources: [edge.from],
          targets: [edge.to],
          properties: {},
        }));

      return root;
    }

    instanceWidth(instance, ins, outs, style) {
      const labelWidth = (ports) =>
        ports.reduce((max, port) => {
          const event = this.events.get(String(port.event.unique_id));
          return Math.max(
            max,
            this.measureTextWidth(
              this.eventLabel(event),
              style.portLabelFontSz,
            ),
          );
        }, 0);

      const titleWidth = this.measureTextWidth(
        instance.name || "",
        style.fontSize,
      );
      const innerPad = style.portSize * 3;
      return Math.max(
        style.nodeWidth,
        labelWidth(ins) + labelWidth(outs) + innerPad,
        titleWidth + innerPad,
      );
    }

    // ── Layout + render ─────────────────────────────────────────────────────────

    async layoutAndRender() {
      const graph = await this.elk.layout(this.buildElkGraph(), {
        layoutOptions: {
          algorithm: "layered",
          "org.eclipse.elk.direction": "RIGHT",
          "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
          // Trigger relations cross instance boundaries, so layering has to see
          // the whole hierarchy at once.
          "org.eclipse.elk.hierarchyHandling": "INCLUDE_CHILDREN",
          // Layer index is the longest trigger path from a chain root, which is
          // what makes the horizontal axis read as causal depth.
          "org.eclipse.elk.layered.layering.strategy": "LONGEST_PATH",
          "org.eclipse.elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          "org.eclipse.elk.layered.nodePlacement.bk.edgeStraightening": "NONE",
          "org.eclipse.elk.padding": "[top=50,left=50,bottom=50,right=50]",
        },
      });

      this.currentGraph = graph;
      this.render(graph);
      this.fitToScreen();
    }

    render(graph) {
      const { layer } = this.createCanvas();
      this.container.classList.add("logic-diagram-container");
      this.groups.clear();
      this.groupDepth.clear();
      this.selectedId = null;

      this.renderInstance(graph, layer, 0);
      this.renderEdges(graph, layer);
      this.renderToolbar();
    }

    renderInstance(node, parentGroup, depth) {
      const style = this.getLayerStyle(depth);
      const instance = this.instances.get(node.id)?.data || {};

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("transform", `translate(${node.x || 0},${node.y || 0})`);
      g.setAttribute("id", node.id);
      g.classList.add("logic-instance");
      this.groups.set(node.id, g);
      this.groupDepth.set(node.id, depth);

      g.appendChild(this.buildInstanceRect(node, instance, depth, style));
      if (node.labels?.length) {
        this.appendInstanceLabels(g, node, instance, style, depth);
      }

      (node.ports || []).forEach((port) =>
        g.appendChild(this.buildEventPort(port, instance, node, style)),
      );

      (node.children || []).forEach((child) => {
        if (this.gateIds.has(child.id)) {
          g.appendChild(this.buildGate(child, instance, depth + 1));
        } else {
          this.renderInstance(child, g, depth + 1);
        }
      });

      parentGroup.appendChild(g);
    }

    buildInstanceRect(node, instance, depth, style) {
      const defaults = this.isDarkMode()
        ? this.styleDefaults.dark
        : this.styleDefaults.light;
      const guide = instance.vis_guide;

      let fill = this.themed(guide, "background_color", defaults.bg);
      if (instance.entity_type === "node") {
        fill = this.themed(guide, "medium_color", defaults.nodeBg);
      }
      if (depth === 0) fill = defaults.rootBg;

      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("width", node.width || 0);
      rect.setAttribute("height", node.height || 0);
      rect.setAttribute("rx", style.cornerR);
      rect.setAttribute("fill", fill);
      rect.setAttribute("stroke", this.themed(guide, "color", defaults.stroke));
      rect.setAttribute("stroke-width", style.borderW);
      rect.classList.add("logic-instance-rect");

      rect.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.clearHighlights();
        this.updateInfoPanel(instance, "Node");
      };

      return rect;
    }

    appendInstanceLabels(g, node, instance, style, depth) {
      const guide = instance.vis_guide;
      let yOffset = Math.round(style.fontSize * 0.8);

      if (node.labels.length > 1 && node.labels[0].text) {
        const nsText = document.createElementNS(SVG_NS, "text");
        nsText.setAttribute("x", (node.width || 0) / 2);
        nsText.setAttribute("y", yOffset);
        nsText.classList.add("node-label");
        nsText.style.fontSize = `${style.nsSize}px`;
        nsText.style.fill = this.themed(guide, "text_color", "#6c757d");
        const lines = this._wrapSVGText(
          nsText,
          node.namespace || "",
          (node.width || 0) / 2,
          (node.width || 0) - style.badgePad * 2,
          style.nsSize,
        );
        g.appendChild(nsText);
        yOffset += (style.nsSize + 2) * lines;
      }

      const nameText = document.createElementNS(SVG_NS, "text");
      nameText.setAttribute("x", (node.width || 0) / 2);
      nameText.setAttribute("y", yOffset + style.fontSize / 2);
      nameText.textContent = node.labels[node.labels.length - 1].text;
      nameText.classList.add("node-label");
      nameText.style.fontSize = `${style.fontSize}px`;
      nameText.style.fill = this.themed(
        guide,
        "text_color",
        this.isDarkMode() ? "#e9ecef" : "#333",
      );
      if (depth <= 1) nameText.style.fontWeight = "bold";
      g.appendChild(nameText);
    }

    // Port events are the boundary of an instance: the chevron points the way the
    // message travels, so an input and an output read the same on either side.
    buildEventPort(port, instance, node, style) {
      const event = this.events.get(port.id);
      const side = (port.x || 0) > (node.width || 0) / 2 ? "out" : "in";
      const size = port.width;

      const glyph = document.createElementNS(SVG_NS, "polygon");
      glyph.setAttribute("points", `0,0 ${size},${size / 2} 0,${size}`);
      glyph.classList.add("logic-event", `logic-event-${side}`);
      if (!this.clocksOf.has(port.id)) glyph.classList.add("logic-unclocked");

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeEvent(event);
      glyph.appendChild(title);

      const group = document.createElementNS(SVG_NS, "g");
      group.setAttribute("id", port.id);
      group.setAttribute("transform", `translate(${port.x},${port.y})`);
      group.style.cursor = "pointer";
      group.appendChild(glyph);

      group.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.traceFrom(port.id);
      };

      (port.labels || []).forEach((label) => {
        const text = document.createElementNS(SVG_NS, "text");
        const lx = (label.x || 0) + (label.width || 0) / 2;
        text.setAttribute("x", lx + (lx >= 0 ? 1 : -1) * style.portLabelOffset);
        text.setAttribute("y", (label.y || 0) + (label.height || 0) / 2);
        text.textContent = label.text;
        text.classList.add("port-label");
        text.style.fontSize = `${style.portLabelFontSz}px`;
        text.style.fill = this.themed(
          instance.vis_guide,
          "text_color",
          this.isDarkMode() ? "#e9ecef" : "#333",
        );
        group.appendChild(text);
      });

      return group;
    }

    buildGate(node, instance, depth) {
      const style = this.getLayerStyle(depth);
      const event = this.events.get(node.id);
      const mismatch = this._rateMismatch(event);

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("transform", `translate(${node.x},${node.y})`);
      g.setAttribute("id", node.id);
      g.classList.add("logic-gate-group");
      g.style.cursor = "pointer";

      const shape = this.buildGateShape(
        event.type,
        node.width,
        node.height,
        style,
      );
      shape.classList.add("logic-gate");
      if (!this.clocksOf.has(node.id)) shape.classList.add("logic-unclocked");
      if (!event.type) shape.classList.add("logic-gate-unknown");
      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeEvent(event, mismatch);
      shape.appendChild(title);
      g.appendChild(shape);

      const name = document.createElementNS(SVG_NS, "text");
      name.setAttribute("x", node.width / 2);
      name.setAttribute("y", node.height * 0.4);
      name.classList.add("node-label", "logic-gate-label");
      name.style.fontSize = `${style.fontSize}px`;
      this._truncateSVGText(
        name,
        event.name,
        node.width - style.fontSize,
        style.fontSize,
      );
      g.appendChild(name);

      const sub = document.createElementNS(SVG_NS, "text");
      sub.setAttribute("x", node.width / 2);
      sub.setAttribute("y", node.height * 0.72);
      sub.textContent = this.gateSubLabel(event);
      sub.classList.add("node-label", "logic-gate-sublabel");
      sub.style.fontSize = `${style.nsSize}px`;
      g.appendChild(sub);

      if (mismatch) {
        const mark = document.createElementNS(SVG_NS, "polygon");
        const s = Math.max(2, node.height * 0.22);
        const x = node.width - s * 1.4;
        mark.setAttribute(
          "points",
          `${x},${s * 1.3} ${x + s},${s * 1.3} ${x + s / 2},${s * 0.2}`,
        );
        mark.classList.add("logic-rate-mismatch");
        g.appendChild(mark);
      }

      g.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.traceFrom(node.id);
      };

      return g;
    }

    // Gate outlines: `and` closes on a single arc, `or` on a concave back, a clock
    // is a pill and `once` a tag; every other type stays a plain box.
    buildGateShape(type, w, h, style) {
      const shape = GATE_SHAPES[type];
      if (shape === "and") {
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute(
          "d",
          `M0,0 L${w * 0.55},0 C${w},0 ${w},${h} ${w * 0.55},${h} L0,${h} Z`,
        );
        return path;
      }
      if (shape === "or") {
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute(
          "d",
          `M0,0 C${w * 0.3},${h * 0.3} ${w * 0.3},${h * 0.7} 0,${h} ` +
            `C${w * 0.55},${h} ${w * 0.85},${h * 0.8} ${w},${h / 2} ` +
            `C${w * 0.85},${h * 0.2} ${w * 0.55},0 0,0 Z`,
        );
        return path;
      }
      if (shape === "tag") {
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute(
          "d",
          `M0,0 L${w - h * 0.45},0 L${w},${h / 2} L${w - h * 0.45},${h} L0,${h} Z`,
        );
        return path;
      }
      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("width", w);
      rect.setAttribute("height", h);
      rect.setAttribute("rx", shape === "clock" ? h / 2 : style.cornerR);
      return rect;
    }

    // ELK reparents every edge to the deepest instance holding both of its ends
    // and reports its route in that instance's own coordinates.
    renderEdges(graph, rootLayer) {
      const rootGroup = this.groups.get(graph.id) || rootLayer;

      (graph.edges || []).forEach((laidEdge) => {
        if (!laidEdge.sections) return;
        const group = this.groups.get(laidEdge.container) || rootGroup;
        const depth = this.groupDepth.get(laidEdge.container) ?? 0;
        group.appendChild(this.buildEdgePath(laidEdge, depth));
      });
    }

    buildEdgePath(laidEdge, depth) {
      const style = this.getLayerStyle(depth);
      let d = "";
      laidEdge.sections.forEach((section) => {
        d += `M ${section.startPoint.x} ${section.startPoint.y} `;
        (section.bendPoints || []).forEach((bp) => (d += `L ${bp.x} ${bp.y} `));
        d += `L ${section.endPoint.x} ${section.endPoint.y} `;
      });

      const fromId = laidEdge.sources?.[0];
      const toId = laidEdge.targets?.[0];
      const crossesInstance =
        this.events.get(fromId)?.ownerId !== this.events.get(toId)?.ownerId;

      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("id", laidEdge.id);
      path.setAttribute("d", d);
      path.setAttribute("data-depth", String(depth));
      path.setAttribute("stroke-width", style.edgeW);
      path.setAttribute("marker-end", `url(#arrowhead-depth-${depth})`);
      path.classList.add("edge-path", "logic-edge");
      path.classList.add(
        crossesInstance ? "logic-edge-link" : "logic-edge-trigger",
      );
      if (!this.clocksOf.has(toId)) path.classList.add("logic-unclocked");
      if (!crossesInstance) {
        const w = parseFloat(style.edgeW);
        path.setAttribute("stroke-dasharray", `${w * 4} ${w * 3}`);
      }

      path.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.traceFrom(toId);
      };

      return path;
    }

    updateTheme() {
      if (this.currentGraph) this.render(this.currentGraph);
    }

    // ── Chain tracing ───────────────────────────────────────────────────────────

    // Walks the trigger relation in one direction and returns the events reached,
    // in hop order, together with the edges the walk used.
    walkChain(startId, adjacency) {
      const order = [];
      const hops = new Map([[startId, 0]]);
      const edges = new Set();
      const queue = [startId];
      const seen = new Set([startId]);

      while (queue.length) {
        const id = queue.shift();
        (adjacency.get(id) || []).forEach((nextId) => {
          const key =
            adjacency === this.succ ? `${id}>${nextId}` : `${nextId}>${id}`;
          const edgeId = this.edgeIdByKey.get(key);
          if (edgeId) edges.add(edgeId);
          if (seen.has(nextId)) return;
          seen.add(nextId);
          hops.set(nextId, (hops.get(id) || 0) + 1);
          order.push(nextId);
          queue.push(nextId);
        });
      }
      return { order, hops, edges };
    }

    traceFrom(eventId) {
      const event = this.events.get(eventId);
      if (!event) return;

      this.clearHighlights();
      this.selectedId = eventId;

      const empty = { order: [], hops: new Map(), edges: new Set() };
      const upstream =
        this.traceMode === "down" ? empty : this.walkChain(eventId, this.pred);
      const downstream =
        this.traceMode === "up" ? empty : this.walkChain(eventId, this.succ);

      upstream.order.forEach((id) => this.highlightEvent(id, UPSTREAM_COLOR));
      upstream.edges.forEach((id) => this.highlightEdge(id, UPSTREAM_COLOR));
      downstream.order.forEach((id) =>
        this.highlightEvent(id, DOWNSTREAM_COLOR),
      );
      downstream.edges.forEach((id) =>
        this.highlightEdge(id, DOWNSTREAM_COLOR),
      );
      this.highlightEvent(eventId, "default");

      this.updateInfoPanel(
        this.describeChain(event, upstream, downstream),
        "Event",
      );
    }

    describeEvent(event, mismatch = null) {
      const parts = [
        `${event.kind} · ${event.type || "type not declared"}`,
        this.rateLabel(event.frequency) || "no clock",
      ];
      if (mismatch) parts.push(`trigger rates: ${mismatch.join(" / ")}`);
      return `${event.name}\n${parts.join("\n")}`;
    }

    describeChain(event, upstream, downstream) {
      const owner = this.instances.get(event.ownerId)?.data || {};
      const entry = (id, hops) => {
        const item = this.events.get(id);
        const instance = this.instances.get(item.ownerId)?.data || {};
        return {
          name: item.name,
          path: instance.path || instance.name || "",
          type: item.type || "—",
          rate: this.rateLabel(item.frequency) || "no clock",
          hops: hops.get(id) || 0,
        };
      };
      const list = (walk) =>
        walk.order.slice(0, CHAIN_LIST_LIMIT).map((id) => entry(id, walk.hops));

      const clocks = [...(this.clocksOf.get(event.id) || [])].map((id) => {
        const clock = this.events.get(id);
        const instance = this.instances.get(clock.ownerId)?.data || {};
        return {
          name: clock.name,
          path: instance.path || "",
          rate: this.rateLabel(clock.frequency) || "no clock",
        };
      });

      return {
        name: event.name,
        path: owner.path || "",
        source_file: owner.source_file,
        event: {
          kind: event.kind,
          type: event.type || "not declared",
          rate: this.rateLabel(event.frequency) || "no clock",
          warn_rate: event.warn_rate,
          error_rate: event.error_rate,
          timeout: event.timeout,
          mismatch: this._rateMismatch(event),
        },
        chain: {
          clocks,
          upstream: list(upstream),
          downstream: list(downstream),
          upstream_total: upstream.order.length,
          downstream_total: downstream.order.length,
          limit: CHAIN_LIST_LIMIT,
        },
      };
    }

    // ── Highlighting ────────────────────────────────────────────────────────────

    clearHighlights() {
      const scope = this.currentSvgRoot || this.container;
      if (!scope) return;

      scope.querySelectorAll(".logic-highlighted").forEach((el) => {
        el.classList.remove("logic-highlighted");
        el.style.stroke = "";
        el.style.strokeWidth = "";
        el.style.fill = "";
        if (el.tagName === "path" && el.classList.contains("logic-edge")) {
          const depth = parseInt(el.getAttribute("data-depth") || "0", 10);
          el.setAttribute("marker-end", `url(#arrowhead-depth-${depth})`);
        }
      });
      scope
        .querySelectorAll(".logic-instance-rect.logic-touched")
        .forEach((el) => {
          el.classList.remove("logic-touched");
          el.style.stroke = "";
          el.style.strokeWidth = "";
        });
      this.selectedId = null;
    }

    highlightEvent(eventId, preset) {
      const color = this.colorPresets[preset]?.port;
      const group = document.getElementById(eventId);
      if (!group || !color) return;

      const shape = group.querySelector(".logic-event, .logic-gate");
      if (shape) {
        shape.classList.add("logic-highlighted");
        shape.style.stroke = color;
        shape.style.fill = color;
      }
      this.markInstance(this.events.get(eventId)?.ownerId, color);
    }

    highlightEdge(edgeId, preset) {
      const path = document.getElementById(edgeId);
      const color = this.colorPresets[preset]?.edge;
      if (!path || !color) return;

      const depth = parseInt(path.getAttribute("data-depth") || "0", 10);
      path.classList.add("logic-highlighted");
      path.style.stroke = color;
      path.style.strokeWidth =
        (parseFloat(this.getLayerStyle(depth).edgeW) * 3).toFixed(1) + "px";
      path.setAttribute(
        "marker-end",
        `url(#arrowhead-highlighted-${preset}-depth-${depth})`,
      );
      if (path.parentNode) path.parentNode.appendChild(path);
    }

    markInstance(instanceId, color) {
      const rect = this.groups
        .get(instanceId)
        ?.querySelector(":scope > .logic-instance-rect");
      if (!rect || rect.classList.contains("logic-touched")) return;
      rect.classList.add("logic-touched");
      rect.style.stroke = color;
      rect.style.strokeWidth =
        (parseFloat(rect.getAttribute("stroke-width") || "1") * 2).toFixed(1) +
        "px";
    }

    highlightUnclocked() {
      this.clearHighlights();
      const color = this.colorPresets.red.port;
      this.events.forEach((event, id) => {
        if (this.clocksOf.has(id) || !this._isVisible(id)) return;
        const shape = document
          .getElementById(id)
          ?.querySelector(".logic-event, .logic-gate");
        if (!shape) return;
        shape.classList.add("logic-highlighted");
        shape.style.stroke = color;
      });
    }

    highlightMismatches() {
      this.clearHighlights();
      const color = this.colorPresets.red.port;
      const found = [];
      this.events.forEach((event, id) => {
        const mismatch = this._rateMismatch(event);
        if (!mismatch || !this._isVisible(id)) return;
        found.push({ event, mismatch });
        const shape = document.getElementById(id)?.querySelector(".logic-gate");
        if (!shape) return;
        shape.classList.add("logic-highlighted");
        shape.style.stroke = color;
        shape.style.strokeWidth = "2px";
      });
      this.updateInfoPanel(
        {
          chain: {
            title: "Mixed trigger rates",
            clocks: null,
            upstream: [],
            downstream: found.map(({ event, mismatch }) => ({
              name: event.name,
              path: this.instances.get(event.ownerId)?.data.path || "",
              type: event.type,
              rate: mismatch
                .map((rate) => this.rateLabel(rate) || "—")
                .join(" / "),
              hops: 0,
            })),
            downstream_label: "Gates",
            upstream_total: 0,
            downstream_total: found.length,
            limit: found.length,
          },
        },
        "Event",
      );
    }

    // Centers one event in the viewport at a readable zoom. Screen geometry is
    // read back after the scale change, so the pan is exact.
    focusEvent(eventId, minScale = 0.6) {
      const element = document.getElementById(eventId);
      if (!element) return;

      if (this.transform.k < minScale) {
        this.transform.k = minScale;
        this.updateTransform();
      }
      const target = element.getBoundingClientRect();
      const view = this.container.getBoundingClientRect();
      this.transform.x +=
        view.x + view.width / 2 - (target.x + target.width / 2);
      this.transform.y +=
        view.y + view.height / 2 - (target.y + target.height / 2);
      this.updateTransform();
    }

    // ── Toolbar ─────────────────────────────────────────────────────────────────

    renderToolbar() {
      const bar = document.createElement("div");
      bar.className = "logic-toolbar";

      const row = (...children) => {
        const div = document.createElement("div");
        div.className = "logic-toolbar-row";
        children.forEach((child) => div.appendChild(child));
        return div;
      };
      const label = (text) => {
        const span = document.createElement("span");
        span.className = "logic-toolbar-label";
        span.textContent = text;
        return span;
      };
      const button = (text, onClick, active = false) => {
        const btn = document.createElement("button");
        btn.className = "logic-btn";
        btn.textContent = text;
        btn.classList.toggle("active", active);
        btn.onclick = onClick;
        return btn;
      };

      const traceButtons = [
        ["both", "both"],
        ["up", "causes"],
        ["down", "effects"],
      ].map(([mode, text]) =>
        button(
          text,
          () => {
            this.traceMode = mode;
            bar
              .querySelectorAll("[data-trace]")
              .forEach((el) =>
                el.classList.toggle("active", el.dataset.trace === mode),
              );
            if (this.selectedId) this.traceFrom(this.selectedId);
          },
          this.traceMode === mode,
        ),
      );
      traceButtons.forEach((btn, i) => {
        btn.dataset.trace = ["both", "up", "down"][i];
      });

      bar.appendChild(row(label("Trace"), ...traceButtons));
      bar.appendChild(
        row(
          button("unclocked", () => this.highlightUnclocked()),
          button("rate mismatch", () => this.highlightMismatches()),
          button("clear", () => this.clearHighlights()),
          button("fit", () => this.fitToScreen()),
        ),
      );

      const toggle = document.createElement("label");
      toggle.className = "logic-toggle";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = this.showUnlinked;
      box.onchange = async () => {
        this.showUnlinked = box.checked;
        await this.layoutAndRender();
      };
      toggle.appendChild(box);
      toggle.appendChild(
        document.createTextNode(" events with no trigger relation"),
      );
      bar.appendChild(toggle);

      bar.appendChild(this.buildRootPicker());
      bar.appendChild(this.buildCounters());
      bar.appendChild(this.buildLegend());
      this.container.appendChild(bar);
    }

    // Every chain starts at a clock, so the roots are the entry points into the
    // graph the viewport cannot show at once.
    buildRootPicker() {
      const row = document.createElement("div");
      row.className = "logic-toolbar-row";

      const select = document.createElement("select");
      select.className = "logic-select";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = `go to chain root (${this.clockRootIds.length})`;
      select.appendChild(placeholder);

      this.clockRootIds
        .map((id) => {
          const event = this.events.get(id);
          const instance = this.instances.get(event.ownerId)?.data || {};
          return { id, event, path: instance.path || "" };
        })
        .sort((a, b) => a.path.localeCompare(b.path))
        .forEach(({ id, event, path }) => {
          const option = document.createElement("option");
          option.value = id;
          option.textContent = `${path}/${event.name} · ${this.rateLabel(event.frequency) || "—"}`;
          select.appendChild(option);
        });

      select.onchange = () => {
        if (!select.value) return;
        this.traceFrom(select.value);
        this.focusEvent(select.value);
      };

      row.appendChild(select);
      return row;
    }

    buildCounters() {
      const shown = [...this.events.keys()].filter((id) => this._isVisible(id));
      const unclocked = shown.filter((id) => !this.clocksOf.has(id)).length;

      const div = document.createElement("div");
      div.className = "logic-counters";
      div.textContent =
        `${shown.length} events · ${this.clockRootIds.length} chain roots · ` +
        `${unclocked} unclocked`;
      return div;
    }

    buildLegend() {
      const details = document.createElement("details");
      details.className = "logic-legend";
      const summary = document.createElement("summary");
      summary.textContent = "Legend";
      details.appendChild(summary);

      LEGEND.forEach(([shape, text]) => {
        const rowEl = document.createElement("div");
        rowEl.className = "logic-legend-row";

        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("width", "26");
        svg.setAttribute("height", "14");
        svg.setAttribute("viewBox", "0 0 26 14");
        const type = { clock: "periodic", and: "and", or: "or" }[shape] || null;
        const glyph = this.buildGateShape(type, 24, 12, { cornerR: 2 });
        glyph.setAttribute("transform", "translate(1,1)");
        glyph.classList.add("logic-gate");
        if (shape === "unknown") glyph.classList.add("logic-gate-unknown");
        svg.appendChild(glyph);

        rowEl.appendChild(svg);
        const span = document.createElement("span");
        span.textContent = text;
        rowEl.appendChild(span);
        details.appendChild(rowEl);
      });
      return details;
    }
  }

  window.LogicDiagramModule = LogicDiagramModule;
})();
