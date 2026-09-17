// Sequence Diagram Module
// Event-chain latency view: one lane per node, time down the page, and the
// chain from a source gate to a sink drawn hop by hop. One solve gives the
// minimum, mean and maximum chain; the view highlights all three and places
// every gate by the component the axis is driven by.

(function () {
  const SVG_NS = ElkCanvas.SVG_NS;
  const T = window.TimingModel;

  // What the numbers are: none, the design's rates and declared latencies, or
  // a loaded measurement with the declared values filling the gaps.
  const STATES = {
    logical: { button: "logical", timed: false },
    declared: { button: "declared", timed: true },
    measured: { button: "measured", timed: true },
  };

  // The component of every summary the y axis is read from.
  const DRIVERS = [
    ["min", "min"],
    ["mean", "mean"],
    ["max", "max"],
    ["sigma1", "mean+1σ"],
    ["sigma2", "mean+2σ"],
    ["sigma3", "mean+3σ"],
  ];

  const CHAIN_CLASS = {
    max: "seq-on-max",
    min: "seq-on-min",
    mean: "seq-on-mean",
  };
  const CHAIN_COLOR = { max: "red", min: "green", mean: "orange" };

  const VIEW = {
    laneMin: 96,
    laneMax: 190,
    laneGap: 18,
    headerH: 44,
    bandPad: 8,
    bandGap: 14,
    rowH: 64,
    barW: 10,
    barMinH: 4,
    logicalBarH: 14,
    glyphW: 14,
    glyphH: 10,
    fontSize: 10,
    subSize: 8,
    gutter: 64,
    padTop: 16,
    padBottom: 24,
    targetHeight: 760,
    collapsedW: 72,
    nameChars: 22,
  };

  const LOD_NAME = 0.45;
  const LOD_RATE = 0.75;
  const ENUMERATE_LIMIT = 12;
  const CHAIN_LIST_LIMIT = 40;

  const LEGEND = [
    ["bar", "a process running; height is its execution time"],
    ["wait", "wait at the gate before it runs (sampling delay, trigger skew)"],
    ["whisker", "±1 sd at the end of a run"],
    ["max", "maximum chain — the critical path"],
    ["min", "minimum chain — the sequential shortest path"],
    ["mean", "mean chain, dashed where it leaves the other two"],
    ["loop", "loop-closing edge, cut from the solve"],
  ];

  const LEGEND_NOTES = [
    "≈ marks a mean folded at an and/or gate: one branch's number, not the set's",
    "? marks a spread that skipped hops with no sd",
    "a periodic gate needs no measurement: its sampling delay is uniform over one period",
    "hatched runs are declared, plain runs measured",
  ];

  class SequenceDiagramModule extends ElkCanvas {
    // ── Initialization ──────────────────────────────────────────────────────────

    constructor(container, options = {}) {
      super(container, options);
      this.graph = new EventGraph();
      this.solution = null;
      this.namedChains = [];
      this.sourceId = null;
      this.sinkId = null;
      this.hopLimit = null;
      this.state = "logical";
      this.driver = "max";
      this.measured = null; // loaded measurement, see latency_source.js
      this.pxPerMs = null;
      this.legendOpen = false;
      this.selectedId = null;
      this.enumerated = null;
      this.highlightEdges = null; // edge ids of an enumerated chain on show
      this.init();
    }

    async init() {
      try {
        await this.loadAndRender();
      } catch (error) {
        console.error("Error loading sequence diagram:", error);
        this.showError(`Error loading sequence diagram: ${error.message}`);
      }
    }

    async loadAndRender() {
      if (!window.sequenceDiagramData?.[this.options.mode]) {
        await this.loadDataScript(this.options.mode, "sequence_diagram");
      }
      const data = window.sequenceDiagramData?.[this.options.mode];
      if (!data) {
        throw new Error(
          `No sequence diagram data available for mode: ${this.options.mode}`,
        );
      }
      this.graph.build(data);
      this.namedChains = this.resolveNamedChains(data.event_chains || []);
      if (window.LatencySource) {
        this.measured = await window.LatencySource.loadBundled(
          this.options.mode,
          this.graph,
        );
        if (this.measured) this.state = "measured";
      }
      this.chooseSource(this.namedChains[0]?.from ?? this.widestRoot());
      if (this.namedChains[0]?.to) this.sinkId = this.namedChains[0].to;
      this.solveAndRender();
    }

    // A named chain points at events by node path and event name.
    resolveNamedChains(chains) {
      const find = (ref) => {
        if (!ref) return null;
        const [path, name] = String(ref).split(":");
        const instance = this.graph.instanceByPath.get(path);
        if (!instance) return null;
        return (
          [...this.graph.events.values()].find(
            (event) =>
              event.ownerId === String(instance.unique_id) &&
              (event.name === name ||
                event.name === `input_${name}` ||
                event.name === `output_${name}`),
          )?.id ?? null
        );
      };
      return chains
        .map((chain) => ({
          name: chain.name,
          from: find(chain.from),
          to: find(chain.to),
        }))
        .filter((chain) => chain.from);
    }

    // The clock root that reaches the most of the graph.
    widestRoot() {
      let best = null;
      this.graph.clockRootIds.forEach((id) => {
        const size = this.graph.reach(id).size;
        if (!best || size > best.size) best = { id, size };
      });
      return best?.id ?? this.graph.events.keys().next().value ?? null;
    }

    chooseSource(sourceId) {
      this.sourceId = sourceId;
      this.sinkId = null;
      this.enumerated = null;
      this.highlightEdges = null;
    }

    // The sink the view opens on: the deepest chain, then the longest.
    defaultSink(solution) {
      let best = null;
      solution.sinkIds.forEach((id) => {
        const arrival = solution.arrivals.get(id);
        const score = [arrival.rank, T.at(arrival.total, "max")];
        if (
          !best ||
          score[0] > best.score[0] ||
          (score[0] === best.score[0] && score[1] > best.score[1])
        ) {
          best = { id, score };
        }
      });
      return best?.id ?? solution.sourceId;
    }

    // ── Solve ───────────────────────────────────────────────────────────────────

    costs() {
      if (this.state === "logical") return T.logicalCosts();
      if (this.state === "measured" && this.measured) {
        return T.measuredCosts(this.graph, this.measured.costs(this.graph));
      }
      return T.declaredCosts(this.graph);
    }

    solveAndRender() {
      if (!this.sourceId) {
        this.showError("The design declares no events to chain.");
        return;
      }
      const solver = new T.ChainSolver(this.graph, this.costs());
      this.solution = solver.solve(this.sourceId, { hopLimit: this.hopLimit });
      if (!this.sinkId || !this.solution.reach.has(this.sinkId)) {
        this.sinkId = this.defaultSink(this.solution);
      }
      this.buildView();
      this.render();
    }

    // ── View model ──────────────────────────────────────────────────────────────

    // The events on some path from the source to the sink, the gates among
    // them, the hops between gates with the ports folded into them, and the
    // lanes the gates sit on.
    buildView() {
      const { solution, graph } = this;
      const onPath = new Set([this.sinkId]);
      const queue = [this.sinkId];
      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const arrival = solution.arrivals.get(queue[cursor]);
        (arrival?.branches || []).forEach((branch) => {
          if (onPath.has(branch.fromId)) return;
          onPath.add(branch.fromId);
          queue.push(branch.fromId);
        });
      }
      this.onPath = onPath;

      this.chains = {};
      ["min", "mean", "max"].forEach((component) => {
        const chain = T.chainTo(solution, this.sinkId, component);
        chain.edgeSet = new Set(chain.edges);
        this.chains[component] = chain;
      });

      this.gates = [...onPath]
        .map((id) => graph.events.get(id))
        .filter(
          (event) => event.kind === "process" || event.id === this.sourceId,
        )
        .sort((a, b) => this.orderKey(a.id) - this.orderKey(b.id));
      this.gateIds = new Set(this.gates.map((gate) => gate.id));

      this.hops = [];
      this.gates.forEach((gate) => {
        this.foldBack(gate.id).forEach((hop) => this.hops.push(hop));
      });
      this.hops.forEach((hop, index) => {
        hop.id = `sq_hop_${index}`;
        hop.on = {};
        ["min", "mean", "max"].forEach((component) => {
          hop.on[component] = hop.edges.every((edgeId) =>
            this.chains[component].edgeSet.has(edgeId),
          );
        });
      });

      this.buildLanes();
    }

    // The gates behind one gate, each with the port events between folded into
    // the hop: the edges it stands for, the transport cost along them, and the
    // topic the message travelled on.
    foldBack(gateId) {
      const { solution, graph } = this;
      const hops = [];
      const walk = (id, edges, comm, topic, branchSummary, depth) => {
        const arrival = solution.arrivals.get(id);
        if (!arrival) return;
        arrival.branches.forEach((branch) => {
          if (!this.onPath.has(branch.fromId)) return;
          const from = graph.events.get(branch.fromId);
          const nextEdges = [branch.key, ...edges];
          const nextComm = T.add(branch.comm, comm);
          const nextTopic =
            topic ??
            (from.kind === "input" || from.kind === "output"
              ? this.topicOf(from)
              : null);
          if (from.kind === "process" || branch.fromId === this.sourceId) {
            hops.push({
              from: branch.fromId,
              to: gateId,
              edges: nextEdges,
              comm: nextComm,
              topic: nextTopic,
              arrival: branchSummary,
              lastEdge: edges.length ? edges[edges.length - 1] : branch.key,
            });
          } else if (depth < 6) {
            walk(
              branch.fromId,
              nextEdges,
              nextComm,
              nextTopic,
              branchSummary,
              depth + 1,
            );
          }
        });
      };
      const arrival = solution.arrivals.get(gateId);
      (arrival?.branches || []).forEach((branch) => {
        if (!this.onPath.has(branch.fromId)) return;
        const from = graph.events.get(branch.fromId);
        const topic =
          from.kind === "input" || from.kind === "output"
            ? this.topicOf(from)
            : null;
        if (from.kind === "process" || branch.fromId === this.sourceId) {
          hops.push({
            from: branch.fromId,
            to: gateId,
            edges: [branch.key],
            comm: branch.comm,
            topic,
            arrival: branch.summary,
            lastEdge: branch.key,
          });
        } else {
          walk(
            branch.fromId,
            [branch.key],
            branch.comm,
            topic,
            branch.summary,
            1,
          );
        }
      });
      return hops;
    }

    topicOf(event) {
      const topic = event.port?.topic;
      if (Array.isArray(topic) && topic.length) return `/${topic.join("/")}`;
      if (typeof topic === "string" && topic) return topic;
      return event.port?.name || event.name.replace(/^(input|output)_/, "");
    }

    // Where an event sits along the axis the view is driven by.
    orderKey(eventId) {
      const arrival = this.solution.arrivals.get(eventId);
      if (!arrival) return Infinity;
      if (!STATES[this.state].timed) return arrival.rank;
      return T.at(arrival.start, this.driver);
    }

    // Lanes: one per node holding a gate on the chain, grouped by component
    // and ordered left to right by the component's declared slot, else by
    // where its gates first appear. Nodes the source reaches but the chain
    // does not pass collapse into one lane per component.
    buildLanes() {
      const { graph, solution } = this;
      const laneOf = new Map(); // ownerId → lane
      const lanes = [];
      this.gates.forEach((gate) => {
        if (laneOf.has(gate.ownerId)) return;
        const instance = graph.instances.get(gate.ownerId)?.data || {};
        const lane = {
          id: `sq_lane_${lanes.length}`,
          ownerId: gate.ownerId,
          instance,
          name: instance.name || gate.ownerId,
          path: instance.path || "",
          component: this.componentOf(instance.path),
          first: this.orderKey(gate.id),
          collapsed: false,
        };
        laneOf.set(gate.ownerId, lane);
        lanes.push(lane);
      });

      const collapsed = new Map(); // component → lane
      solution.reach.forEach((id) => {
        const event = graph.events.get(id);
        if (event.kind !== "process" || laneOf.has(event.ownerId)) return;
        const instance = graph.instances.get(event.ownerId)?.data || {};
        const component = this.componentOf(instance.path);
        if (!collapsed.has(component)) {
          collapsed.set(component, {
            id: `sq_lane_c_${collapsed.size}`,
            ownerId: null,
            instance: graph.instanceByPath.get(component) || {},
            owners: new Set(),
            component,
            first: Infinity,
            collapsed: true,
          });
        }
        collapsed.get(component).owners.add(event.ownerId);
      });
      collapsed.forEach((lane) => {
        lane.name = `+${lane.owners.size} node${lane.owners.size > 1 ? "s" : ""}`;
        lane.path = `${lane.owners.size} nodes in ${lane.component} the chain does not pass`;
        lanes.push(lane);
      });

      const componentOrder = new Map();
      lanes.forEach((lane) => {
        const instance = graph.instanceByPath.get(lane.component);
        const slot = instance?.vis_guide?.position?.[0];
        const entry = componentOrder.get(lane.component) || {
          slot: null,
          first: Infinity,
        };
        if (slot !== null && slot !== undefined) entry.slot = slot;
        entry.first = Math.min(entry.first, lane.first);
        componentOrder.set(lane.component, entry);
      });
      const rankOf = (component) => {
        const entry = componentOrder.get(component);
        return (
          entry.slot ??
          1000 + (Number.isFinite(entry.first) ? entry.first : 999)
        );
      };
      lanes.sort((a, b) => {
        const byComponent = rankOf(a.component) - rankOf(b.component);
        if (byComponent) return byComponent;
        if (a.component !== b.component)
          return a.component.localeCompare(b.component);
        if (a.collapsed !== b.collapsed) return a.collapsed ? 1 : -1;
        return a.first - b.first;
      });

      // Lane width follows the longest name among the lanes it holds.
      lanes.forEach((lane) => {
        const label = this.shortName(lane.name);
        lane.label = label;
        lane.width = lane.collapsed
          ? VIEW.collapsedW
          : Math.min(
              VIEW.laneMax,
              Math.max(
                VIEW.laneMin,
                this.measureTextWidth(label, VIEW.fontSize) + 24,
              ),
            );
      });

      let x = VIEW.gutter;
      const bands = [];
      lanes.forEach((lane, index) => {
        const previous = lanes[index - 1];
        if (!previous || previous.component !== lane.component) {
          if (previous) x += VIEW.bandGap;
          bands.push({
            component: lane.component,
            instance: graph.instanceByPath.get(lane.component) || {},
            x0: x,
            lanes: [],
          });
        }
        x += VIEW.bandPad;
        lane.x = x + lane.width / 2;
        x += lane.width + VIEW.laneGap;
        const band = bands[bands.length - 1];
        band.lanes.push(lane);
        band.x1 = x - VIEW.laneGap + VIEW.bandPad;
      });
      this.lanes = lanes;
      this.laneOf = laneOf;
      this.collapsedLaneOf = collapsed;
      this.bands = bands;
      this.width = x + VIEW.bandPad;
    }

    componentOf(path) {
      return `/${(path || "").split("/").filter(Boolean).slice(0, 1).join("/")}`;
    }

    shortName(name, limit = VIEW.nameChars) {
      if (name.length <= limit) return name;
      return `…${name.slice(name.length - limit + 1)}`;
    }

    // ── Geometry ────────────────────────────────────────────────────────────────

    // Time to y. In the logical state one row per rank; in a timed state the
    // scale is set so the sink lands near the target height, then zoomed.
    prepareScale() {
      const timed = STATES[this.state].timed;
      this.origin = VIEW.padTop + VIEW.headerH;
      if (!timed) {
        this.pxPerMs = null;
        return;
      }
      if (this.pxPerMs === null) {
        let extent = 0;
        this.gates.forEach((gate) => {
          const arrival = this.solution.arrivals.get(gate.id);
          extent = Math.max(
            extent,
            T.at(arrival.total, this.driver) + arrival.total.sd,
          );
        });
        this.pxPerMs = extent > 0 ? VIEW.targetHeight / extent : 1;
      }
    }

    yOf(summary) {
      return this.origin + T.at(summary, this.driver) * this.pxPerMs;
    }

    // Top, bottom and arrival edge of a gate's run.
    gateBox(gateId) {
      const arrival = this.solution.arrivals.get(gateId);
      if (!STATES[this.state].timed) {
        const top = this.origin + arrival.rank * VIEW.rowH;
        return {
          arrive: top,
          top,
          bottom: top + VIEW.logicalBarH,
          sdPx: 0,
        };
      }
      const arrive = this.yOf(arrival.arrive);
      const top = this.yOf(arrival.start);
      const bottom = Math.max(this.yOf(arrival.total), top + VIEW.barMinH);
      return { arrive, top, bottom, sdPx: arrival.total.sd * this.pxPerMs };
    }

    // ── Render ──────────────────────────────────────────────────────────────────

    render() {
      this.prepareScale();
      const { layer } = this.createCanvas();
      this.container.classList.add("sequence-diagram-container");

      this.bandLayer = document.createElementNS(SVG_NS, "g");
      this.axisLayer = document.createElementNS(SVG_NS, "g");
      this.hopLayer = document.createElementNS(SVG_NS, "g");
      this.gateLayer = document.createElementNS(SVG_NS, "g");
      this.labelLayer = document.createElementNS(SVG_NS, "g");
      [
        this.bandLayer,
        this.axisLayer,
        this.hopLayer,
        this.gateLayer,
        this.labelLayer,
      ].forEach((g) => layer.appendChild(g));

      this.boxes = new Map();
      this.gates.forEach((gate) =>
        this.boxes.set(gate.id, this.gateBox(gate.id)),
      );
      let bottom = this.origin + VIEW.rowH;
      this.boxes.forEach((box) => {
        bottom = Math.max(bottom, box.bottom + box.sdPx);
      });
      this.height = bottom + VIEW.padBottom;

      this.drawBands();
      this.drawAxis();
      this.hops.forEach((hop) => this.drawHop(hop));
      this.gates.forEach((gate) => this.drawGate(gate));
      this.applyEmphasis();

      this.renderToolbar();
      this.fitToScreen();
      this.applyLOD();
      if (this.selectedId) this.select(this.selectedId, false);
    }

    // A tinted band per component behind its lanes, the lane names in its header
    // and a hairline down every lane.
    drawBands() {
      const defaults = this.isDarkMode()
        ? this.styleDefaults.dark
        : this.styleDefaults.light;
      this.bands.forEach((band) => {
        const guide = band.instance.vis_guide;
        const g = document.createElementNS(SVG_NS, "g");
        g.classList.add("seq-band");

        const rect = document.createElementNS(SVG_NS, "rect");
        rect.setAttribute("x", band.x0);
        rect.setAttribute("y", VIEW.padTop);
        rect.setAttribute("width", band.x1 - band.x0);
        rect.setAttribute("height", this.height - VIEW.padTop);
        rect.setAttribute("rx", 4);
        rect.setAttribute(
          "fill",
          this.themed(guide, "background_color", defaults.rootBg),
        );
        rect.setAttribute(
          "stroke",
          this.themed(guide, "color", defaults.stroke),
        );
        rect.classList.add("seq-band-rect");
        g.appendChild(rect);

        const title = document.createElementNS(SVG_NS, "text");
        title.setAttribute("x", (band.x0 + band.x1) / 2);
        title.setAttribute("y", VIEW.padTop + 11);
        title.setAttribute("text-anchor", "middle");
        title.textContent = band.component;
        title.classList.add("seq-band-title");
        title.style.fontSize = `${VIEW.fontSize}px`;
        title.style.fill = this.themed(guide, "text_color", defaults.text);
        g.appendChild(title);

        band.lanes.forEach((lane) => {
          const laneGuide = lane.instance.vis_guide || guide;
          const line = document.createElementNS(SVG_NS, "line");
          line.setAttribute("x1", lane.x);
          line.setAttribute("x2", lane.x);
          line.setAttribute("y1", this.origin - 4);
          line.setAttribute("y2", this.height - VIEW.padBottom / 2);
          line.setAttribute(
            "stroke",
            this.themed(laneGuide, "color", defaults.stroke),
          );
          line.classList.add("seq-lane-line");
          if (lane.collapsed) line.classList.add("seq-lane-collapsed");
          g.appendChild(line);

          const name = document.createElementNS(SVG_NS, "text");
          name.setAttribute("x", lane.x);
          name.setAttribute("y", VIEW.padTop + 28);
          name.setAttribute("text-anchor", "middle");
          name.textContent = lane.label;
          name.classList.add("seq-lane-name");
          if (lane.collapsed) name.classList.add("seq-lane-collapsed");
          name.style.fontSize = `${VIEW.fontSize}px`;
          name.style.fill = this.themed(laneGuide, "text_color", defaults.text);
          const tip = document.createElementNS(SVG_NS, "title");
          tip.textContent = lane.path;
          name.appendChild(tip);
          name.style.cursor = "pointer";
          name.onclick = (e) => {
            if (this.hasDragged) return;
            e.stopPropagation();
            this.selectLane(lane);
          };
          g.appendChild(name);
        });
        this.bandLayer.appendChild(g);
      });
    }

    // Ticks down the left gutter: milliseconds in a timed state, ranks otherwise.
    drawAxis() {
      const g = document.createElementNS(SVG_NS, "g");
      g.classList.add("seq-axis");
      const x = VIEW.gutter - 10;
      const axis = document.createElementNS(SVG_NS, "line");
      axis.setAttribute("x1", x);
      axis.setAttribute("x2", x);
      axis.setAttribute("y1", this.origin);
      axis.setAttribute("y2", this.height - VIEW.padBottom / 2);
      axis.classList.add("seq-axis-line");
      g.appendChild(axis);

      const tick = (y, label) => {
        const mark = document.createElementNS(SVG_NS, "line");
        mark.setAttribute("x1", x - 4);
        mark.setAttribute("x2", this.width);
        mark.setAttribute("y1", y);
        mark.setAttribute("y2", y);
        mark.classList.add("seq-axis-tick");
        g.appendChild(mark);
        const text = document.createElementNS(SVG_NS, "text");
        text.setAttribute("x", x - 7);
        text.setAttribute("y", y);
        text.setAttribute("text-anchor", "end");
        text.setAttribute("dominant-baseline", "central");
        text.textContent = label;
        text.classList.add("seq-axis-label");
        text.style.fontSize = `${VIEW.subSize}px`;
        g.appendChild(text);
      };

      if (!STATES[this.state].timed) {
        const rows = Math.round(
          (this.height - VIEW.padBottom - this.origin) / VIEW.rowH,
        );
        for (let rank = 0; rank <= rows; rank += 1) {
          tick(this.origin + rank * VIEW.rowH, `rank ${rank}`);
        }
      } else {
        const spanMs =
          (this.height - VIEW.padBottom - this.origin) / this.pxPerMs;
        const step = niceStep(spanMs / 8);
        for (let ms = 0; ms <= spanMs + 1e-9; ms += step) {
          tick(this.origin + ms * this.pxPerMs, T.formatMs(ms, 1));
        }
        const title = document.createElementNS(SVG_NS, "text");
        title.setAttribute("x", x);
        title.setAttribute("y", this.origin - 8);
        title.setAttribute("text-anchor", "end");
        title.textContent = `${this.driverLabel()} · ${this.state}`;
        title.classList.add("seq-axis-label");
        title.style.fontSize = `${VIEW.subSize}px`;
        g.appendChild(title);
      }
      this.axisLayer.appendChild(g);
    }

    driverLabel() {
      return DRIVERS.find(([key]) => key === this.driver)?.[1] || this.driver;
    }

    // A hop is an arrow from the end of one run to the arrival at the next
    // gate, labelled with the topic; a hop inside one node runs down its lane.
    drawHop(hop) {
      const fromLane = this.laneOf.get(this.graph.events.get(hop.from).ownerId);
      const toLane = this.laneOf.get(this.graph.events.get(hop.to).ownerId);
      const fromBox = this.boxes.get(hop.from);
      const toBox = this.boxes.get(hop.to);
      if (!fromLane || !toLane || !fromBox || !toBox) return;

      const x1 = fromLane.x;
      const x2 = toLane.x;
      const y1 = fromBox.bottom;
      const y2 = STATES[this.state].timed
        ? Math.max(this.yOf(hop.arrival), y1)
        : toBox.top;
      const same = fromLane === toLane;

      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("id", hop.id);
      if (same) {
        const dx = VIEW.barW * 1.6;
        path.setAttribute(
          "d",
          `M ${x1} ${y1} C ${x1 + dx} ${y1 + 6}, ${x2 + dx} ${y2 - 6}, ${x2 + 2} ${y2}`,
        );
        path.classList.add("seq-hop-internal");
      } else {
        const side = x2 > x1 ? 1 : -1;
        path.setAttribute(
          "d",
          `M ${x1 + side * (VIEW.barW / 2)} ${y1} L ${x2 - side * (VIEW.barW / 2 + 1)} ${y2}`,
        );
      }
      path.classList.add("seq-hop");
      path.setAttribute("marker-end", "url(#arrowhead-depth-0)");
      hop.element = path;

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeHop(hop);
      path.appendChild(title);
      path.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.select(hop.id);
      };
      this.hopLayer.appendChild(path);

      // A wider transparent stroke keeps thin arrows clickable.
      const hit = path.cloneNode(false);
      hit.removeAttribute("id");
      hit.removeAttribute("marker-end");
      hit.classList.add("seq-hop-hit");
      hit.onclick = path.onclick;
      hit.appendChild(title.cloneNode(true));
      this.hopLayer.appendChild(hit);

      if (hop.topic && !same) {
        const label = document.createElementNS(SVG_NS, "text");
        label.setAttribute("x", (x1 + x2) / 2);
        label.setAttribute("y", (y1 + y2) / 2 - 3);
        label.setAttribute("text-anchor", "middle");
        label.textContent = this.shortName(hop.topic, 30);
        label.classList.add("seq-hop-label");
        label.style.fontSize = `${VIEW.subSize}px`;
        hop.label = label;
        this.labelLayer.appendChild(label);
      }
    }

    // A gate is a run on its lane: the glyph names the trigger semantics, the
    // bar is the run, a bracket above it the wait, a whisker below it the sd.
    drawGate(gate) {
      const lane = this.laneOf.get(gate.ownerId);
      const box = this.boxes.get(gate.id);
      if (!lane || !box) return;
      const arrival = this.solution.arrivals.get(gate.id);
      const guide = lane.instance.vis_guide;
      const defaults = this.isDarkMode()
        ? this.styleDefaults.dark
        : this.styleDefaults.light;
      const style = { cornerR: 2 };

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("id", `sq_gate_${gate.id}`);
      g.classList.add("seq-gate");
      g.dataset.eventId = gate.id;
      g.style.cursor = "pointer";
      gate.element = g;

      if (box.arrive < box.top - 0.5) {
        const wait = document.createElementNS(SVG_NS, "path");
        const w = VIEW.barW / 2 + 3;
        wait.setAttribute(
          "d",
          `M ${lane.x - w} ${box.arrive} L ${lane.x - w} ${box.top} M ${lane.x - w - 3} ${box.arrive} L ${lane.x - w + 3} ${box.arrive}`,
        );
        wait.classList.add("seq-wait");
        g.appendChild(wait);
      }

      const bar = document.createElementNS(SVG_NS, "rect");
      bar.setAttribute("x", lane.x - VIEW.barW / 2);
      bar.setAttribute("y", box.top);
      bar.setAttribute("width", VIEW.barW);
      bar.setAttribute("height", Math.max(VIEW.barMinH, box.bottom - box.top));
      bar.setAttribute("rx", 1.5);
      bar.setAttribute(
        "fill",
        this.themed(guide, "medium_color", defaults.nodeBg),
      );
      bar.setAttribute("stroke", this.themed(guide, "color", defaults.stroke));
      bar.classList.add("seq-bar");
      if (arrival.exec.source === "declared") bar.classList.add("seq-declared");
      if (arrival.exec.source === "none" && STATES[this.state].timed) {
        bar.classList.add("seq-unknown");
      }
      g.appendChild(bar);

      if (box.sdPx > 0.5) {
        const whisker = document.createElementNS(SVG_NS, "path");
        const y0 = Math.max(box.top, box.bottom - box.sdPx);
        const y1 = box.bottom + box.sdPx;
        const w = 3;
        whisker.setAttribute(
          "d",
          `M ${lane.x} ${y0} L ${lane.x} ${y1} M ${lane.x - w} ${y0} L ${lane.x + w} ${y0} M ${lane.x - w} ${y1} L ${lane.x + w} ${y1}`,
        );
        whisker.classList.add("seq-whisker");
        if (arrival.total.missingSd)
          whisker.classList.add("seq-whisker-partial");
        g.appendChild(whisker);
      }

      const glyph = this.buildTypeShape(
        gate.type,
        VIEW.glyphW,
        VIEW.glyphH,
        style,
      );
      glyph.setAttribute(
        "transform",
        `translate(${lane.x + VIEW.barW / 2 + 3},${box.top - VIEW.glyphH / 2 + 2})`,
      );
      glyph.classList.add("logic-process", "seq-glyph");
      if (!gate.type) glyph.classList.add("logic-process-unknown");
      g.appendChild(glyph);

      const name = document.createElementNS(SVG_NS, "text");
      name.setAttribute("x", lane.x + VIEW.barW / 2 + VIEW.glyphW + 6);
      name.setAttribute("y", box.top + 2);
      name.setAttribute("dominant-baseline", "central");
      name.textContent = gate.name;
      name.classList.add("seq-gate-name");
      name.style.fontSize = `${VIEW.subSize}px`;
      g.appendChild(name);

      if (STATES[this.state].timed) {
        const rate = document.createElementNS(SVG_NS, "text");
        rate.setAttribute("x", lane.x + VIEW.barW / 2 + VIEW.glyphW + 6);
        rate.setAttribute("y", box.top + 2 + VIEW.subSize + 1);
        rate.setAttribute("dominant-baseline", "central");
        rate.textContent = `@${T.formatMs(T.at(arrival.total, this.driver), 1)}`;
        rate.classList.add("seq-gate-rate");
        rate.style.fontSize = `${VIEW.subSize - 1}px`;
        g.appendChild(rate);
      } else if (gate.frequency) {
        const rate = document.createElementNS(SVG_NS, "text");
        rate.setAttribute("x", lane.x + VIEW.barW / 2 + VIEW.glyphW + 6);
        rate.setAttribute("y", box.top + 2 + VIEW.subSize + 1);
        rate.setAttribute("dominant-baseline", "central");
        rate.textContent = this.rateLabel(gate.frequency);
        rate.classList.add("seq-gate-rate");
        rate.style.fontSize = `${VIEW.subSize - 1}px`;
        g.appendChild(rate);
      }

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeGate(gate);
      g.appendChild(title);
      g.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.select(gate.id);
      };
      this.gateLayer.appendChild(g);
    }

    rateLabel(frequency) {
      if (frequency === null || frequency === undefined) return "";
      if (frequency === 0) return "once";
      return `${Number(frequency.toFixed(3))}Hz`;
    }

    // The three chains at full strength, everything else dimmed; an enumerated
    // chain on show takes the place of all three.
    applyEmphasis() {
      const chainOf = (hop) => {
        if (this.highlightEdges) {
          return hop.edges.every((id) => this.highlightEdges.has(id))
            ? "max"
            : null;
        }
        if (hop.on.max) return "max";
        if (hop.on.min) return "min";
        if (hop.on.mean) return "mean";
        return null;
      };
      const onGate = new Map();
      this.hops.forEach((hop) => {
        const component = chainOf(hop);
        const element = hop.element;
        if (!element) return;
        element.classList.remove(...Object.values(CHAIN_CLASS), "seq-dim");
        hop.label?.classList.remove(...Object.values(CHAIN_CLASS), "seq-dim");
        if (component) {
          element.classList.add(CHAIN_CLASS[component]);
          hop.label?.classList.add(CHAIN_CLASS[component]);
          element.setAttribute(
            "marker-end",
            `url(#arrowhead-highlighted-${CHAIN_COLOR[component]}-depth-0)`,
          );
          if (hop.on.mean && component !== "mean" && !this.highlightEdges) {
            element.classList.add("seq-on-mean-too");
          }
          onGate.set(hop.to, true);
          onGate.set(hop.from, true);
        } else {
          element.classList.add("seq-dim");
          hop.label?.classList.add("seq-dim");
          element.setAttribute("marker-end", "url(#arrowhead-depth-0)");
        }
        if (hop.on.mean && !hop.on.max && !hop.on.min && !this.highlightEdges) {
          element.classList.add("seq-mean-diverges");
        }
      });
      this.gates.forEach((gate) => {
        gate.element?.classList.toggle(
          "seq-dim",
          !onGate.get(gate.id) &&
            gate.id !== this.sourceId &&
            gate.id !== this.sinkId,
        );
      });
    }

    // ── Level of detail ─────────────────────────────────────────────────────────

    applyLOD() {
      const root = this.currentSvgRoot;
      if (!root) return;
      root.classList.toggle("lod-name", this.transform.k >= LOD_NAME);
      root.classList.toggle("lod-rate", this.transform.k >= LOD_RATE);
    }

    onTransform() {
      this.applyLOD();
    }

    updateTheme() {
      this.render();
    }

    // ── Descriptions ────────────────────────────────────────────────────────────

    describeGate(gate) {
      const arrival = this.solution.arrivals.get(gate.id);
      const lines = [
        `${this.graph.ownerOf(gate.id)?.path || ""}:${gate.name}`,
        `${gate.type || "type not declared"} · ${this.rateLabel(gate.frequency) || "no clock"}`,
      ];
      if (STATES[this.state].timed) {
        lines.push(`cumulative ${T.formatSummary(arrival.total)}`);
      } else {
        lines.push(`rank ${arrival.rank}`);
      }
      return lines.join("\n");
    }

    describeHop(hop) {
      const from = this.graph.events.get(hop.from);
      const to = this.graph.events.get(hop.to);
      const lines = [
        `${this.graph.ownerOf(hop.from)?.name}:${from.name} → ${this.graph.ownerOf(hop.to)?.name}:${to.name}`,
      ];
      if (hop.topic) lines.push(hop.topic);
      if (STATES[this.state].timed) {
        lines.push(`comm ${T.formatSummary(hop.comm)}`);
        lines.push(`arrives ${T.formatSummary(hop.arrival)}`);
      }
      return lines.join("\n");
    }

    // ── Selection / info panel ──────────────────────────────────────────────────

    select(id, focus = true) {
      const hop = this.hops.find((h) => h.id === id);
      this.container
        .querySelectorAll(".seq-selected")
        .forEach((el) => el.classList.remove("seq-selected"));
      this.selectedId = id;
      if (hop) {
        hop.element?.classList.add("seq-selected");
        this.updateInfoPanel(this.describeHopPanel(hop), "Hop");
        return;
      }
      const gate = this.gates.find((g) => g.id === id);
      if (!gate) return;
      gate.element?.classList.add("seq-selected");
      this.updateInfoPanel(this.describeGatePanel(gate), "Event");
      if (focus) this.focusElement(gate.element);
    }

    selectLane(lane) {
      if (lane.collapsed) {
        const entries = [...lane.owners].map((ownerId) => {
          const instance = this.graph.instances.get(ownerId)?.data || {};
          return {
            name: instance.name || ownerId,
            path: instance.path || "",
            type: "node",
            rate: "off the chain",
          };
        });
        this.updateInfoPanel(
          {
            name: lane.path,
            chain: {
              title: "Reached, not on the chain",
              clocks: null,
              upstream: [],
              downstream: entries.slice(0, CHAIN_LIST_LIMIT),
              downstream_label: lane.component,
              upstream_total: 0,
              downstream_total: entries.length,
              limit: CHAIN_LIST_LIMIT,
            },
          },
          "Component",
        );
        return;
      }
      this.updateInfoPanel(
        {
          ...lane.instance,
          gates: this.gates.filter((g) => g.ownerId === lane.ownerId).length,
        },
        "Node",
      );
    }

    latencyRows(arrival, comm) {
      const timed = STATES[this.state].timed;
      const row = (label, summary) => ({
        label,
        value: timed ? T.formatSummary(summary) : "—",
        source: summary?.source,
        count: summary?.count,
      });
      const rows = [];
      if (comm) rows.push(row("comm", comm));
      rows.push(row("wait", arrival.wait));
      rows.push(row("exec", arrival.exec));
      rows.push(row("cumulative", arrival.total));
      return rows;
    }

    describeGatePanel(gate) {
      const arrival = this.solution.arrivals.get(gate.id);
      const owner = this.graph.ownerOf(gate.id) || {};
      const declared = T.fromRecord(gate.latency, "declared");
      const measured =
        this.state === "measured" && arrival.exec.source === "measured"
          ? arrival.exec
          : null;
      const upstream = this.graph.walk([gate.id], "up");
      const downstream = this.graph.walk([gate.id], "down");
      return {
        name: gate.name,
        path: owner.path || "",
        source_file: owner.source_file,
        event: {
          kind: gate.kind,
          type: gate.type || "not declared",
          rate: this.rateLabel(gate.frequency) || "no clock",
          warn_rate: gate.warn_rate,
          error_rate: gate.error_rate,
          timeout: gate.timeout,
          mismatch: this.graph.rateMismatch(gate),
        },
        latency: {
          state: this.state,
          rank: arrival.rank,
          fold: arrival.fold,
          rows: this.latencyRows(arrival),
          branches: arrival.branches.map((branch) => ({
            name: this.branchName(branch),
            value: T.formatSummary(branch.summary),
            via: Object.entries(arrival.via)
              .filter(([, key]) => key === branch.key)
              .map(([component]) => component),
          })),
          declared: declared ? T.formatSummary(declared) : null,
          measured: measured ? T.formatSummary(measured) : null,
          delta: T.compare(declared, measured),
          unknownType: gate.kind === "process" && !gate.type,
        },
        chain: this.chainReport(upstream, downstream, gate.id),
      };
    }

    describeHopPanel(hop) {
      const from = this.graph.events.get(hop.from);
      const to = this.graph.events.get(hop.to);
      const arrival = this.solution.arrivals.get(hop.to);
      return {
        name: `${from.name} → ${to.name}`,
        from: `${this.graph.ownerOf(hop.from)?.path}:${from.name}`,
        to: `${this.graph.ownerOf(hop.to)?.path}:${to.name}`,
        topic: hop.topic || undefined,
        latency: {
          state: this.state,
          fold: arrival.fold,
          rows: this.latencyRows(arrival, hop.comm),
          arrives: T.formatSummary(hop.arrival),
          on: Object.entries(hop.on)
            .filter(([, yes]) => yes)
            .map(([component]) => component),
          branches: arrival.branches.map((branch) => ({
            name: this.branchName(branch),
            value: T.formatSummary(branch.summary),
            via: Object.entries(arrival.via)
              .filter(([, key]) => key === branch.key)
              .map(([component]) => component),
          })),
        },
      };
    }

    branchName(branch) {
      const from = this.graph.events.get(branch.fromId);
      const owner = this.graph.ownerOf(branch.fromId);
      return `${owner?.name || ""}:${from?.name || branch.fromId}`;
    }

    chainReport(upstream, downstream, eventId) {
      const entry = (id, hops) => {
        const item = this.graph.events.get(id);
        const instance = this.graph.ownerOf(id) || {};
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
        clocks: [...(this.graph.clocksOf.get(eventId) || [])].map((id) => {
          const clock = this.graph.events.get(id);
          return {
            name: clock.name,
            path: this.graph.ownerOf(id)?.path || "",
            rate: this.rateLabel(clock.frequency) || "no clock",
          };
        }),
        upstream: list(upstream),
        downstream: list(downstream),
        upstream_total: upstream.order.length,
        downstream_total: downstream.order.length,
        limit: CHAIN_LIST_LIMIT,
      };
    }

    focusElement(element, minScale = 0.8) {
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

    // ── Chain enumeration ───────────────────────────────────────────────────────

    enumerate() {
      const { chains, total } = T.enumerateChains(this.solution, this.sinkId, {
        limit: ENUMERATE_LIMIT,
      });
      this.enumerated = chains;
      this.updateInfoPanel(
        {
          name: `chains to ${this.graph.events.get(this.sinkId)?.name}`,
          chains: {
            title: `Chains (${chains.length} of ${total})`,
            total,
            items: chains.map((chain, index) => ({
              label: `#${index + 1} ${STATES[this.state].timed ? T.formatSummary(chain.summary) : `${chain.events.size} events`}`,
              detail: `${[...chain.events].filter((id) => this.gateIds.has(id)).length} gates`,
              onSelect: () => this.showChain(index),
            })),
          },
        },
        "Chains",
      );
      this.showChain(0);
    }

    showChain(index) {
      const chain = this.enumerated?.[index];
      this.highlightEdges = chain ? new Set(chain.edges) : null;
      this.applyEmphasis();
      this.updateCounters();
    }

    clearChain() {
      this.highlightEdges = null;
      this.enumerated = null;
      this.applyEmphasis();
      this.updateCounters();
    }

    // ── Measurement ─────────────────────────────────────────────────────────────

    // A measurement file dropped on the canvas replaces the loaded one.
    async loadMeasurement(file) {
      if (!window.LatencySource) return;
      try {
        this.measured = await window.LatencySource.fromFile(file, this.graph);
        this.state = "measured";
        this.pxPerMs = null;
        this.solveAndRender();
      } catch (error) {
        console.error("Failed to load measurement:", error);
        this._setStatus(`measurement rejected: ${error.message}`);
      }
    }

    // ── Toolbar ─────────────────────────────────────────────────────────────────

    setState(state) {
      if (this.state === state) return;
      if (state === "measured" && !this.measured) return;
      this.state = state;
      this.pxPerMs = null;
      this.solveAndRender();
    }

    setDriver(driver) {
      if (this.driver === driver) return;
      this.driver = driver;
      this.pxPerMs = null;
      this.buildView();
      this.render();
    }

    zoomTime(factor) {
      if (!STATES[this.state].timed) return;
      this.pxPerMs *= factor;
      this.render();
    }

    renderToolbar() {
      const bar = document.createElement("div");
      bar.className = "logic-toolbar seq-toolbar";

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
      const button = (text, onClick, active = false, disabled = false) => {
        const btn = document.createElement("button");
        btn.className = "logic-btn";
        btn.textContent = text;
        btn.classList.toggle("active", active);
        btn.disabled = disabled;
        btn.onclick = onClick;
        return btn;
      };

      bar.appendChild(
        row(
          label("State"),
          ...Object.entries(STATES).map(([state, spec]) =>
            button(
              state === "measured" && !this.measured
                ? "measured (drop a latency file)"
                : spec.button,
              () => this.setState(state),
              this.state === state,
              state === "measured" && !this.measured,
            ),
          ),
        ),
      );

      bar.appendChild(
        row(
          label("Axis"),
          ...DRIVERS.map(([driver, text]) =>
            button(
              text,
              () => this.setDriver(driver),
              this.driver === driver,
              !STATES[this.state].timed,
            ),
          ),
          button(
            "time +",
            () => this.zoomTime(2),
            false,
            !STATES[this.state].timed,
          ),
          button(
            "time −",
            () => this.zoomTime(0.5),
            false,
            !STATES[this.state].timed,
          ),
        ),
      );

      bar.appendChild(row(label("From"), this.buildSourcePicker()));
      bar.appendChild(
        row(label("To"), this.buildSinkPicker(), this.buildHopLimit()),
      );

      bar.appendChild(
        row(
          button("enumerate chains", () => this.enumerate()),
          button(
            "three chains",
            () => this.clearChain(),
            false,
            !this.highlightEdges,
          ),
          button("fit", () => this.fitToScreen()),
        ),
      );

      bar.appendChild(this.buildCounters());
      bar.appendChild(this.buildLegend());
      this.container.appendChild(bar);
      this.installDropZone();
    }

    // Sources: named chains first, then every clock root by path.
    buildSourcePicker() {
      const select = document.createElement("select");
      select.className = "logic-select";
      if (this.namedChains.length) {
        const group = document.createElement("optgroup");
        group.label = "named chains";
        this.namedChains.forEach((chain, index) => {
          const option = document.createElement("option");
          option.value = `chain:${index}`;
          option.textContent = chain.name;
          option.selected =
            chain.from === this.sourceId &&
            (!chain.to || chain.to === this.sinkId);
          group.appendChild(option);
        });
        select.appendChild(group);
      }
      const group = document.createElement("optgroup");
      group.label = `clock roots (${this.graph.clockRootIds.length})`;
      this.graph.clockRootIds
        .map((id) => ({
          id,
          event: this.graph.events.get(id),
          path: this.graph.ownerOf(id)?.path || "",
        }))
        .sort((a, b) => a.path.localeCompare(b.path))
        .forEach(({ id, event, path }) => {
          const option = document.createElement("option");
          option.value = id;
          option.textContent = `${path}:${event.name} · ${this.rateLabel(event.frequency) || "—"}`;
          option.selected =
            id === this.sourceId && !select.querySelector("option[selected]");
          group.appendChild(option);
        });
      select.appendChild(group);
      select.onchange = () => {
        if (select.value.startsWith("chain:")) {
          const chain = this.namedChains[Number(select.value.slice(6))];
          this.chooseSource(chain.from);
          this.sinkId = chain.to;
        } else {
          this.chooseSource(select.value);
        }
        this.pxPerMs = null;
        this.solveAndRender();
      };
      return select;
    }

    // Sinks: every event the source reaches that triggers nothing further.
    buildSinkPicker() {
      const select = document.createElement("select");
      select.className = "logic-select";
      const ids = new Set(this.solution.sinkIds);
      ids.add(this.sinkId);
      [...ids]
        .map((id) => ({
          id,
          event: this.graph.events.get(id),
          path: this.graph.ownerOf(id)?.path || "",
          arrival: this.solution.arrivals.get(id),
        }))
        .sort(
          (a, b) =>
            b.arrival.rank - a.arrival.rank || a.path.localeCompare(b.path),
        )
        .forEach(({ id, event, path, arrival }) => {
          const option = document.createElement("option");
          option.value = id;
          const tail = STATES[this.state].timed
            ? T.formatMs(T.at(arrival.total, this.driver), 1)
            : `rank ${arrival.rank}`;
          option.textContent = `${path}:${event.name} · ${tail}`;
          option.selected = id === this.sinkId;
          select.appendChild(option);
        });
      select.onchange = () => {
        this.sinkId = select.value;
        this.enumerated = null;
        this.highlightEdges = null;
        this.pxPerMs = null;
        this.buildView();
        this.render();
      };
      return select;
    }

    buildHopLimit() {
      const wrap = document.createElement("label");
      wrap.className = "logic-toggle";
      wrap.appendChild(document.createTextNode("hops ≤ "));
      const input = document.createElement("input");
      input.type = "number";
      input.min = "1";
      input.className = "seq-hop-limit";
      input.value = this.hopLimit ?? "";
      input.placeholder = "∞";
      input.onchange = () => {
        const value = Number(input.value);
        this.hopLimit = input.value === "" || !(value > 0) ? null : value;
        this.pxPerMs = null;
        this.solveAndRender();
      };
      wrap.appendChild(input);
      return wrap;
    }

    _setStatus(text) {
      const counters = this.container.querySelector(".logic-counters");
      if (counters) counters.textContent = text;
    }

    countersText() {
      const sink = this.solution.arrivals.get(this.sinkId);
      const lanes = this.lanes.filter((lane) => !lane.collapsed).length;
      const parts = [
        `${lanes} nodes`,
        `${this.gates.length} gates`,
        `${this.hops.length} hops`,
        `${this.solution.loopEdges.size} loop edge${this.solution.loopEdges.size === 1 ? "" : "s"} cut`,
        `${this.solution.reach.size} events reached`,
      ];
      if (this.solution.unknownGates.length) {
        parts.push(
          `${this.solution.unknownGates.length} gates without a type (folded as or)`,
        );
      }
      if (STATES[this.state].timed && sink) {
        parts.push(`total ${T.formatSummary(sink.total)}`);
      } else if (sink) {
        parts.push(`depth ${sink.rank}`);
      }
      if (this.highlightEdges) parts.push("showing one enumerated chain");
      if (this.measured) parts.push(`measurement: ${this.measured.label}`);
      return parts.join(" · ");
    }

    buildCounters() {
      const div = document.createElement("div");
      div.className = "logic-counters";
      div.textContent = this.countersText();
      return div;
    }

    updateCounters() {
      this._setStatus(this.countersText());
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

      LEGEND.forEach(([kind, text]) => {
        const rowEl = document.createElement("div");
        rowEl.className = "logic-legend-row";
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("width", "26");
        svg.setAttribute("height", "14");
        svg.setAttribute("viewBox", "0 0 26 14");
        svg.appendChild(this.legendGlyph(kind));
        rowEl.appendChild(svg);
        const span = document.createElement("span");
        span.textContent = text;
        rowEl.appendChild(span);
        details.appendChild(rowEl);
      });
      LEGEND_NOTES.forEach((text) => {
        const note = document.createElement("div");
        note.className = "logic-legend-note";
        note.textContent = text;
        details.appendChild(note);
      });
      return details;
    }

    legendGlyph(kind) {
      const make = (tag) => document.createElementNS(SVG_NS, tag);
      if (kind === "bar") {
        const rect = make("rect");
        rect.setAttribute("x", 9);
        rect.setAttribute("y", 1);
        rect.setAttribute("width", 8);
        rect.setAttribute("height", 12);
        rect.classList.add("seq-bar", "seq-legend-bar");
        return rect;
      }
      if (kind === "wait") {
        const path = make("path");
        path.setAttribute("d", "M 13 2 L 13 12 M 10 2 L 16 2");
        path.classList.add("seq-wait");
        return path;
      }
      if (kind === "whisker") {
        const path = make("path");
        path.setAttribute("d", "M 13 2 L 13 12 M 10 2 L 16 2 M 10 12 L 16 12");
        path.classList.add("seq-whisker");
        return path;
      }
      const line = make("path");
      line.setAttribute("d", "M 2 7 L 24 7");
      line.classList.add("seq-hop");
      if (kind === "loop") line.classList.add("seq-loop");
      else line.classList.add(CHAIN_CLASS[kind]);
      return line;
    }

    // Drop a measurement file anywhere on the canvas.
    installDropZone() {
      const zone = this.container;
      zone.ondragover = (e) => {
        e.preventDefault();
        zone.classList.add("seq-dropping");
      };
      zone.ondragleave = () => zone.classList.remove("seq-dropping");
      zone.ondrop = (e) => {
        e.preventDefault();
        zone.classList.remove("seq-dropping");
        const file = e.dataTransfer?.files?.[0];
        if (file) this.loadMeasurement(file);
      };
    }
  }

  // 1 / 2 / 5 × 10^k at or above a raw step.
  function niceStep(raw) {
    if (!(raw > 0)) return 1;
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const unit = raw / power;
    const nice = unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10;
    return nice * power;
  }

  window.SequenceDiagramModule = SequenceDiagramModule;
})();
