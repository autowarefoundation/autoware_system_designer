// Sequence Diagram Module
// Event-chain latency view drawn as a timeline: time runs left to right, every
// process gate is a block as wide as its run, and the gaps between blocks are
// the transport, alignment and sampling delays that separate them. Every chain
// of the system is drawn: one group per clock root, from its periodic source
// to the terminals it reaches, stacked down the page on a shared axis. Within a group the chain the axis is
// driven by is the spine on the centre track; the branches that join or leave
// it are packed onto the tracks above and below.

(function () {
  const SVG_NS = ElkCanvas.SVG_NS;
  const T = window.TimingModel;

  // What the numbers are: none, the design's rates alone, or a loaded
  // measurement with the rates filling the gaps.
  const STATES = {
    logical: { button: "logical", timed: false },
    rates: { button: "rates", timed: true },
    measured: { button: "measured", timed: true },
  };

  // The component of every summary the time axis is read from.
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
    trackH: 46,
    blockH: 14,
    blockMinW: 5,
    logicalBlockW: 40,
    logicalColW: 150,
    glyphW: 12,
    glyphH: 8,
    fontSize: 10,
    subSize: 8,
    padLeft: 24,
    padRight: 60,
    padTop: 12,
    padBottom: 16,
    axisH: 24,
    groupHeaderH: 20,
    groupGap: 14,
    targetWidth: 1400,
    labelMin: 18,
    labelTierH: 10,
    nameChars: 22,
  };

  const LOD_NAME = 0.45;
  const LOD_RATE = 0.75;
  const ENUMERATE_LIMIT = 12;
  const CHAIN_LIST_LIMIT = 40;

  const LEGEND = [
    ["block", "a process running; width is its execution time"],
    ["wait", "wait before the run: sampling delay, trigger skew"],
    ["whisker", "±1 sd at the end of a run"],
    ["max", "maximum chain — the critical path"],
    ["min", "minimum chain — the sequential shortest path"],
    ["mean", "mean chain, dashed where it leaves the other two"],
    ["late", "late hop: the message lands after the run it feeds started"],
    ["loop", "loop-closing edge, cut from the solve"],
  ];

  const LEGEND_NOTES = [
    "one group per chain, each starting at its own source firing; all groups share the axis",
    "the tinted track is the chain the axis is driven by; branches sit above and below",
    "≈ marks a mean folded at an and/or gate: one branch's number, not the set's",
    "? marks a spread that skipped hops with no sd",
    "a periodic gate needs no measurement: its sampling delay is uniform over one period",
    "a faint block is a process run no measurement covers",
  ];

  class SequenceDiagramModule extends ElkCanvas {
    // ── Initialization ──────────────────────────────────────────────────────────

    constructor(container, options = {}) {
      super(container, options);
      this.graph = new EventGraph();
      this.groups = [];
      this.activeGroup = null;
      this.hopLimit = null;
      this.showTrivial = false;
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
      if (window.LatencySource) {
        this.measured = await window.LatencySource.loadBundled(
          this.options.mode,
          this.graph,
        );
        if (this.measured) this.state = "measured";
      }
      this.solveAndRender();
    }

    // The sink a group opens on: the deepest chain, then the longest.
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
      return T.designCosts(this.graph);
    }

    // One group per clock root: the chain a periodic source drives, analyzed
    // from the graph. Groups with a single gate are trivial and hidden unless
    // asked for.
    solveAndRender() {
      const solver = new T.ChainSolver(this.graph, this.costs());
      const specs = this.graph.clockRootIds
        .map((id) => ({ id, path: this.graph.ownerOf(id)?.path || "" }))
        .sort((a, b) => a.path.localeCompare(b.path))
        .map(({ id }) => ({ sourceId: id, sinkId: null }));

      if (!specs.length) {
        this.showError("The design declares no events to chain.");
        return;
      }

      const activeSource = this.activeGroup?.sourceId ?? null;
      this.groups = specs.map((spec, index) => {
        const group = { ...spec, index };
        group.solution = solver.solve(spec.sourceId, {
          hopLimit: this.hopLimit,
        });
        group.sinkId = this.defaultSink(group.solution);
        const source = this.graph.events.get(spec.sourceId);
        group.title = `${this.graph.ownerOf(spec.sourceId)?.path || ""}:${source.name}`;
        group.rate = this.rateLabel(source.frequency);
        this.buildView(group);
        group.trivial = group.gates.length <= 1;
        return group;
      });
      this.groups.sort((a, b) => {
        const rankA = a.solution.arrivals.get(a.sinkId).rank;
        const rankB = b.solution.arrivals.get(b.sinkId).rank;
        if (rankA !== rankB) return rankB - rankA;
        return a.title.localeCompare(b.title);
      });
      this.groups.forEach((group, index) => {
        group.index = index;
        group.gates.forEach((gate) => {
          gate.key = `${index}/${gate.id}`;
        });
        group.hops.forEach((hop, hopIndex) => {
          hop.id = `sq_hop_${index}_${hopIndex}`;
        });
      });
      this.activeGroup =
        this.groups.find((group) => group.sourceId === activeSource) ||
        this.visibleGroups()[0] ||
        this.groups[0];
      this.render();
    }

    visibleGroups() {
      return this.groups.filter((group) => this.showTrivial || !group.trivial);
    }

    // ── View model ──────────────────────────────────────────────────────────────

    // The events on some path from the source to the sink, the gates among
    // them, the hops between gates with the ports folded into them, and the
    // tracks the gates sit on. Gates are views over the graph's events, since
    // one event may sit in several groups.
    buildView(group) {
      const { solution } = group;
      const { graph } = this;
      const onPath = new Set([group.sinkId]);
      const queue = [group.sinkId];
      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const arrival = solution.arrivals.get(queue[cursor]);
        (arrival?.branches || []).forEach((branch) => {
          if (onPath.has(branch.fromId)) return;
          onPath.add(branch.fromId);
          queue.push(branch.fromId);
        });
      }
      group.onPath = onPath;

      group.chains = {};
      ["min", "mean", "max"].forEach((component) => {
        const chain = T.chainTo(solution, group.sinkId, component);
        chain.edgeSet = new Set(chain.edges);
        group.chains[component] = chain;
      });

      group.gates = [...onPath]
        .map((id) => graph.events.get(id))
        .filter(
          (event) => event.kind === "process" || event.id === group.sourceId,
        )
        .sort((a, b) => this.orderKey(group, a.id) - this.orderKey(group, b.id))
        .map((event) => ({ ...event, key: `${group.index}/${event.id}` }));
      group.gateIds = new Set(group.gates.map((gate) => gate.id));

      group.hops = [];
      group.gates.forEach((gate) => {
        this.foldBack(group, gate.id).forEach((hop) => group.hops.push(hop));
      });
      group.hops.forEach((hop, index) => {
        hop.id = `sq_hop_${group.index}_${index}`;
        hop.on = {};
        ["min", "mean", "max"].forEach((component) => {
          hop.on[component] = hop.edges.every((edgeId) =>
            group.chains[component].edgeSet.has(edgeId),
          );
        });
      });

      this.assignTracks(group);
    }

    // The gates behind one gate, each with the port events between folded into
    // the hop: the edges it stands for, the transport cost along them, and the
    // topic the message travelled on.
    foldBack(group, gateId) {
      const { solution } = group;
      const { graph } = this;
      const hops = [];
      const walk = (id, edges, comm, topic, branchSummary, depth) => {
        const arrival = solution.arrivals.get(id);
        if (!arrival) return;
        arrival.branches.forEach((branch) => {
          if (!group.onPath.has(branch.fromId)) return;
          const from = graph.events.get(branch.fromId);
          const nextEdges = [branch.key, ...edges];
          const nextComm = T.add(branch.comm, comm);
          const nextTopic =
            topic ??
            (from.kind === "input" || from.kind === "output"
              ? this.topicOf(from)
              : null);
          if (from.kind === "process" || branch.fromId === group.sourceId) {
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
        if (!group.onPath.has(branch.fromId)) return;
        const from = graph.events.get(branch.fromId);
        const topic =
          from.kind === "input" || from.kind === "output"
            ? this.topicOf(from)
            : null;
        if (from.kind === "process" || branch.fromId === group.sourceId) {
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
    orderKey(group, eventId) {
      const arrival = group.solution.arrivals.get(eventId);
      if (!arrival) return Infinity;
      if (!STATES[this.state].timed) return arrival.rank;
      return T.at(arrival.start, this.driver);
    }

    // The chain the spine follows: the axis driver, the mean under a σ driver.
    spineComponent() {
      if (!STATES[this.state].timed) return "max";
      return ["min", "mean", "max"].includes(this.driver)
        ? this.driver
        : "mean";
    }

    // Tracks: the spine on track 0; every other gate continues the track of
    // the gate it feeds, or takes the nearest track beside the spine that is
    // free over its span, sides alternating. Successors are placed before the
    // gates that feed them, so a branch reads as one run leaving the spine.
    // Spans are in time, so gates with no cost share a track at one instant.
    assignTracks(group) {
      const spans = new Map();
      group.gates.forEach((gate) =>
        spans.set(gate.id, this.spanOf(group, gate.id)),
      );
      const trackOf = new Map();
      const occupancy = new Map();
      const overlaps = (track, [x0, x1]) =>
        (occupancy.get(track) || []).some(([a, b]) => x0 < b && a < x1);
      const claim = (id, track) => {
        trackOf.set(id, track);
        if (!occupancy.has(track)) occupancy.set(track, []);
        occupancy.get(track).push(spans.get(id));
      };
      const nearestFree = (from, dir, id) => {
        let track = from;
        while (track === 0 || overlaps(track, spans.get(id))) track += dir;
        return track;
      };

      const spine = group.chains[this.spineComponent()].events.filter((id) =>
        group.gateIds.has(id),
      );
      spine.forEach((id) => claim(id, 0));
      if (!trackOf.has(group.sinkId) && group.gateIds.has(group.sinkId)) {
        claim(group.sinkId, 0);
      }

      const hopsInto = new Map();
      group.hops.forEach((hop) => {
        if (!hopsInto.has(hop.to)) hopsInto.set(hop.to, []);
        hopsInto.get(hop.to).push(hop);
      });
      const rankHop = (hop) =>
        hop.on.max ? 0 : hop.on.mean ? 1 : hop.on.min ? 2 : 3;

      let side = 1;
      const queue = [...spine].reverse();
      const seen = new Set(queue);
      for (let cursor = 0; cursor < queue.length; cursor += 1) {
        const id = queue[cursor];
        const track = trackOf.get(id);
        let continued = track === 0;
        (hopsInto.get(id) || [])
          .slice()
          .sort(
            (a, b) =>
              rankHop(a) - rankHop(b) ||
              spans.get(b.from)[1] - spans.get(a.from)[1],
          )
          .forEach((hop) => {
            if (!trackOf.has(hop.from)) {
              if (!continued && !overlaps(track, spans.get(hop.from))) {
                claim(hop.from, track);
                continued = true;
              } else if (track !== 0) {
                const dir = Math.sign(track);
                claim(hop.from, nearestFree(track + dir, dir, hop.from));
              } else {
                claim(hop.from, nearestFree(side, side, hop.from));
                side = -side;
              }
            }
            if (!seen.has(hop.from)) {
              seen.add(hop.from);
              queue.push(hop.from);
            }
          });
      }
      group.gates.forEach((gate) => {
        if (!trackOf.has(gate.id))
          claim(gate.id, nearestFree(side, side, gate.id));
      });

      // Rows: the tracks in use, top to bottom, empty ones dropped.
      const tracks = [...new Set(trackOf.values())].sort((a, b) => a - b);
      group.rowOf = new Map();
      group.gates.forEach((gate) => {
        group.rowOf.set(gate.id, tracks.indexOf(trackOf.get(gate.id)));
      });
      group.rows = tracks.map((track, index) => ({
        index,
        track,
        spine: track === 0,
        gates: group.gates
          .filter((gate) => trackOf.get(gate.id) === track)
          .sort((a, b) => spans.get(a.id)[0] - spans.get(b.id)[0]),
      }));
    }

    componentOf(path) {
      return `/${(path || "").split("/").filter(Boolean).slice(0, 1).join("/")}`;
    }

    // Top-level components the drawn chains pass, in order of first appearance.
    componentsOnChain() {
      const seen = new Map();
      this.visibleGroups().forEach((group) => {
        group.gates.forEach((gate) => {
          const instance = this.graph.instances.get(gate.ownerId)?.data || {};
          const component = this.componentOf(instance.path);
          if (!seen.has(component)) {
            seen.set(component, {
              component,
              instance: this.graph.instanceByPath.get(component) || {},
            });
          }
        });
      });
      return [...seen.values()];
    }

    shortName(name, limit = VIEW.nameChars) {
      if (name.length <= limit) return name;
      return `…${name.slice(name.length - limit + 1)}`;
    }

    // ── Geometry ────────────────────────────────────────────────────────────────

    // Time to x. In the logical state one column per rank; in a timed state the
    // scale is shared by every group and set so the longest chain lands near
    // the target width, then zoomed.
    prepareScale() {
      const timed = STATES[this.state].timed;
      this.originX = VIEW.padLeft;
      this.originY = VIEW.padTop + VIEW.axisH;
      if (!timed) {
        this.pxPerMs = null;
        return;
      }
      if (this.pxPerMs === null) {
        let extent = 0;
        this.visibleGroups().forEach((group) => {
          group.gates.forEach((gate) => {
            const arrival = group.solution.arrivals.get(gate.id);
            extent = Math.max(
              extent,
              T.at(arrival.total, this.driver) + arrival.total.sd,
            );
          });
        });
        this.pxPerMs = extent > 0 ? VIEW.targetWidth / extent : 1;
      }
    }

    xOf(summary) {
      return this.originX + T.at(summary, this.driver) * this.pxPerMs;
    }

    // Arrival, left and right edge of a gate's block along the axis. Track
    // assignment happens before the scale is known, so a unit scale stands in
    // there: spans are compared, never drawn.
    gateBox(group, gateId) {
      const arrival = group.solution.arrivals.get(gateId);
      if (!STATES[this.state].timed) {
        const left = VIEW.padLeft + arrival.rank * VIEW.logicalColW;
        return {
          arrive: left,
          left,
          right: left + VIEW.logicalBlockW,
          end: left + VIEW.logicalBlockW,
          sdPx: 0,
        };
      }
      const scale = this.pxPerMs ?? 1;
      const x = (summary) => VIEW.padLeft + T.at(summary, this.driver) * scale;
      const arrive = x(arrival.arrive);
      const left = x(arrival.start);
      const end = x(arrival.total);
      return {
        arrive,
        left,
        right: Math.max(end, left + VIEW.blockMinW),
        end,
        sdPx: arrival.total.sd * scale,
      };
    }

    // The time a gate occupies on its track, wait included.
    spanOf(group, gateId) {
      const box = this.gateBox(group, gateId);
      return [box.arrive, box.end];
    }

    rowY(group, gateId) {
      return group.y0 + group.rowOf.get(gateId) * VIEW.trackH + VIEW.trackH / 2;
    }

    // Groups stack down the page, each under its header.
    layoutGroups() {
      let y = this.originY;
      this.visibleGroups().forEach((group) => {
        group.headerY = y;
        group.y0 = y + VIEW.groupHeaderH;
        group.y1 = group.y0 + group.rows.length * VIEW.trackH;
        y = group.y1 + VIEW.groupGap;
      });
      return y - VIEW.groupGap;
    }

    // ── Render ──────────────────────────────────────────────────────────────────

    render() {
      this.prepareScale();
      const { layer } = this.createCanvas();
      this.container.classList.add("sequence-diagram-container");

      this.trackLayer = document.createElementNS(SVG_NS, "g");
      this.axisLayer = document.createElementNS(SVG_NS, "g");
      this.hopLayer = document.createElementNS(SVG_NS, "g");
      this.gateLayer = document.createElementNS(SVG_NS, "g");
      this.labelLayer = document.createElementNS(SVG_NS, "g");
      [
        this.trackLayer,
        this.axisLayer,
        this.hopLayer,
        this.gateLayer,
        this.labelLayer,
      ].forEach((g) => layer.appendChild(g));

      const groups = this.visibleGroups();
      let right = this.originX + VIEW.logicalColW;
      groups.forEach((group) => {
        group.boxes = new Map();
        group.gates.forEach((gate) => {
          const box = this.gateBox(group, gate.id);
          group.boxes.set(gate.id, box);
          right = Math.max(right, box.right + box.sdPx);
        });
      });
      this.width = right + VIEW.padRight;
      this.height = this.layoutGroups() + VIEW.padBottom;

      groups.forEach((group) => this.drawGroup(group));
      this.drawAxis();
      groups.forEach((group) => {
        group.hops.forEach((hop) => this.drawHop(group, hop));
        group.gates.forEach((gate) => this.drawGate(group, gate));
        this.fitLabels(group);
        this.applyEmphasis(group);
      });
      this.activeGroup?.element?.classList.add("seq-group-active");

      this.renderToolbar();
      this.fitToScreen();
      this.applyLOD();
      if (this.selectedId) this.select(this.selectedId, false);
    }

    // A group is its header line and a stripe per track, the spine's tinted.
    drawGroup(group) {
      const g = document.createElementNS(SVG_NS, "g");
      g.classList.add("seq-group");
      g.dataset.group = group.index;
      group.element = g;

      const header = document.createElementNS(SVG_NS, "text");
      header.setAttribute("x", this.originX);
      header.setAttribute("y", group.headerY + VIEW.groupHeaderH - 6);
      header.classList.add("seq-group-title");
      header.style.fontSize = `${VIEW.fontSize}px`;
      const title = document.createElementNS(SVG_NS, "tspan");
      title.textContent = group.title;
      header.appendChild(title);
      const detail = document.createElementNS(SVG_NS, "tspan");
      detail.classList.add("seq-group-detail");
      detail.textContent = ` · ${this.describeGroup(group)}`;
      header.appendChild(detail);
      header.style.cursor = "pointer";
      header.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.selectGroup(group);
      };
      const tip = document.createElementNS(SVG_NS, "title");
      tip.textContent = `${group.title}\nto ${this.graph.ownerOf(group.sinkId)?.path || ""}:${this.graph.events.get(group.sinkId)?.name || ""}`;
      header.appendChild(tip);
      g.appendChild(header);

      group.rows.forEach((row) => {
        const rect = document.createElementNS(SVG_NS, "rect");
        rect.setAttribute("x", this.originX - VIEW.padLeft / 2);
        rect.setAttribute("y", group.y0 + row.index * VIEW.trackH);
        rect.setAttribute("width", this.width - this.originX);
        rect.setAttribute("height", VIEW.trackH);
        rect.classList.add("seq-track");
        if (row.spine) rect.classList.add("seq-track-spine");
        else if (row.index % 2) rect.classList.add("seq-track-alt");
        g.appendChild(rect);
      });
      this.trackLayer.appendChild(g);
    }

    describeGroup(group) {
      const sink = group.solution.arrivals.get(group.sinkId);
      const parts = [];
      if (group.rate) parts.push(group.rate);
      parts.push(`${group.gates.length} gates`);
      if (STATES[this.state].timed) {
        parts.push(`total ${T.formatSummary(sink.total)}`);
      } else {
        parts.push(`depth ${sink.rank}`);
      }
      const measured = this.measuredChain(group);
      if (measured && STATES[this.state].timed) {
        parts.push(`measured ${T.formatSummary(measured.summary)}`);
      }
      if (group.solution.loopEdges.size) {
        parts.push(`${group.solution.loopEdges.size} loop cut`);
      }
      return parts.join(" · ");
    }

    // The measured end-to-end record matching a group: from a timer of the
    // source's node to the sink's node and topic. An input sink is reached
    // through the output feeding it, since chains end at publishes.
    measuredChain(group) {
      if (!this.measured?.chainFor) return null;
      const fromNode = this.graph.ownerOf(group.sourceId)?.path;
      let sinkId = group.sinkId;
      let sink = this.graph.events.get(sinkId);
      if (sink?.kind === "input") {
        const feeder = (this.graph.pred.get(sinkId) || []).find(
          (id) => this.graph.events.get(id)?.kind === "output",
        );
        if (feeder) {
          sinkId = feeder;
          sink = this.graph.events.get(sinkId);
        }
      }
      const toNode = this.graph.ownerOf(sinkId)?.path;
      const topic = sink?.kind === "output" ? this.topicOf(sink) : null;
      return this.measured.chainFor(fromNode, toNode, topic);
    }

    measuredRows(group) {
      const measured = this.measuredChain(group);
      if (!measured) return [];
      return [
        {
          label: `measured e2e${measured.hops ? ` (${measured.hops} hops)` : ""}`,
          value: T.formatSummary(measured.summary),
          source: "measured",
          count: measured.summary.count,
        },
      ];
    }

    // Ticks along the top: milliseconds in a timed state, ranks otherwise, each
    // with a hairline down through every group.
    drawAxis() {
      const g = document.createElementNS(SVG_NS, "g");
      g.classList.add("seq-axis");
      const y = this.originY - 4;
      const axis = document.createElementNS(SVG_NS, "line");
      axis.setAttribute("x1", this.originX);
      axis.setAttribute("x2", this.width - VIEW.padRight / 2);
      axis.setAttribute("y1", y);
      axis.setAttribute("y2", y);
      axis.classList.add("seq-axis-line");
      g.appendChild(axis);

      const tick = (x, label) => {
        const mark = document.createElementNS(SVG_NS, "line");
        mark.setAttribute("x1", x);
        mark.setAttribute("x2", x);
        mark.setAttribute("y1", y - 3);
        mark.setAttribute("y2", this.height - VIEW.padBottom / 2);
        mark.classList.add("seq-axis-tick");
        g.appendChild(mark);
        const text = document.createElementNS(SVG_NS, "text");
        text.setAttribute("x", x);
        text.setAttribute("y", y - 6);
        text.setAttribute("text-anchor", "middle");
        text.textContent = label;
        text.classList.add("seq-axis-label");
        text.style.fontSize = `${VIEW.subSize}px`;
        g.appendChild(text);
      };

      const spanPx = this.width - VIEW.padRight - this.originX;
      if (!STATES[this.state].timed) {
        const columns = Math.round(spanPx / VIEW.logicalColW);
        for (let rank = 0; rank <= columns; rank += 1) {
          tick(this.originX + rank * VIEW.logicalColW, `rank ${rank}`);
        }
      } else {
        const spanMs = spanPx / this.pxPerMs;
        const step = niceStep(spanMs / 10);
        for (let ms = 0; ms <= spanMs + 1e-9; ms += step) {
          tick(this.originX + ms * this.pxPerMs, T.formatMs(ms, 1));
        }
      }
      const title = document.createElementNS(SVG_NS, "text");
      title.setAttribute("x", this.width - VIEW.padRight / 2);
      title.setAttribute("y", y - 6);
      title.setAttribute("text-anchor", "end");
      title.textContent = STATES[this.state].timed
        ? `${this.driverLabel()} · ${this.state}`
        : "rank · logical";
      title.classList.add("seq-axis-label", "seq-axis-title");
      title.style.fontSize = `${VIEW.subSize}px`;
      g.appendChild(title);
      this.axisLayer.appendChild(g);
    }

    driverLabel() {
      return DRIVERS.find(([key]) => key === this.driver)?.[1] || this.driver;
    }

    // A hop runs from the end of one block to the arrival at the next gate:
    // straight along a shared track, a curve between tracks, dashed when both
    // gates belong to one node. A hop landing after its block started is late:
    // the gate fired from another branch. The topic labels the gap.
    drawHop(group, hop) {
      const fromBox = group.boxes.get(hop.from);
      const toBox = group.boxes.get(hop.to);
      if (!fromBox || !toBox) return;
      const timed = STATES[this.state].timed;
      const y1 = this.rowY(group, hop.from);
      const y2 = this.rowY(group, hop.to);
      const x1 = fromBox.right;
      const arrive = timed ? this.xOf(hop.arrival) : toBox.left;
      const x2 = Math.max(arrive, x1 + 4);
      const same = y1 === y2;

      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("id", hop.id);
      if (same) {
        path.setAttribute("d", `M ${x1} ${y1} L ${x2} ${y2}`);
      } else {
        const dx = Math.max(10, (x2 - x1) / 2);
        path.setAttribute(
          "d",
          `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
        );
      }
      const fromOwner = this.graph.events.get(hop.from).ownerId;
      const toOwner = this.graph.events.get(hop.to).ownerId;
      if (fromOwner === toOwner) path.classList.add("seq-hop-internal");
      if (hop.comm?.source === "intra_process")
        path.classList.add("seq-hop-intra");
      if (timed && arrive > toBox.left + 0.5)
        path.classList.add("seq-hop-late");
      path.classList.add("seq-hop");
      path.setAttribute("marker-end", "url(#arrowhead-depth-0)");
      hop.element = path;
      hop.gap = [x1, x2, same];

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeHop(group, hop);
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

      if (hop.topic) {
        const label = document.createElementNS(SVG_NS, "text");
        label.setAttribute("x", (x1 + x2) / 2);
        label.setAttribute(
          "y",
          same ? y1 + VIEW.blockH / 2 + VIEW.subSize : (y1 + y2) / 2 - 3,
        );
        label.setAttribute("text-anchor", "middle");
        label.textContent = this.shortName(hop.topic, 30);
        label.classList.add("seq-hop-label");
        label.style.fontSize = `${VIEW.subSize}px`;
        hop.label = label;
        this.labelLayer.appendChild(label);
      }
    }

    // A gate is a block on its track: the wait segment leads into it, the
    // whisker trails its end, and the line above carries the trigger glyph,
    // node, process and the time it completes.
    drawGate(group, gate) {
      const box = group.boxes.get(gate.id);
      if (!box) return;
      const y = this.rowY(group, gate.id);
      const arrival = group.solution.arrivals.get(gate.id);
      const instance = this.graph.instances.get(gate.ownerId)?.data || {};
      const guide = instance.vis_guide;
      const defaults = this.isDarkMode()
        ? this.styleDefaults.dark
        : this.styleDefaults.light;
      const style = { cornerR: 2 };

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("id", `sq_gate_${group.index}_${gate.id}`);
      g.classList.add("seq-gate");
      g.dataset.eventId = gate.id;
      g.style.cursor = "pointer";
      gate.element = g;

      if (box.arrive < box.left - 0.5) {
        const wait = document.createElementNS(SVG_NS, "rect");
        wait.setAttribute("x", box.arrive);
        wait.setAttribute("y", y - VIEW.blockH / 6);
        wait.setAttribute("width", box.left - box.arrive);
        wait.setAttribute("height", VIEW.blockH / 3);
        wait.classList.add("seq-wait");
        g.appendChild(wait);
      }

      const block = document.createElementNS(SVG_NS, "rect");
      block.setAttribute("x", box.left);
      block.setAttribute("y", y - VIEW.blockH / 2);
      block.setAttribute(
        "width",
        Math.max(VIEW.blockMinW, box.right - box.left),
      );
      block.setAttribute("height", VIEW.blockH);
      block.setAttribute("rx", 1.5);
      block.setAttribute(
        "fill",
        this.themed(guide, "medium_color", defaults.nodeBg),
      );
      block.setAttribute(
        "stroke",
        this.themed(guide, "color", defaults.stroke),
      );
      block.classList.add("seq-bar");
      if (arrival.exec.source === "unmeasured" && STATES[this.state].timed) {
        block.classList.add("seq-unmeasured");
      }
      g.appendChild(block);

      if (box.sdPx > 0.5) {
        const whisker = document.createElementNS(SVG_NS, "path");
        const x0 = Math.max(box.left, box.end - box.sdPx);
        const x1 = box.end + box.sdPx;
        const w = 3;
        whisker.setAttribute(
          "d",
          `M ${x0} ${y} L ${x1} ${y} M ${x0} ${y - w} L ${x0} ${y + w} M ${x1} ${y - w} L ${x1} ${y + w}`,
        );
        whisker.classList.add("seq-whisker");
        if (arrival.total.missingSd)
          whisker.classList.add("seq-whisker-partial");
        g.appendChild(whisker);
      }

      const labelY = y - VIEW.blockH / 2 - 3;
      const glyph = this.buildTypeShape(
        gate.type,
        VIEW.glyphW,
        VIEW.glyphH,
        style,
      );
      glyph.setAttribute(
        "transform",
        `translate(${box.left},${labelY - VIEW.glyphH + 1})`,
      );
      glyph.classList.add("logic-process", "seq-glyph");
      if (!gate.type) glyph.classList.add("logic-process-unknown");
      g.appendChild(glyph);

      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", box.left + VIEW.glyphW + 3);
      label.setAttribute("y", labelY);
      label.classList.add("seq-gate-name");
      label.style.fontSize = `${VIEW.subSize}px`;
      const tail = STATES[this.state].timed
        ? `@${T.formatMs(T.at(arrival.total, this.driver), 1)}`
        : this.rateLabel(gate.frequency);
      gate.label = {
        element: label,
        x: box.left + VIEW.glyphW + 3,
        y: labelY,
        node: this.shortName(instance.name || gate.ownerId),
        process: gate.name,
        tail,
      };
      this.setLabel(gate.label, true);
      label.style.cursor = "pointer";
      label.style.pointerEvents = "auto";
      label.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.selectNode(gate.ownerId);
      };
      const tip = document.createElementNS(SVG_NS, "title");
      tip.textContent = instance.path || "";
      label.appendChild(tip);
      g.appendChild(label);

      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = this.describeGate(group, gate);
      g.appendChild(title);
      g.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.select(gate.key);
      };
      this.gateLayer.appendChild(g);
    }

    // node · process @time as three spans; the node span is dropped first when
    // the room is short.
    setLabel(label, withNode) {
      const text = label.element;
      [...text.querySelectorAll("tspan")].forEach((span) => span.remove());
      const tip = text.querySelector("title");
      const span = (content, cls) => {
        const el = document.createElementNS(SVG_NS, "tspan");
        el.textContent = content;
        if (cls) el.classList.add(cls);
        text.insertBefore(el, tip);
      };
      if (withNode) span(`${label.node} · `, "seq-gate-node");
      span(label.process);
      if (label.tail) span(` ${label.tail}`, "seq-gate-rate");
    }

    labelWidth(label, withNode) {
      const text = `${withNode ? `${label.node} · ` : ""}${label.process}${label.tail ? ` ${label.tail}` : ""}`;
      return this.measureTextWidth(text, VIEW.subSize);
    }

    // Labels sit on two tiers above the blocks of a track. A label takes the
    // lower tier when it fits before the next block, the upper tier when it
    // fits before the block after that; otherwise the node part goes first,
    // then the process name is clipped, then the label is hidden. Hop labels
    // are clipped to their gap the same way.
    fitLabels(group) {
      group.rows.forEach((row) => {
        const tierEnd = [-Infinity, -Infinity];
        row.gates.forEach((gate, index) => {
          const label = gate.label;
          if (!label) return;
          const startOf = (offset) => {
            const next = row.gates[index + offset];
            return next ? group.boxes.get(next.id).left : Infinity;
          };
          const full = this.labelWidth(label, true);
          const short = this.labelWidth(label, false);
          const room = [startOf(1), startOf(2)].map((edge, tier) =>
            label.x < tierEnd[tier] ? -1 : edge - label.x - 4,
          );
          const tier =
            full <= room[0] ? 0 : full <= room[1] || room[1] > room[0] ? 1 : 0;
          const available = room[tier];
          label.element.setAttribute(
            "y",
            label.y - (tier ? VIEW.labelTierH : 0),
          );
          label.element.classList.toggle("seq-gate-name-upper", tier === 1);
          label.element.classList.remove("seq-label-hidden");
          if (full <= available) {
            this.setLabel(label, true);
            tierEnd[tier] = label.x + full;
          } else if (short <= available) {
            this.setLabel(label, false);
            tierEnd[tier] = label.x + short;
          } else if (available < VIEW.labelMin) {
            label.element.classList.add("seq-label-hidden");
          } else {
            const chars = Math.max(
              3,
              Math.floor((label.process.length * available) / short),
            );
            this.setLabel(
              { ...label, process: `${label.process.slice(0, chars - 1)}…` },
              false,
            );
            tierEnd[tier] = label.x + available;
          }
        });
      });
      group.hops.forEach((hop) => {
        if (!hop.label || !hop.gap) return;
        const [x1, x2, same] = hop.gap;
        const room = same ? x2 - x1 - 4 : Math.max(x2 - x1, 40);
        if (room < VIEW.labelMin) {
          hop.label.classList.add("seq-label-hidden");
        } else {
          this._truncateSVGText(
            hop.label,
            this.shortName(hop.topic, 30),
            room,
            VIEW.subSize,
          );
        }
      });
    }

    rateLabel(frequency) {
      if (frequency === null || frequency === undefined) return "";
      if (frequency === 0) return "once";
      return `${Number(frequency.toFixed(3))}Hz`;
    }

    // The three chains of a group at full strength, everything else dimmed; an
    // enumerated chain on show takes the place of all three in its group.
    applyEmphasis(group) {
      const highlight = group === this.activeGroup ? this.highlightEdges : null;
      const chainOf = (hop) => {
        if (highlight) {
          return hop.edges.every((id) => highlight.has(id)) ? "max" : null;
        }
        if (hop.on.max) return "max";
        if (hop.on.min) return "min";
        if (hop.on.mean) return "mean";
        return null;
      };
      const onGate = new Map();
      group.hops.forEach((hop) => {
        const component = chainOf(hop);
        const element = hop.element;
        if (!element) return;
        element.classList.remove(
          ...Object.values(CHAIN_CLASS),
          "seq-dim",
          "seq-on-mean-too",
          "seq-mean-diverges",
        );
        hop.label?.classList.remove(...Object.values(CHAIN_CLASS), "seq-dim");
        if (component) {
          element.classList.add(CHAIN_CLASS[component]);
          hop.label?.classList.add(CHAIN_CLASS[component]);
          element.setAttribute(
            "marker-end",
            `url(#arrowhead-highlighted-${CHAIN_COLOR[component]}-depth-0)`,
          );
          if (hop.on.mean && component !== "mean" && !highlight) {
            element.classList.add("seq-on-mean-too");
          }
          onGate.set(hop.to, true);
          onGate.set(hop.from, true);
        } else {
          element.classList.add("seq-dim");
          hop.label?.classList.add("seq-dim");
          element.setAttribute("marker-end", "url(#arrowhead-depth-0)");
        }
        if (hop.on.mean && !hop.on.max && !hop.on.min && !highlight) {
          element.classList.add("seq-mean-diverges");
        }
      });
      group.gates.forEach((gate) => {
        gate.element?.classList.toggle(
          "seq-dim",
          !onGate.get(gate.id) &&
            gate.id !== group.sourceId &&
            gate.id !== group.sinkId,
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

    describeGate(group, gate) {
      const arrival = group.solution.arrivals.get(gate.id);
      const lines = [
        `${this.graph.ownerOf(gate.id)?.path || ""}:${gate.name}`,
        `${gate.type || "type not declared"} · ${this.rateLabel(gate.frequency) || "no clock"}`,
        `chain ${group.title}`,
      ];
      if (STATES[this.state].timed) {
        lines.push(`cumulative ${T.formatSummary(arrival.total)}`);
      } else {
        lines.push(`rank ${arrival.rank}`);
      }
      return lines.join("\n");
    }

    describeHop(group, hop) {
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

    findHop(id) {
      for (const group of this.groups) {
        const hop = group.hops.find((h) => h.id === id);
        if (hop) return { group, hop };
      }
      return null;
    }

    findGate(key) {
      for (const group of this.groups) {
        const gate = group.gates.find((g) => g.key === key);
        if (gate) return { group, gate };
      }
      return null;
    }

    // Selecting anything in a group makes it the group enumeration acts on.
    select(id, focus = true) {
      this.container
        .querySelectorAll(".seq-selected")
        .forEach((el) => el.classList.remove("seq-selected"));
      this.selectedId = id;
      const hopHit = this.findHop(id);
      if (hopHit) {
        this.setActiveGroup(hopHit.group);
        hopHit.hop.element?.classList.add("seq-selected");
        this.updateInfoPanel(
          this.describeHopPanel(hopHit.group, hopHit.hop),
          "Hop",
        );
        return;
      }
      const gateHit = this.findGate(id);
      if (!gateHit) return;
      this.setActiveGroup(gateHit.group);
      gateHit.gate.element?.classList.add("seq-selected");
      this.updateInfoPanel(
        this.describeGatePanel(gateHit.group, gateHit.gate),
        "Event",
      );
      if (focus) this.focusElement(gateHit.gate.element);
    }

    setActiveGroup(group) {
      if (this.activeGroup === group) return;
      const previous = this.activeGroup;
      this.activeGroup = group;
      if (this.highlightEdges) {
        this.highlightEdges = null;
        this.enumerated = null;
        if (previous) this.applyEmphasis(previous);
      }
      this.container
        .querySelectorAll(".seq-group-active")
        .forEach((el) => el.classList.remove("seq-group-active"));
      group.element?.classList.add("seq-group-active");
      this.updateCounters();
    }

    selectGroup(group) {
      this.setActiveGroup(group);
      const sink = group.solution.arrivals.get(group.sinkId);
      const source = this.graph.events.get(group.sourceId);
      const sinkEvent = this.graph.events.get(group.sinkId);
      this.updateInfoPanel(
        {
          name: group.title,
          from: `${this.graph.ownerOf(group.sourceId)?.path || ""}:${source.name}`,
          to: `${this.graph.ownerOf(group.sinkId)?.path || ""}:${sinkEvent.name}`,
          latency: {
            state: this.state,
            rank: sink.rank,
            rows: [...this.latencyRows(sink), ...this.measuredRows(group)],
            branches: [],
          },
          chain: this.chainReport(
            this.graph.walk([group.sourceId], "up"),
            this.graph.walk([group.sourceId], "down"),
            group.sourceId,
          ),
        },
        "Chain",
      );
    }

    selectNode(ownerId) {
      const instance = this.graph.instances.get(ownerId)?.data || {};
      const gates = new Set();
      this.groups.forEach((group) =>
        group.gates.forEach((gate) => {
          if (gate.ownerId === ownerId) gates.add(gate.id);
        }),
      );
      const measurement = this.measured?.nodeRecord?.(instance.path) || null;
      this.updateInfoPanel(
        { ...instance, gates: gates.size, measurement },
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

    describeGatePanel(group, gate) {
      const arrival = group.solution.arrivals.get(gate.id);
      const owner = this.graph.ownerOf(gate.id) || {};
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
          unknownType: gate.kind === "process" && !gate.type,
          diff: this.declaredDiff(owner.path, gate),
        },
        chain: this.chainReport(upstream, downstream, gate.id),
      };
    }

    // The measurement's declared-versus-observed rows for the outputs a gate feeds.
    declaredDiff(path, gate) {
      if (!this.measured?.diffFor || !path || gate.kind !== "process")
        return [];
      const topics = window.LatencySource.outputTopicsOf(this.graph, gate);
      return topics.length ? this.measured.diffFor(path, topics) : [];
    }

    describeHopPanel(group, hop) {
      const from = this.graph.events.get(hop.from);
      const to = this.graph.events.get(hop.to);
      const arrival = group.solution.arrivals.get(hop.to);
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

    // Every distinct chain of the active group, ranked by max.
    enumerate() {
      const group = this.activeGroup;
      if (!group) return;
      const { chains, total } = T.enumerateChains(
        group.solution,
        group.sinkId,
        { limit: ENUMERATE_LIMIT },
      );
      this.enumerated = chains;
      this.updateInfoPanel(
        {
          name: `chains of ${group.title}`,
          chains: {
            title: `Chains (${chains.length} of ${total})`,
            total,
            items: chains.map((chain, index) => ({
              label: `#${index + 1} ${STATES[this.state].timed ? T.formatSummary(chain.summary) : `${chain.events.size} events`}`,
              detail: `${[...chain.events].filter((id) => group.gateIds.has(id)).length} gates`,
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
      if (this.activeGroup) this.applyEmphasis(this.activeGroup);
      this.updateCounters();
    }

    clearChain() {
      this.highlightEdges = null;
      this.enumerated = null;
      if (this.activeGroup) this.applyEmphasis(this.activeGroup);
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
      this.groups.forEach((group) => this.buildView(group));
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

      bar.appendChild(
        row(
          label("Chains"),
          this.buildTrivialToggle(),
          this.buildHopLimit(),
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

    buildTrivialToggle() {
      const trivial = this.groups.filter((group) => group.trivial).length;
      const wrap = document.createElement("label");
      wrap.className = "logic-toggle";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = this.showTrivial;
      input.disabled = trivial === 0;
      input.onchange = () => {
        this.showTrivial = input.checked;
        this.pxPerMs = null;
        if (this.activeGroup?.trivial && !this.showTrivial) {
          this.activeGroup = this.visibleGroups()[0] || this.activeGroup;
        }
        this.render();
      };
      wrap.appendChild(input);
      wrap.appendChild(
        document.createTextNode(` single-gate clocks (${trivial})`),
      );
      return wrap;
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
      const groups = this.visibleGroups();
      const hidden = this.groups.length - groups.length;
      const nodes = new Set();
      let gates = 0;
      let hops = 0;
      let loops = 0;
      let unknown = 0;
      groups.forEach((group) => {
        group.gates.forEach((gate) => nodes.add(gate.ownerId));
        gates += group.gates.length;
        hops += group.hops.length;
        loops += group.solution.loopEdges.size;
        unknown += group.solution.unknownGates.length;
      });
      const parts = [
        `${groups.length} chain${groups.length === 1 ? "" : "s"}${hidden ? ` (${hidden} single-gate hidden)` : ""}`,
        `${nodes.size} nodes`,
        `${gates} gates`,
        `${hops} hops`,
        `${loops} loop edge${loops === 1 ? "" : "s"} cut`,
      ];
      if (unknown) parts.push(`${unknown} gates without a type (folded as or)`);
      if (this.activeGroup) parts.push(`active: ${this.activeGroup.title}`);
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

      const defaults = this.isDarkMode()
        ? this.styleDefaults.dark
        : this.styleDefaults.light;
      this.componentsOnChain().forEach(({ component, instance }) => {
        const rowEl = document.createElement("div");
        rowEl.className = "logic-legend-row";
        const svg = document.createElementNS(SVG_NS, "svg");
        svg.setAttribute("width", "26");
        svg.setAttribute("height", "14");
        svg.setAttribute("viewBox", "0 0 26 14");
        const rect = document.createElementNS(SVG_NS, "rect");
        rect.setAttribute("x", 2);
        rect.setAttribute("y", 3);
        rect.setAttribute("width", 22);
        rect.setAttribute("height", 8);
        rect.setAttribute("rx", 1.5);
        const guide = instance.vis_guide;
        rect.setAttribute(
          "fill",
          this.themed(guide, "medium_color", defaults.nodeBg),
        );
        rect.setAttribute(
          "stroke",
          this.themed(guide, "color", defaults.stroke),
        );
        rect.classList.add("seq-bar");
        svg.appendChild(rect);
        rowEl.appendChild(svg);
        const span = document.createElement("span");
        span.textContent = component;
        rowEl.appendChild(span);
        details.appendChild(rowEl);
      });
      return details;
    }

    legendGlyph(kind) {
      const make = (tag) => document.createElementNS(SVG_NS, tag);
      if (kind === "block") {
        const rect = make("rect");
        rect.setAttribute("x", 2);
        rect.setAttribute("y", 3);
        rect.setAttribute("width", 22);
        rect.setAttribute("height", 8);
        rect.setAttribute("rx", 1.5);
        rect.classList.add("seq-bar", "seq-legend-bar");
        return rect;
      }
      if (kind === "wait") {
        const rect = make("rect");
        rect.setAttribute("x", 3);
        rect.setAttribute("y", 5.5);
        rect.setAttribute("width", 20);
        rect.setAttribute("height", 3);
        rect.classList.add("seq-wait");
        return rect;
      }
      if (kind === "whisker") {
        const path = make("path");
        path.setAttribute("d", "M 3 7 L 23 7 M 3 4 L 3 10 M 23 4 L 23 10");
        path.classList.add("seq-whisker");
        return path;
      }
      const line = make("path");
      line.setAttribute("d", "M 2 7 L 24 7");
      line.classList.add("seq-hop");
      if (kind === "loop") line.classList.add("seq-loop");
      else if (kind === "late") line.classList.add("seq-hop-late");
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
