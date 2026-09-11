// Logic Diagram Module
// Chain view of a system: every event is a vertex, trigger relations are the
// edges, and the chain of causes behind any event is traceable from it. Events
// sit in the box of the node declaring them; that box is the only nesting the
// drawing has, and one scale serves all of it.

(function () {
  const SVG_NS = ElkCanvas.SVG_NS;

  // Frequency is propagated from clock roots only, so these types start a chain.
  const CLOCK_TYPES = new Set(["periodic", "once"]);

  // Trigger semantics carried by shape: the type of a process event is what
  // decides how many of its triggers have to fire before it does.
  const TYPE_SHAPES = {
    and: "and",
    or: "or",
    periodic: "clock",
    once: "tag",
  };

  const UPSTREAM_COLOR = "green";
  const DOWNSTREAM_COLOR = "orange";
  const CHAIN_LIST_LIMIT = 40;

  // One scale for the whole drawing; a vertex is a single row.
  const VIEW = {
    rowH: 16,
    glyphW: 13,
    glyphH: 11,
    fontSize: 9,
    subSize: 7,
    padX: 6,
    gap: 4,
    labelChars: 26,
    eventChars: 15,
    markW: 7,
    nodeSpacing: 6,
    layerSpacing: 20,
    edgeSpacing: 2,
    aspectRatio: 1.6,
  };

  // The node box around the events one node owns.
  const GROUP = {
    fontSize: 10,
    padTop: 15,
    pad: 4,
    rowSpacing: 2,
    layerSpacing: 12,
    edgeSpacing: 2,
    nameChars: 34,
    prefix: "lg_",
  };

  // Viewport scales at which a label becomes legible; the node name outlives the
  // event name, and below both only the glyph and the box color remain.
  const LOD_NODE = 0.3;
  const LOD_NAME = 0.45;
  const LOD_RATE = 0.75;

  // A level chooses which events keep a vertex of their own, what a vertex
  // stands for, and whether vertices sit in the box of the node owning them.
  // Transparent events fold into the edges running through them.
  const LEVELS = {
    nodes: {
      title: "nodes",
      button: "nodes",
      transparent: () => false,
      vertexOf: (event) => event.ownerId,
    },
    process: {
      title: "process events",
      button: "process events",
      transparent: (event) => event.kind !== "process",
      vertexOf: (event) => event.id,
    },
    events: {
      title: "events",
      button: "all events",
      transparent: () => false,
      vertexOf: (event) => event.id,
    },
  };

  // How the drawing is cut into boxes. A box keeps the events it holds together
  // and lengthens every link that leaves it, so the cut is a layout choice.
  const GROUPINGS = {
    node: { title: "node", keyOf: (d, v) => v.ownerId },
    component: {
      title: "component",
      keyOf: (d, v) => d._componentOf(v.ownerId),
    },
    linked: {
      title: "linked",
      keyOf: (d, v) => `c_${d._communities().get(v.ownerId)}`,
    },
    none: { title: "none", keyOf: () => null },
  };

  // Namespace depth a component box is cut at.
  const COMPONENT_DEPTH = 1;

  // Legend entries, in the order the chain reads.
  const LEGEND = [
    ["port", "a topic the node subscribes or publishes"],
    ["clock", "periodic / once — chain root"],
    ["and", "and — waits for every trigger"],
    ["or", "or — fires on any trigger"],
    ["box", "on_input / on_trigger — runs on one trigger"],
    ["unknown", "type not declared"],
  ];

  // Sample rates the legend draws the color ramp from.
  const RATE_RAMP = [0.1, 0.5, 2, 8, 30, 100];

  const LEGEND_NOTES = [
    "a process event is one the node design declares under events:",
    "box — the node owning the events in it",
    "solid edge — crosses a node, dashed — inside one",
    "an edge carries the events the level folds away",
  ];

  class LogicDiagramModule extends ElkCanvas {
    // ── Initialization ──────────────────────────────────────────────────────────

    constructor(container, options = {}) {
      super(container, options);

      this.rootData = null;
      this.events = new Map(); // eventId → event record
      this.instances = new Map(); // instanceId → { data, depth }
      this.instanceByPath = new Map(); // instance path → instance
      this.succ = new Map(); // eventId → [eventId] it triggers
      this.pred = new Map(); // eventId → [eventId] triggering it
      this.edgeList = []; // { id, from, to, cross }
      this.edgeIdByKey = new Map(); // "from>to" → edgeId
      this.clocksOf = new Map(); // eventId → Set(clock root id)
      this.clockRootIds = [];
      this.activeIds = new Set(); // events carrying at least one trigger relation
      this.chainEndIds = new Set(); // events the chain stops at

      this.vertices = new Map(); // vertexId → drawn record
      this.vertexOf = new Map(); // eventId → vertexId, null when folded
      this.viewEdges = []; // { id, from, to, via, label }
      this.viewEdgeById = new Map();
      this.viewEdgeOf = new Map(); // event edge id → view edge id
      this.edgeWeight = new Map(); // view edge id → layout priority
      this.foldedCount = 0;

      this.groups = new Map(); // group key → { id, vertexIds, ownerIds, name }
      this.groupById = new Map();
      this.communityOf = null; // ownerId → community id, built on demand
      this.linkLength = null; // mean straight-line length of a drawn link
      this.groupingReport = null;

      this.currentGraph = null;
      this.level = "events";
      this.grouping = "node";
      this.colorBy = "owner";
      this.showUnlinked = false;
      this.traceMode = "both";
      this.legendOpen = false;
      this.selectedId = null;
      this.selectedVertexId = null;
      this.selectedOwnerId = null;

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
      this.instanceByPath.clear();
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
        if (instance.path) this.instanceByPath.set(instance.path, instance);
        (instance.in_ports || []).forEach((port) =>
          addEvent(port.event, instance, "input", port),
        );
        (instance.out_ports || []).forEach((port) =>
          addEvent(port.event, instance, "output", port),
        );
        (instance.events || []).forEach((event) =>
          addEvent(event, instance, "process", null),
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

      this.chainEndIds = new Set(
        [...this.activeIds].filter((id) => !(this.succ.get(id) || []).length),
      );

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

    // An `and` event fires at the slowest of its triggers, so triggers arriving
    // at different rates mean the declared rate cannot hold for all of them.
    _rateMismatch(event) {
      if (event.type !== "and") return null;
      const rates = new Set(
        (this.pred.get(event.id) || [])
          .map((id) => this.events.get(id)?.frequency)
          .filter((frequency) => frequency !== null && frequency !== undefined),
      );
      return rates.size > 1 ? [...rates].sort((a, b) => a - b) : null;
    }

    // ── View model ──────────────────────────────────────────────────────────────

    // Vertices of the current level and the trigger paths between them. An event
    // the level folds away is carried by the edge that runs through it, so the
    // relation it stands for survives the fold.
    buildView() {
      const level = LEVELS[this.level];
      this.vertices = new Map();
      this.vertexOf = new Map();
      this.viewEdges = [];
      this.viewEdgeById = new Map();
      this.viewEdgeOf = new Map();
      this.communityOf = null;
      this.foldedCount = 0;

      this.events.forEach((event, id) => {
        if (!this._isVisible(id)) return;
        if (level.transparent(event)) {
          this.vertexOf.set(id, null);
          this.foldedCount += 1;
          return;
        }
        const vertexId = level.vertexOf(event);
        this.vertexOf.set(id, vertexId);
        if (!this.vertices.has(vertexId)) {
          this.vertices.set(vertexId, { id: vertexId, eventIds: [] });
        }
        this.vertices.get(vertexId).eventIds.push(id);
      });

      const edgeByKey = new Map();
      const connect = (fromId, toId, sourceEventId, via, eventEdges) => {
        if (fromId === toId) return;
        const key = `${fromId}>${toId}`;
        let edge = edgeByKey.get(key);
        if (!edge) {
          edge = {
            id: `lv_${this.viewEdges.length}`,
            from: fromId,
            to: toId,
            sourceEventId,
            via: [],
          };
          edgeByKey.set(key, edge);
          this.viewEdges.push(edge);
          this.viewEdgeById.set(edge.id, edge);
        }
        via.forEach((id) => {
          if (!edge.via.includes(id)) edge.via.push(id);
        });
        eventEdges.forEach((id) => this.viewEdgeOf.set(id, edge.id));
      };

      this.vertexOf.forEach((vertexId, eventId) => {
        if (vertexId === null) return;
        const stack = [{ id: eventId, via: [], edges: [] }];
        const seen = new Set();
        while (stack.length) {
          const step = stack.pop();
          (this.succ.get(step.id) || []).forEach((nextId) => {
            if (!this.vertexOf.has(nextId)) return;
            const eventEdgeId = this.edgeIdByKey.get(`${step.id}>${nextId}`);
            const edges = eventEdgeId
              ? [...step.edges, eventEdgeId]
              : step.edges;
            const target = this.vertexOf.get(nextId);
            if (target !== null) {
              connect(vertexId, target, eventId, step.via, edges);
              return;
            }
            if (seen.has(nextId)) return;
            seen.add(nextId);
            stack.push({ id: nextId, via: [...step.via, nextId], edges });
          });
        }
      });

      this.vertices.forEach((vertex) => this._decorateVertex(vertex));
      this._assignGroups();
      this.vertices.forEach((vertex) => this._labelVertex(vertex));
      this.viewEdges.forEach((edge) => this._labelEdge(edge));
      this._weighEdges();
    }

    // What a link weighs: the hops of chain reaching it plus the hops it reaches
    // before the chain ends. The links of the longest chain weigh most, and the
    // layout spends its freedom on the heaviest links first.
    _weighEdges() {
      const out = new Map();
      const into = new Map();
      this.vertices.forEach((_, id) => {
        out.set(id, []);
        into.set(id, []);
      });
      this.viewEdges.forEach((edge) => {
        out.get(edge.from).push(edge.to);
        into.get(edge.to).push(edge.from);
      });

      // Hops from the vertices the walk starts at, which are the ones no link
      // enters from the opposite side; a vertex held in a cycle none of them
      // reaches keeps no depth.
      const depths = (adjacency, opposite) => {
        const depth = new Map();
        const queue = [];
        this.vertices.forEach((_, id) => {
          if (opposite.get(id).length) return;
          depth.set(id, 0);
          queue.push(id);
        });
        for (let cursor = 0; cursor < queue.length; cursor += 1) {
          const id = queue[cursor];
          adjacency.get(id).forEach((next) => {
            if (depth.has(next)) return;
            depth.set(next, depth.get(id) + 1);
            queue.push(next);
          });
        }
        return depth;
      };

      const upstream = depths(out, into);
      const downstream = depths(into, out);

      this.edgeWeight = new Map();
      this.viewEdges.forEach((edge) => {
        this.edgeWeight.set(
          edge.id,
          (upstream.get(edge.from) ?? 0) + (downstream.get(edge.to) ?? 0) + 1,
        );
      });
    }

    // ── Grouping ────────────────────────────────────────────────────────────────

    // Every vertex takes the key of the box it is drawn in; a null key stays a
    // free row of the drawing.
    _assignGroups() {
      this.groups = new Map();
      this.groupById = new Map();
      const keyOf = GROUPINGS[this.grouping].keyOf;

      this.vertices.forEach((vertex) => {
        const key =
          this.level === "nodes" && this.grouping === "node"
            ? null
            : keyOf(this, vertex);
        vertex.groupKey = key;
        if (key === null) return;
        let group = this.groups.get(key);
        if (!group) {
          group = {
            key,
            id: `${GROUP.prefix}${key}`,
            vertexIds: [],
            ownerIds: new Set(),
          };
          this.groups.set(key, group);
          this.groupById.set(group.id, group);
        }
        group.vertexIds.push(vertex.id);
        group.ownerIds.add(vertex.ownerId);
      });

      this.groups.forEach((group) => this._nameGroup(group));
    }

    // The namespace prefix a node sits under.
    _componentOf(ownerId) {
      const path = this.instances.get(ownerId)?.data.path || "";
      return `/${path.split("/").filter(Boolean).slice(0, COMPONENT_DEPTH).join("/")}`;
    }

    // What a box is called and which instance lends it its color: the node
    // itself, or the component the nodes in it have in common.
    _nameGroup(group) {
      const ownerIds = [...group.ownerIds];
      if (this.grouping === "node") {
        const instance = this.instances.get(ownerIds[0])?.data || {};
        group.instance = instance;
        group.name = instance.name || ownerIds[0];
        group.detail = instance.path || "";
        return;
      }

      const counts = new Map();
      ownerIds.forEach((ownerId) => {
        const component = this._componentOf(ownerId);
        counts.set(component, (counts.get(component) || 0) + 1);
      });
      const [component] = [...counts].sort((a, b) => b[1] - a[1])[0];
      group.instance = this.instanceByPath.get(component) || {};
      group.detail = component;
      group.name =
        this.grouping === "component"
          ? component
          : `${component} · ${ownerIds.length} node${ownerIds.length > 1 ? "s" : ""}`;
    }

    // Modularity communities over the node boxes, from the links between them:
    // the cut that keeps the busiest links inside one box.
    _communities() {
      if (this.communityOf) return this.communityOf;

      const neighbors = new Map();
      const degree = new Map();
      let links = 0;
      const add = (a, b) => {
        if (!neighbors.has(a)) neighbors.set(a, new Map());
        neighbors.get(a).set(b, (neighbors.get(a).get(b) || 0) + 1);
        degree.set(a, (degree.get(a) || 0) + 1);
      };
      this.viewEdges.forEach((edge) => {
        const a = this.vertices.get(edge.from).ownerId;
        const b = this.vertices.get(edge.to).ownerId;
        if (a === b) return;
        add(a, b);
        add(b, a);
        links += 1;
      });

      const community = new Map();
      this.vertices.forEach((vertex) =>
        community.set(vertex.ownerId, vertex.ownerId),
      );
      if (!links) {
        this.communityOf = community;
        return community;
      }

      // Greedy modularity: a node joins the neighbouring community that gains
      // the most, repeated until no move pays off.
      const m2 = links * 2;
      const communityDegree = new Map();
      community.forEach((key, ownerId) =>
        communityDegree.set(
          key,
          (communityDegree.get(key) || 0) + (degree.get(ownerId) || 0),
        ),
      );

      for (let round = 0; round < 20; round += 1) {
        let moved = false;
        community.forEach((own, ownerId) => {
          const k = degree.get(ownerId) || 0;
          communityDegree.set(own, communityDegree.get(own) - k);

          const weights = new Map();
          (neighbors.get(ownerId) || new Map()).forEach((weight, other) => {
            const key = community.get(other);
            weights.set(key, (weights.get(key) || 0) + weight);
          });

          let best = own;
          let bestGain =
            (weights.get(own) || 0) -
            ((communityDegree.get(own) || 0) * k) / m2;
          weights.forEach((weight, key) => {
            const gain = weight - ((communityDegree.get(key) || 0) * k) / m2;
            if (gain > bestGain + 1e-9) {
              bestGain = gain;
              best = key;
            }
          });

          communityDegree.set(best, (communityDegree.get(best) || 0) + k);
          if (best !== own) {
            community.set(ownerId, best);
            moved = true;
          }
        });
        if (!moved) break;
      }

      this.communityOf = community;
      return community;
    }

    // The relation an edge stands for, named once: the first event it folds, or
    // its source event when the source vertex does not already carry that name.
    _labelEdge(edge) {
      const source = this.events.get(edge.via[0] ?? edge.sourceEventId);
      const label = this._shortLabel(this._bareName(source.name));
      edge.label = label === this.vertices.get(edge.from).label ? "" : label;
    }

    _decorateVertex(vertex) {
      if (this.level === "nodes") {
        const instance = this.instances.get(vertex.id)?.data || {};
        vertex.kind = "instance";
        vertex.type = null;
        vertex.ownerId = vertex.id;
        vertex.name = instance.name || vertex.id;
        vertex.detail = instance.path || "";
        vertex.frequency = null;
        vertex.sub = this._rateSpan(vertex.eventIds);
        vertex.clocked = vertex.eventIds.some((id) => this.clocksOf.has(id));
        vertex.mismatch = vertex.eventIds.some((id) =>
          this._rateMismatch(this.events.get(id)),
        );
      } else {
        const event = this.events.get(vertex.eventIds[0]);
        vertex.kind = event.kind;
        vertex.type = event.type;
        vertex.ownerId = event.ownerId;
        vertex.name = this._bareName(event.name);
        vertex.detail = this.instances.get(event.ownerId)?.data.path || "";
        vertex.frequency = event.frequency;
        vertex.sub = this.rateLabel(event.frequency);
        vertex.clocked = this.clocksOf.has(event.id);
        vertex.mismatch = Boolean(this._rateMismatch(event));
      }
    }

    // A row carries the name of the node owning it unless the box around it
    // already does.
    _labelVertex(vertex) {
      const owner = this.instances.get(vertex.ownerId)?.data;
      if (
        vertex.groupKey === vertex.ownerId ||
        vertex.kind === "instance" ||
        !owner?.name
      ) {
        vertex.label = this._shortLabel(vertex.name);
      } else {
        // Both names are cut on the left, so each keeps the end that tells the
        // two rows of one node apart.
        const event = this._shortLabel(vertex.name, VIEW.eventChars);
        vertex.label = `${this._shortLabel(
          owner.name,
          Math.max(6, VIEW.labelChars - event.length - 1),
        )}/${event}`;
      }
      vertex.width = Math.round(
        VIEW.padX * 2 +
          VIEW.glyphW +
          VIEW.gap +
          this.measureTextWidth(vertex.label, VIEW.fontSize) +
          (vertex.sub
            ? VIEW.gap + this.measureTextWidth(vertex.sub, VIEW.subSize)
            : 0) +
          (vertex.mismatch ? VIEW.gap + VIEW.markW : 0),
      );
    }

    // The rates an instance's own events run at, as one span.
    _rateSpan(eventIds) {
      const rates = [
        ...new Set(
          eventIds
            .map((id) => this.events.get(id).frequency)
            .filter((frequency) => frequency),
        ),
      ].sort((a, b) => a - b);
      if (!rates.length) return "";
      if (rates.length === 1) return this.rateLabel(rates[0]);
      return `${this.rateLabel(rates[0])}–${this.rateLabel(rates[rates.length - 1])}`;
    }

    // ── Labels ──────────────────────────────────────────────────────────────────

    rateLabel(frequency) {
      if (frequency === null || frequency === undefined) return "";
      if (frequency === 0) return "once";
      return `${Number(frequency.toFixed(3))}Hz`;
    }

    // The port kind is already carried by the glyph.
    _bareName(name) {
      return name.replace(/^(input|output)_/, "");
    }

    // A name too long to draw keeps its tail: the leading namespace is the part
    // its neighbours in the chain repeat. What is left of the budget is filled
    // with the segment before it, cut on the left.
    _shortLabel(name, limit = VIEW.labelChars) {
      if (name.length <= limit) return name;
      const budget = limit - 1;
      const segments = name.split("/");
      let tail = segments.pop();
      if (tail.length >= budget) {
        return `…${tail.slice(tail.length - budget)}`;
      }
      while (segments.length) {
        const next = segments.pop();
        if (next.length + 1 + tail.length > budget) {
          const room = budget - tail.length - 1;
          return `…${next.slice(next.length - room)}/${tail}`;
        }
        tail = `${next}/${tail}`;
      }
      return `…${tail}`;
    }

    // ── ELK graph ───────────────────────────────────────────────────────────────

    // The drawing has a single scale, so every metric the canvas asks for is the
    // same whatever depth it asks about.
    getLayerStyle() {
      return {
        cornerR: 3,
        borderW: "1",
        edgeW: "0.6",
        arrowW: "3.6",
        arrowH: "2.6",
        fontSize: VIEW.fontSize,
        nsSize: VIEW.subSize,
      };
    }

    vertexNode(vertex) {
      return { id: vertex.id, width: vertex.width, height: VIEW.rowH };
    }

    // The weight reaches the layout as edge priority: node placement aligns the
    // ends of a heavy link, and a cycle is cut at the lightest link in it.
    edgeLink(edge, source = edge.from, target = edge.to) {
      const priority = String(this.edgeWeight.get(edge.id) ?? 1);
      return {
        id: edge.id,
        sources: [source],
        targets: [target],
        layoutOptions: {
          "org.eclipse.elk.layered.priority.direction": priority,
          "org.eclipse.elk.layered.priority.straightness": priority,
        },
      };
    }

    buildFlatGraph() {
      return {
        id: "logic-view",
        children: [...this.vertices.values()].map((vertex) =>
          this.vertexNode(vertex),
        ),
        edges: this.viewEdges.map((edge) => this.edgeLink(edge)),
      };
    }

    // ── Layout + render ─────────────────────────────────────────────────────────

    layoutOptions() {
      return {
        algorithm: "layered",
        "org.eclipse.elk.direction": "RIGHT",
        "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
        // Every edge still runs left to right, so the horizontal axis reads as
        // causal order; the strategy picks the layer that keeps it shortest.
        "org.eclipse.elk.layered.layering.strategy": "NETWORK_SIMPLEX",
        // Boxes joined by a chain are placed as one segment, which pulls the
        // stack together without costing the link a corner. The strategies that
        // pack tighter cost either the corners or the layout time.
        "org.eclipse.elk.layered.nodePlacement.strategy": "LINEAR_SEGMENTS",
        "org.eclipse.elk.spacing.nodeNode": String(VIEW.nodeSpacing),
        "org.eclipse.elk.layered.spacing.nodeNodeBetweenLayers": String(
          VIEW.layerSpacing,
        ),
        // Clearance around an edge route is what holds rows apart, so it stays
        // near zero and the placed graph is compacted instead.
        "org.eclipse.elk.spacing.edgeNode": String(VIEW.edgeSpacing),
        "org.eclipse.elk.layered.spacing.edgeNodeBetweenLayers": String(
          VIEW.edgeSpacing,
        ),
        "org.eclipse.elk.spacing.edgeEdge": String(VIEW.edgeSpacing),
        "org.eclipse.elk.layered.spacing.edgeEdgeBetweenLayers": String(
          VIEW.edgeSpacing,
        ),
        "org.eclipse.elk.layered.compaction.postCompaction.strategy": "LEFT",
        "org.eclipse.elk.layered.compaction.postCompaction.constraints":
          "QUADRATIC",
        "org.eclipse.elk.padding": "[top=20,left=20,bottom=20,right=20]",
        // Chains that never meet are packed to this shape instead of stacking
        // into one column.
        "org.eclipse.elk.separateConnectedComponents": "true",
        "org.eclipse.elk.aspectRatio": String(VIEW.aspectRatio),
        "org.eclipse.elk.spacing.componentComponent": "14",
      };
    }

    async layoutAndRender() {
      this.maxDepth = 0;
      this.buildView();
      const graph = await this.layoutView();
      this.linkLength = this.measureLinks(graph);
      this.currentGraph = graph;
      this.render(graph);
      this.fitToScreen();
    }

    // Each box is laid out on its own, then the boxes and the rows outside them
    // are laid out by the trigger relations running between them: causal order
    // is read box to box, and inside a box by the triggers its own events share.
    // The boxes carry the crossing links on ports pinned to the row each one
    // belongs to. With no box at all this collapses to one pass over the rows.
    async layoutView() {
      if (!this.groups.size) {
        return this.elk.layout(this.buildFlatGraph(), {
          layoutOptions: this.layoutOptions(),
        });
      }

      const groupIdOf = (vertexId) => {
        const key = this.vertices.get(vertexId).groupKey;
        return key === null ? null : this.groups.get(key).id;
      };
      const crossing = this.viewEdges.filter(
        (edge) =>
          groupIdOf(edge.from) === null ||
          groupIdOf(edge.to) === null ||
          groupIdOf(edge.from) !== groupIdOf(edge.to),
      );

      const sides = new Map(); // vertexId → Set("EAST" | "WEST")
      // A row inside a box is reached through a port on the box; a free row is
      // a child of the drawing and carries the link itself.
      const endpoint = (vertexId, side) => {
        if (groupIdOf(vertexId) === null) return vertexId;
        if (!sides.has(vertexId)) sides.set(vertexId, new Set());
        sides.get(vertexId).add(side);
        return `${vertexId}@${side}`;
      };
      const portOf = new Map();
      crossing.forEach((edge) =>
        portOf.set(edge.id, {
          source: endpoint(edge.from, "EAST"),
          target: endpoint(edge.to, "WEST"),
        }),
      );

      const groups = await Promise.all(
        [...this.groups.values()].map((group) =>
          this.layoutGroup(group, sides),
        ),
      );
      const free = [...this.vertices.values()]
        .filter((vertex) => vertex.groupKey === null)
        .map((vertex) => this.vertexNode(vertex));

      // The boxes go into the second pass without their content, so the pass
      // places them without relaying out what they hold.
      const laid = await this.elk.layout(
        {
          id: "logic-view",
          children: [...groups.map((group) => group.box), ...free],
          edges: crossing.map((edge) =>
            this.edgeLink(
              edge,
              portOf.get(edge.id).source,
              portOf.get(edge.id).target,
            ),
          ),
        },
        { layoutOptions: this.layoutOptions() },
      );

      const contentOf = new Map(
        groups.map((group) => [group.box.id, group.content]),
      );
      (laid.children || []).forEach((box) => {
        const content = contentOf.get(box.id);
        if (!content) return;
        box.children = content.children;
        box.edges = content.edges;
      });
      return laid;
    }

    // Mean straight-line distance between the rows a link joins: what a cut into
    // boxes costs, in the unit the drawing is read in.
    measureLinks(graph) {
      const at = new Map();
      const walk = (node, ox, oy) => {
        const x = ox + (node.x || 0);
        const y = oy + (node.y || 0);
        if (this.vertices.has(node.id)) at.set(node.id, { x, y });
        (node.children || []).forEach((child) => walk(child, x, y));
      };
      walk(graph, 0, 0);

      let total = 0;
      let counted = 0;
      this.viewEdges.forEach((edge) => {
        const from = at.get(edge.from);
        const to = at.get(edge.to);
        if (!from || !to) return;
        const fromWidth = this.vertices.get(edge.from).width;
        const toWidth = this.vertices.get(edge.to).width;
        total += Math.hypot(
          to.x + toWidth / 2 - from.x - fromWidth / 2,
          to.y - from.y,
        );
        counted += 1;
      });
      return counted ? Math.round(total / counted) : 0;
    }

    async layoutGroup(group, sides) {
      const ids = new Set(group.vertexIds);
      const inner = await this.elk.layout(
        {
          id: group.id,
          children: group.vertexIds.map((id) =>
            this.vertexNode(this.vertices.get(id)),
          ),
          edges: this.viewEdges
            .filter((edge) => ids.has(edge.from) && ids.has(edge.to))
            .map((edge) => this.edgeLink(edge)),
        },
        {
          layoutOptions: {
            algorithm: "layered",
            "org.eclipse.elk.direction": "RIGHT",
            "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
            // A row is aligned with the rows it triggers, which costs the box
            // height the layer with the most rows in it needs anyway.
            "org.eclipse.elk.layered.layering.strategy": "MIN_WIDTH",
            "org.eclipse.elk.spacing.nodeNode": String(GROUP.rowSpacing),
            "org.eclipse.elk.layered.spacing.nodeNodeBetweenLayers": String(
              GROUP.layerSpacing,
            ),
            "org.eclipse.elk.spacing.edgeNode": String(GROUP.edgeSpacing),
            "org.eclipse.elk.layered.spacing.edgeNodeBetweenLayers": String(
              GROUP.edgeSpacing,
            ),
            "org.eclipse.elk.spacing.edgeEdge": String(GROUP.edgeSpacing),
            "org.eclipse.elk.layered.spacing.edgeEdgeBetweenLayers": String(
              GROUP.edgeSpacing,
            ),
            "org.eclipse.elk.layered.nodePlacement.bk.edgeStraightening":
              "NONE",
            "org.eclipse.elk.layered.compaction.postCompaction.strategy":
              "LEFT",
            "org.eclipse.elk.layered.compaction.postCompaction.constraints":
              "QUADRATIC",
            "org.eclipse.elk.padding": `[top=${GROUP.padTop},left=${GROUP.pad},bottom=${GROUP.pad},right=${GROUP.pad}]`,
          },
        },
      );

      const name = this._shortLabel(group.name, GROUP.nameChars);
      const width = Math.max(
        inner.width,
        this.measureTextWidth(name, GROUP.fontSize) + GROUP.pad * 4,
      );

      const ports = [];
      (inner.children || []).forEach((child) => {
        (sides.get(child.id) || []).forEach((side) =>
          ports.push({
            id: `${child.id}@${side}`,
            width: 1,
            height: 1,
            x: side === "WEST" ? 0 : width,
            y: (child.y || 0) + VIEW.rowH / 2,
            properties: { "org.eclipse.elk.port.side": side },
          }),
        );
      });

      return {
        box: {
          id: group.id,
          width,
          height: inner.height,
          labels: [{ text: name }],
          ports,
          properties: { "org.eclipse.elk.portConstraints": "FIXED_POS" },
        },
        content: { children: inner.children || [], edges: inner.edges || [] },
      };
    }

    // ELK reports a node in the coordinates of the box holding it; the drawing
    // is flat, so every position is resolved to the root before anything is
    // drawn and the layers then stack boxes, edges and vertices in that order.
    render(graph) {
      const { layer } = this.createCanvas();
      this.container.classList.add("logic-diagram-container");
      this.selectedId = null;
      this.selectedVertexId = null;
      this.selectedOwnerId = null;
      this.groupRects = new Map();

      this.groupLayer = document.createElementNS(SVG_NS, "g");
      this.edgeLayer = document.createElementNS(SVG_NS, "g");
      this.vertexLayer = document.createElementNS(SVG_NS, "g");
      this.edgeLabelLayer = document.createElementNS(SVG_NS, "g");
      layer.appendChild(this.groupLayer);
      layer.appendChild(this.edgeLayer);
      layer.appendChild(this.vertexLayer);
      layer.appendChild(this.edgeLabelLayer);

      const origins = new Map();
      const groups = [];
      const vertices = [];
      const edges = [];

      const walk = (node, ox, oy) => {
        const origin = { x: ox + (node.x || 0), y: oy + (node.y || 0) };
        origins.set(node.id, origin);
        if (this.vertices.has(node.id)) vertices.push({ node, origin });
        else if (node.id !== graph.id) groups.push({ node, origin });
        (node.edges || []).forEach((laid) => edges.push({ laid, node }));
        (node.children || []).forEach((child) =>
          walk(child, origin.x, origin.y),
        );
      };
      walk(graph, 0, 0);

      groups.forEach(({ node, origin }) =>
        this.groupLayer.appendChild(this.buildGroup(node, origin)),
      );
      edges.forEach(({ laid, node }) => {
        const origin = origins.get(laid.container) || origins.get(node.id);
        const path = this.buildEdgePath(laid, origin);
        if (path) this.edgeLayer.appendChild(path);
      });
      vertices.forEach(({ node, origin }) =>
        this.vertexLayer.appendChild(this.buildVertex(node, origin)),
      );

      this.renderToolbar();
      this.applyLOD();
    }

    // The box naming what the events drawn in it have in common.
    buildGroup(node, origin) {
      const group = this.groupById.get(node.id);
      const instance = group?.instance || {};
      const guide = instance.vis_guide;
      const defaults = this.isDarkMode()
        ? this.styleDefaults.dark
        : this.styleDefaults.light;

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("id", node.id);
      g.setAttribute("transform", `translate(${origin.x},${origin.y})`);
      g.classList.add("logic-group");

      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("width", node.width || 0);
      rect.setAttribute("height", node.height || 0);
      rect.setAttribute("rx", GROUP.pad);
      rect.setAttribute(
        "fill",
        this.themed(guide, "background_color", defaults.bg),
      );
      rect.setAttribute("stroke", this.themed(guide, "color", defaults.stroke));
      rect.classList.add("logic-group-rect");
      g.appendChild(rect);
      this.groupRects.set(group?.key, rect);

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = `${group?.detail || group?.name || node.id}\n${
        node.children?.length || 0
      } events`;
      g.appendChild(title);

      const name = document.createElementNS(SVG_NS, "text");
      name.setAttribute("x", (node.width || 0) / 2);
      name.setAttribute("y", GROUP.padTop / 2 + 2);
      name.setAttribute("text-anchor", "middle");
      name.setAttribute("dominant-baseline", "central");
      name.textContent = node.labels?.[0]?.text || "";
      name.classList.add("logic-group-label");
      name.style.fontSize = `${GROUP.fontSize}px`;
      name.style.fill = this.themed(
        guide,
        "text_color",
        this.isDarkMode() ? "#e9ecef" : "#333",
      );
      g.appendChild(name);

      g.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.traceGroup(group?.key);
      };

      return g;
    }

    // A vertex is one row: the glyph carries the trigger semantics, the outline
    // the instance that owns it, and the trailing badge the rate.
    buildVertex(node, origin) {
      const vertex = this.vertices.get(node.id);
      const style = this.getLayerStyle();
      const guide = this.instances.get(vertex.ownerId)?.data.vis_guide;
      const defaults = this.isDarkMode()
        ? this.styleDefaults.dark
        : this.styleDefaults.light;

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("id", node.id);
      g.setAttribute("transform", `translate(${origin.x},${origin.y})`);
      g.classList.add("logic-vertex");
      if (!vertex.clocked) g.classList.add("logic-unclocked");
      g.style.cursor = "pointer";

      const body = document.createElementNS(SVG_NS, "rect");
      body.setAttribute("width", node.width);
      body.setAttribute("height", node.height);
      body.setAttribute("rx", style.cornerR);
      body.setAttribute("fill", this.vertexFill(vertex, guide, defaults));
      body.setAttribute("stroke", this.themed(guide, "color", defaults.stroke));
      body.setAttribute("stroke-width", style.borderW);
      body.classList.add("logic-vertex-body");
      g.appendChild(body);

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeVertex(vertex);
      g.appendChild(title);

      const glyph = this.buildVertexGlyph(vertex, style);
      glyph.setAttribute(
        "transform",
        `translate(${VIEW.padX},${(node.height - VIEW.glyphH) / 2})`,
      );
      g.appendChild(glyph);

      const textColor = this.themed(
        guide,
        "text_color",
        this.isDarkMode() ? "#e9ecef" : "#333",
      );

      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", VIEW.padX + VIEW.glyphW + VIEW.gap);
      label.setAttribute("y", node.height / 2);
      label.setAttribute("dominant-baseline", "central");
      label.textContent = vertex.label;
      label.classList.add("logic-vertex-label");
      label.style.fontSize = `${VIEW.fontSize}px`;
      label.style.fill = textColor;
      g.appendChild(label);

      if (vertex.sub) {
        const sub = document.createElementNS(SVG_NS, "text");
        sub.setAttribute(
          "x",
          node.width -
            VIEW.padX -
            (vertex.mismatch ? VIEW.markW + VIEW.gap : 0),
        );
        sub.setAttribute("y", node.height / 2);
        sub.setAttribute("text-anchor", "end");
        sub.setAttribute("dominant-baseline", "central");
        sub.textContent = vertex.sub;
        sub.classList.add("logic-vertex-sub");
        sub.style.fontSize = `${VIEW.subSize}px`;
        g.appendChild(sub);
      }

      if (vertex.mismatch) {
        const mark = document.createElementNS(SVG_NS, "polygon");
        const x = node.width - VIEW.padX - VIEW.markW;
        const y = (node.height - VIEW.markW) / 2;
        mark.setAttribute(
          "points",
          `${x},${y + VIEW.markW} ${x + VIEW.markW},${y + VIEW.markW} ${x + VIEW.markW / 2},${y}`,
        );
        mark.classList.add("logic-rate-mismatch");
        g.appendChild(mark);
      }

      g.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.traceVertex(node.id);
      };

      return g;
    }

    vertexFill(vertex, guide, defaults) {
      if (this.colorBy === "rate") {
        return this.rateColor(vertex.frequency) || defaults.bg;
      }
      return this.themed(guide, "medium_color", defaults.nodeBg);
    }

    // Rate as a hue, so the pace of a chain survives a zoom level the labels do
    // not: slow is blue, fast is red, unclocked keeps the plain background.
    rateColor(frequency) {
      if (!frequency) return frequency === 0 ? "#9aa0a6" : null;
      const t = Math.min(1, Math.max(0, (Math.log10(frequency) + 1) / 3));
      const light = this.isDarkMode() ? 34 : 76;
      return `hsl(${Math.round(210 - 210 * t)}, 62%, ${light}%)`;
    }

    buildVertexGlyph(vertex, style) {
      if (vertex.kind === "process" || vertex.kind === "instance") {
        const shape = this.buildTypeShape(
          vertex.type,
          VIEW.glyphW,
          VIEW.glyphH,
          style,
        );
        shape.classList.add("logic-process");
        if (vertex.kind === "process" && !vertex.type) {
          shape.classList.add("logic-process-unknown");
        }
        return shape;
      }
      return this.buildPortGlyph(vertex.kind);
    }

    // Port events are the boundary of a node: the chevron points the way the
    // message travels, so an input and an output read the same on either side.
    buildPortGlyph(kind) {
      const glyph = document.createElementNS(SVG_NS, "polygon");
      glyph.setAttribute(
        "points",
        `0,0 ${VIEW.glyphW},${VIEW.glyphH / 2} 0,${VIEW.glyphH}`,
      );
      glyph.classList.add("logic-event", `logic-event-${kind}`);
      return glyph;
    }

    // Process-event outlines: `and` closes on a single arc, `or` on a concave
    // back, a clock is a pill and `once` a tag; every other type is a plain box.
    buildTypeShape(type, w, h, style) {
      const shape = TYPE_SHAPES[type];
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

    buildEdgePath(laidEdge, origin = { x: 0, y: 0 }) {
      if (!laidEdge.sections) return null;
      const edge = this.viewEdgeById.get(laidEdge.id);
      if (!edge) return null;
      const style = this.getLayerStyle();

      const at = (point) => `${point.x + origin.x} ${point.y + origin.y}`;
      let d = "";
      laidEdge.sections.forEach((section) => {
        d += `M ${at(section.startPoint)} `;
        (section.bendPoints || []).forEach((bp) => (d += `L ${at(bp)} `));
        d += `L ${at(section.endPoint)} `;
      });

      const from = this.vertices.get(edge.from);
      const to = this.vertices.get(edge.to);
      const crossesInstance = from.ownerId !== to.ownerId;

      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("id", laidEdge.id);
      path.setAttribute("d", d);
      path.setAttribute("stroke-width", style.edgeW);
      path.setAttribute("marker-end", "url(#arrowhead-depth-0)");
      path.classList.add("edge-path", "logic-edge");
      path.classList.add(
        crossesInstance ? "logic-edge-link" : "logic-edge-trigger",
      );
      if (!to.clocked) path.classList.add("logic-unclocked");
      if (!crossesInstance) {
        const w = parseFloat(style.edgeW);
        path.setAttribute("stroke-dasharray", `${w * 4} ${w * 3}`);
      }

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeEdge(edge);
      path.appendChild(title);

      path.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.traceVertex(edge.to);
      };

      return path;
    }

    updateTheme() {
      this.redraw();
    }

    // Draws the current layout again; the trace the selection stands for is
    // restored, since the drawing it marked is replaced.
    redraw() {
      if (!this.currentGraph) return;
      const selection = {
        eventId: this.selectedId,
        vertexId: this.selectedVertexId,
        ownerId: this.selectedOwnerId,
      };
      this.render(this.currentGraph);
      this.selectedId = selection.eventId;
      this.selectedVertexId = selection.vertexId;
      this.selectedOwnerId = selection.ownerId;
      this.retrace();
    }

    // ── Level of detail ─────────────────────────────────────────────────────────

    // Label detail follows the viewport scale: what is too small to read is not
    // drawn, so the glyph and the owner color carry the widest view.
    applyLOD() {
      const root = this.currentSvgRoot;
      if (!root) return;
      root.classList.toggle("lod-node", this.transform.k >= LOD_NODE);
      root.classList.toggle("lod-name", this.transform.k >= LOD_NAME);
      root.classList.toggle("lod-rate", this.transform.k >= LOD_RATE);
    }

    onTransform() {
      this.applyLOD();
    }

    // ── Chain tracing ───────────────────────────────────────────────────────────

    // Walks the trigger relation in one direction and returns the events reached,
    // in hop order, together with the edges the walk used.
    walkChain(startIds, adjacency) {
      const order = [];
      const hops = new Map(startIds.map((id) => [id, 0]));
      const edges = new Set();
      const queue = [...startIds];
      const seen = new Set(startIds);

      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const id = queue[cursor];
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

    _trace(startIds) {
      const empty = { order: [], hops: new Map(), edges: new Set() };
      const upstream =
        this.traceMode === "down" ? empty : this.walkChain(startIds, this.pred);
      const downstream =
        this.traceMode === "up" ? empty : this.walkChain(startIds, this.succ);

      this.clearHighlights();
      upstream.order.forEach((id) => this.highlightEvent(id, UPSTREAM_COLOR));
      upstream.edges.forEach((id) => this.highlightEdge(id, UPSTREAM_COLOR));
      downstream.order.forEach((id) =>
        this.highlightEvent(id, DOWNSTREAM_COLOR),
      );
      downstream.edges.forEach((id) =>
        this.highlightEdge(id, DOWNSTREAM_COLOR),
      );
      startIds.forEach((id) => this.highlightEvent(id, "default"));
      return { upstream, downstream };
    }

    // Repeats the current selection under a changed trace mode.
    retrace() {
      if (this.selectedVertexId) this.traceVertex(this.selectedVertexId);
      else if (this.selectedOwnerId) this.traceGroup(this.selectedOwnerId);
      else if (this.selectedId) this.traceFrom(this.selectedId);
    }

    traceFrom(eventId) {
      const event = this.events.get(eventId);
      if (!event) return;
      const { upstream, downstream } = this._trace([eventId]);
      this.selectedId = eventId;
      this.selectedVertexId = this.vertexOf.get(eventId) ?? null;
      this.selectedOwnerId = null;
      this.updateInfoPanel(
        this.describeChain(event, upstream, downstream),
        "Event",
      );
    }

    // A vertex standing for a whole instance traces the chains all of its events
    // take part in.
    traceVertex(vertexId) {
      const vertex = this.vertices.get(vertexId);
      if (!vertex) return;
      if (vertex.eventIds.length === 1) {
        this.traceFrom(vertex.eventIds[0]);
        return;
      }

      this._traceOwned(vertex.groupKey, vertex.eventIds);
      this.selectedVertexId = vertexId;
      this.selectedOwnerId = null;
    }

    // The box traces every chain the events drawn in it take part in.
    traceGroup(groupKey) {
      const group = this.groups.get(groupKey);
      if (!group) return;
      const eventIds = group.vertexIds.flatMap(
        (id) => this.vertices.get(id).eventIds,
      );
      if (eventIds.length) this._traceOwned(groupKey, eventIds);
    }

    _traceOwned(groupKey, eventIds) {
      const { upstream, downstream } = this._trace(eventIds);
      this.selectedId = eventIds[0];
      this.selectedVertexId = null;
      this.selectedOwnerId = groupKey;
      const group = this.groups.get(groupKey);
      this.updateInfoPanel(
        {
          ...(group?.instance || {}),
          name: group?.name || groupKey,
          nodes: group ? group.ownerIds.size : 1,
          chain: this.chainReport(
            upstream,
            downstream,
            this.clockIds(eventIds),
          ),
        },
        "Node",
      );
    }

    describeVertex(vertex) {
      if (vertex.kind === "instance") {
        return (
          `${vertex.detail || vertex.name}\n` +
          `${vertex.eventIds.length} events\n` +
          `${vertex.sub || "no clock"}`
        );
      }
      return this.describeEvent(
        this.events.get(vertex.eventIds[0]),
        this._rateMismatch(this.events.get(vertex.eventIds[0])),
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

    describeEdge(edge) {
      const head = `${this.vertices.get(edge.from).name} → ${this.vertices.get(edge.to).name}`;
      if (!edge.via.length) return head;
      return `${head}\n${edge.via.map((id) => this.events.get(id).name).join("\n")}`;
    }

    clockIds(eventIds) {
      const clocks = new Set();
      eventIds.forEach((id) =>
        (this.clocksOf.get(id) || []).forEach((clockId) => clocks.add(clockId)),
      );
      return [...clocks];
    }

    chainReport(upstream, downstream, clockIds, extra = {}) {
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

      return {
        clocks: clockIds.map((id) => {
          const clock = this.events.get(id);
          const instance = this.instances.get(clock.ownerId)?.data || {};
          return {
            name: clock.name,
            path: instance.path || "",
            rate: this.rateLabel(clock.frequency) || "no clock",
          };
        }),
        upstream: list(upstream),
        downstream: list(downstream),
        upstream_total: upstream.order.length,
        downstream_total: downstream.order.length,
        limit: CHAIN_LIST_LIMIT,
        ...extra,
      };
    }

    describeChain(event, upstream, downstream) {
      const owner = this.instances.get(event.ownerId)?.data || {};
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
        chain: this.chainReport(
          upstream,
          downstream,
          this.clockIds([event.id]),
        ),
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
        if (el.tagName === "path") {
          el.setAttribute("marker-end", "url(#arrowhead-depth-0)");
        }
      });
      if (this.edgeLabelLayer) this.edgeLabelLayer.innerHTML = "";
      this.selectedId = null;
      this.selectedVertexId = null;
      this.selectedOwnerId = null;
    }

    highlightEvent(eventId, preset) {
      const vertexId = this.vertexOf.get(eventId);
      if (!vertexId) return;
      this.highlightVertex(vertexId, preset);
    }

    highlightVertex(vertexId, preset) {
      const color = this.colorPresets[preset]?.port;
      const body =
        this.elementById(vertexId)?.querySelector(".logic-vertex-body");
      if (!body || !color) return;
      body.classList.add("logic-highlighted");
      body.style.stroke = color;
      body.style.strokeWidth = "2px";
      this.markGroup(this.vertices.get(vertexId)?.groupKey, color);
    }

    // The box a chain passes through is marked once, by the first of the events
    // in it the walk reaches.
    markGroup(groupKey, color) {
      const rect = this.groupRects?.get(groupKey);
      if (!rect || rect.classList.contains("logic-highlighted")) return;
      rect.classList.add("logic-highlighted");
      rect.style.stroke = color;
      rect.style.strokeWidth = "1.5px";
    }

    // The events an edge folds away are named only while the chain is traced, so
    // the drawing carries the names without reserving room for them.
    highlightEdge(eventEdgeId, preset) {
      const viewEdgeId = this.viewEdgeOf.get(eventEdgeId);
      const path = this.elementById(viewEdgeId);
      const color = this.colorPresets[preset]?.edge;
      if (!path || !color || path.classList.contains("logic-highlighted")) {
        return;
      }

      path.classList.add("logic-highlighted");
      path.style.stroke = color;
      path.style.strokeWidth = (
        parseFloat(this.getLayerStyle().edgeW) * 3
      ).toFixed(1);
      path.setAttribute(
        "marker-end",
        `url(#arrowhead-highlighted-${preset}-depth-0)`,
      );
      if (path.parentNode) path.parentNode.appendChild(path);

      this.appendEdgeLabel(path, this.viewEdgeById.get(viewEdgeId), color);
    }

    appendEdgeLabel(path, edge, color) {
      if (!edge?.label || !this.edgeLabelLayer) return;
      const length = path.getTotalLength();
      if (!length) return;
      const point = path.getPointAtLength(length / 2);

      const text = document.createElementNS(SVG_NS, "text");
      text.setAttribute("x", point.x);
      text.setAttribute("y", point.y - 2);
      text.setAttribute("text-anchor", "middle");
      text.textContent = edge.label;
      text.classList.add("logic-edge-label");
      text.style.fontSize = `${VIEW.subSize}px`;
      text.style.fill = color;
      this.edgeLabelLayer.appendChild(text);
    }

    // Marks every visible event a report names, and lists them in the panel.
    report(title, entries, eventIds) {
      this.clearHighlights();
      const color = this.colorPresets.red.port;
      eventIds.forEach((id) => {
        const body = this.elementById(this.vertexOf.get(id))?.querySelector(
          ".logic-vertex-body",
        );
        if (!body) return;
        body.classList.add("logic-highlighted");
        body.style.stroke = color;
        body.style.strokeWidth = "2px";
      });

      this.updateInfoPanel(
        {
          chain: {
            title,
            clocks: null,
            upstream: [],
            downstream: entries.slice(0, CHAIN_LIST_LIMIT),
            downstream_label: title,
            upstream_total: 0,
            downstream_total: entries.length,
            limit: CHAIN_LIST_LIMIT,
          },
        },
        "Event",
      );
    }

    _entryOf(eventId, rate) {
      const event = this.events.get(eventId);
      return {
        name: event.name,
        path: this.instances.get(event.ownerId)?.data.path || "",
        type: event.type || "—",
        rate: rate ?? (this.rateLabel(event.frequency) || "no clock"),
        hops: 0,
      };
    }

    highlightUnclocked() {
      const ids = [...this.events.keys()].filter(
        (id) => this._isVisible(id) && !this.clocksOf.has(id),
      );
      this.report(
        "No clock reaches these",
        ids.map((id) => this._entryOf(id)),
        ids,
      );
    }

    highlightMismatches() {
      const found = [];
      this.events.forEach((event, id) => {
        const mismatch = this._rateMismatch(event);
        if (mismatch && this._isVisible(id)) found.push({ id, mismatch });
      });
      this.report(
        "Mixed trigger rates",
        found.map(({ id, mismatch }) =>
          this._entryOf(
            id,
            mismatch.map((rate) => this.rateLabel(rate) || "—").join(" / "),
          ),
        ),
        found.map(({ id }) => id),
      );
    }

    // Chain ends have no vertex of their own where the ports are folded away, so
    // the report comes with the level that draws them.
    async highlightChainEnds() {
      const ids = [...this.chainEndIds].filter((id) => this._isVisible(id));
      if (!ids.some((id) => this.vertexOf.get(id))) {
        await this.setLevel("events");
      }
      this.report(
        "Chain ends",
        ids.map((id) => this._entryOf(id)),
        ids,
      );
    }

    // Centers one vertex in the viewport at a readable zoom. Screen geometry is
    // read back after the scale change, so the pan is exact.
    focusVertex(vertexId, minScale = 0.8) {
      const element = this.elementById(vertexId);
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

    async setLevel(level) {
      if (this.level === level) return;
      this.level = level;
      await this.layoutAndRender();
    }

    async setGrouping(grouping) {
      if (this.grouping === grouping) return;
      this.grouping = grouping;
      this.groupingReport = null;
      await this.layoutAndRender();
    }

    // Keeps the cut with the shortest mean link: every grouping is laid out and
    // measured, and the drawing keeps the best of them.
    async optimizeGrouping() {
      const results = [];
      let best = null;

      for (const grouping of Object.keys(GROUPINGS)) {
        this._setStatus(`measuring ${GROUPINGS[grouping].title}…`);
        this.grouping = grouping;
        this.buildView();
        const graph = await this.layoutView();
        const length = this.measureLinks(graph);
        results.push({ grouping, length, boxes: this.groups.size });
        if (!best || length < best.length) best = { grouping, graph, length };
      }

      results.sort((a, b) => a.length - b.length);
      this.grouping = best.grouping;
      this.buildView();
      this.linkLength = best.length;
      this.groupingReport = results
        .map((entry) => `${entry.grouping} ${entry.length}px`)
        .join(" · ");
      this.currentGraph = best.graph;
      this.render(best.graph);
      this.fitToScreen();
      this.updateInfoPanel(
        {
          name: `grouping: ${best.grouping}`,
          mean_link: `${best.length}px`,
          boxes: this.groups.size,
          measured: this.groupingReport,
        },
        "Layout",
      );
    }

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
      const toggle = (text, checked, onChange) => {
        const wrap = document.createElement("label");
        wrap.className = "logic-toggle";
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = checked;
        box.onchange = () => onChange(box.checked);
        wrap.appendChild(box);
        wrap.appendChild(document.createTextNode(` ${text}`));
        return wrap;
      };

      bar.appendChild(
        row(
          label("View"),
          ...Object.entries(LEVELS).map(([level, spec]) =>
            button(
              spec.button,
              () => this.setLevel(level),
              this.level === level,
            ),
          ),
        ),
      );

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
            this.retrace();
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
          label("Group"),
          ...Object.entries(GROUPINGS).map(([grouping, spec]) =>
            button(
              spec.title,
              () => this.setGrouping(grouping),
              this.grouping === grouping,
            ),
          ),
          button("shortest", () => this.optimizeGrouping()),
        ),
      );

      bar.appendChild(
        row(
          button("unclocked", () => this.highlightUnclocked()),
          button("rate mismatch", () => this.highlightMismatches()),
          button("chain ends", () => this.highlightChainEnds()),
          button("clear", () => this.clearHighlights()),
          button("fit", () => this.fitToScreen()),
        ),
      );

      bar.appendChild(
        row(
          label("Color"),
          button(
            "node",
            () => {
              this.colorBy = "owner";
              this.redraw();
            },
            this.colorBy === "owner",
          ),
          button(
            "rate",
            () => {
              this.colorBy = "rate";
              this.redraw();
            },
            this.colorBy === "rate",
          ),
        ),
      );

      bar.appendChild(
        toggle(
          "events with no trigger relation",
          this.showUnlinked,
          async (checked) => {
            this.showUnlinked = checked;
            await this.layoutAndRender();
          },
        ),
      );

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
        this.focusVertex(this.vertexOf.get(select.value));
      };

      row.appendChild(select);
      return row;
    }

    // What the toolbar reports while a measurement is running.
    _setStatus(text) {
      const counters = this.container.querySelector(".logic-counters");
      if (counters) counters.textContent = text;
    }

    buildCounters() {
      const shown = [...this.events.keys()].filter((id) => this._isVisible(id));
      const unclocked = shown.filter((id) => !this.clocksOf.has(id)).length;

      const div = document.createElement("div");
      div.className = "logic-counters";
      div.textContent =
        `${this.vertices.size} ${LEVELS[this.level].title} · ` +
        `${this.viewEdges.length} links · ${this.foldedCount} folded · ` +
        `${this.chainEndIds.size} chain ends · ${unclocked} unclocked · ` +
        `${this.groups.size} boxes · ${this.linkLength}px mean link`;
      if (this.groupingReport) {
        const measured = document.createElement("div");
        measured.className = "logic-legend-note";
        measured.textContent = `measured: ${this.groupingReport}`;
        div.appendChild(measured);
      }
      return div;
    }

    buildLegend() {
      const details = document.createElement("details");
      details.className = "logic-legend";
      details.open = this.legendOpen;
      details.ontoggle = () => {
        this.legendOpen = details.open;
      };
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
        const glyph =
          shape === "port"
            ? this.buildPortGlyph("input")
            : this.buildTypeShape(
                { clock: "periodic", and: "and", or: "or" }[shape] || null,
                24,
                12,
                { cornerR: 2 },
              );
        glyph.setAttribute("transform", "translate(1,1)");
        if (shape !== "port") {
          glyph.classList.add("logic-process");
          if (shape === "unknown") glyph.classList.add("logic-process-unknown");
        }
        svg.appendChild(glyph);

        rowEl.appendChild(svg);
        const span = document.createElement("span");
        span.textContent = text;
        rowEl.appendChild(span);
        details.appendChild(rowEl);
      });

      details.appendChild(this.buildRampRow());
      LEGEND_NOTES.forEach((text) => {
        const note = document.createElement("div");
        note.className = "logic-legend-note";
        note.textContent = text;
        details.appendChild(note);
      });
      return details;
    }

    buildRampRow() {
      const row = document.createElement("div");
      row.className = "logic-legend-row";

      const svg = document.createElementNS(SVG_NS, "svg");
      svg.setAttribute("width", "60");
      svg.setAttribute("height", "10");
      svg.setAttribute("viewBox", "0 0 60 10");
      RATE_RAMP.forEach((rate, i) => {
        const cell = document.createElementNS(SVG_NS, "rect");
        cell.setAttribute("x", i * 10);
        cell.setAttribute("width", 10);
        cell.setAttribute("height", 10);
        cell.setAttribute("fill", this.rateColor(rate));
        svg.appendChild(cell);
      });
      row.appendChild(svg);

      const span = document.createElement("span");
      span.textContent = "0.1Hz → 100Hz — fill under rate coloring";
      row.appendChild(span);
      return row;
    }
  }

  window.LogicDiagramModule = LogicDiagramModule;
})();
