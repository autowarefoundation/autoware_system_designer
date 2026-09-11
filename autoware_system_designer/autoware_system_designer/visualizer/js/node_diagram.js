(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const FIT_MARGIN = 40;
  const FIT_SCALE_CAP = 1;
  const MAX_ZOOM = 5;

  // Topic stars span the whole graph, so they are sized in screen pixels and
  // redrawn against the live zoom instead of a layer scale.
  const STAR_LINE_WIDTH = 1.4;
  const STAR_HUB_RADIUS = 7;
  const STAR_FONT_SIZE = 11;

  class NodeDiagramModule extends DiagramBase {
    // ── Initialization ──────────────────────────────────────────────────────────

    constructor(container, options = {}) {
      super(container, options);

      this.currentGraph = null;
      this.currentSvgRoot = null;
      this.graphBBox = null;
      this.transform = { x: 0, y: 0, k: 1 };
      this.isDragging = false;
      this.hasDragged = false;
      this.dragStartRaw = null;
      this.startPoint = { x: 0, y: 0 };
      this.elementData = new Map();
      this.portToEdges = new Map();
      this.portToNode = new Map();
      this.nodeConnectionDirections = new Map();
      this.globalTopics = new Map();
      this.portAbsPos = new Map();
      this.globalOverlay = null;
      this.activeGlobalTopic = null;
      this.colorPresets = null;
      this.styleDefaults = null;

      this.init();
    }

    async init() {
      await DiagramBase.ensureLibrary("ELK", DiagramBase.CDN.elk);
      if (typeof ELK === "undefined") {
        throw new Error("ELK library failed to load");
      }

      // The bundle exposes the constructor directly or under a module wrapper.
      const elkConstructor =
        typeof ELK === "function" ? ELK : ELK.default || ELK.ELK || ELK.Elk;
      if (typeof elkConstructor !== "function") {
        throw new Error("ELK library loaded but constructor not found");
      }

      try {
        this.elk = new elkConstructor();
      } catch (e) {
        throw new Error("Failed to create ELK instance: " + e.message);
      }

      await this.loadAndRender();
    }

    async loadAndRender() {
      try {
        if (!window.systemDesignData?.[this.options.mode]) {
          await this.loadDataScript(this.options.mode, "node_diagram");
        }
        if (!window.systemDesignData?.[this.options.mode]) {
          throw new Error(`No data available for mode: ${this.options.mode}`);
        }
        const elkGraph = this.transformDataToElk(
          window.systemDesignData[this.options.mode],
        );
        await this.layoutAndRenderNodeDiagram(elkGraph);
      } catch (error) {
        console.error("Error loading node diagram:", error);
        this.showError(`Error loading node diagram: ${error.message}`);
      }
    }

    // ── Data transformation ─────────────────────────────────────────────────────

    transformDataToElk(root) {
      this.elementData.clear();
      this.portToEdges.clear();
      this.portToNode.clear();
      this.maxDepth = this.findMaxDepth(root);

      const addPorts = (node, ports, side, style) => {
        (ports || []).forEach((port) => {
          if (!port.unique_id) return;
          const portId = String(port.unique_id);
          this.elementData.set(portId, port);
          this.portToNode.set(portId, node.id);
          node.ports.push({
            id: portId,
            width: style.portSize,
            height: style.portSize,
            properties: { "org.eclipse.elk.port.side": side },
            labels: [
              {
                text: port.name || "Port",
                width: this.measureTextWidth(
                  port.name || "Port",
                  style.portLabelFontSz,
                ),
                height: style.portSize,
              },
            ],
          });
        });
      };

      const convertNode = (instance, depth = 0) => {
        if (!instance?.unique_id) return null;

        const style = this.getLayerStyle(depth);
        const nodeId = String(instance.unique_id);
        this.elementData.set(nodeId, instance);

        const containerTarget = this.getContainerTarget(instance);
        const maxPorts = Math.max(
          (instance.in_ports || []).length,
          (instance.out_ports || []).length,
        );
        const nodeHeight = Math.max(
          style.nodeBaseH,
          style.nodeBaseH +
            maxPorts * (style.portSize + style.portSpacing * 2) +
            (containerTarget ? style.badgeH + style.badgePad : 0),
        );

        const node = {
          id: nodeId,
          labels: [
            { text: instance.namespace || "" },
            { text: instance.name || nodeId || "Unnamed" },
          ],
          namespace: instance.namespace || "",
          width: this.calculateNodeWidth(instance, style),
          height: nodeHeight,
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

        addPorts(node, instance.in_ports, "WEST", style);
        addPorts(node, instance.out_ports, "EAST", style);

        if (instance.children?.length > 0) {
          node.children = instance.children
            .map((child) => convertNode(child, depth + 1))
            .filter(Boolean);
          if (node.children.length > 0) {
            delete node.width;
            delete node.height;
          }
        }

        if (instance.links) {
          node.edges = instance.links
            .map((link) => {
              if (!link.from_port || !link.to_port) return null;
              const edgeId = link.unique_id;
              this.elementData.set(edgeId, link);

              const fromId = String(link.from_port.unique_id);
              const toId = String(link.to_port.unique_id);

              if (!this.portToEdges.has(fromId))
                this.portToEdges.set(fromId, []);
              this.portToEdges.get(fromId).push(edgeId);
              if (!this.portToEdges.has(toId)) this.portToEdges.set(toId, []);
              this.portToEdges.get(toId).push(edgeId);

              return {
                id: edgeId,
                sources: [fromId],
                targets: [toId],
                properties: {},
              };
            })
            .filter(Boolean);
        }

        return node;
      };

      const rootNode = convertNode(root);
      if (rootNode) {
        delete rootNode.width;
        delete rootNode.height;
        if (!rootNode.id) rootNode.id = "root";
      }
      this._injectRemapHub(rootNode);
      this._buildGlobalTopicIndex();
      return rootNode;
    }

    // Global ports carry no link, so the topic name is the only thing that groups
    // them. Ports that inherited is_global through a reference chain are members
    // of the group, but only node-owned ports are endpoints of the topic star.
    _buildGlobalTopicIndex() {
      this.globalTopics.clear();

      for (const [id, data] of this.elementData) {
        if (!this._isGlobalPort(data)) continue;
        if (!this.portToNode.has(id)) continue;

        const topicKey = "/" + data.topic.join("/");
        let entry = this.globalTopics.get(topicKey);
        if (!entry) {
          entry = {
            topic: topicKey,
            members: [],
            publishers: [],
            subscribers: [],
          };
          this.globalTopics.set(topicKey, entry);
        }
        entry.members.push(id);

        const owner = this.elementData.get(this.portToNode.get(id));
        if (owner?.entity_type !== "node") continue;
        const direction = this._getPortDirection(id);
        if (direction === "downstream") entry.publishers.push(id);
        else if (direction === "upstream") entry.subscribers.push(id);
      }
    }

    // A remap owns the topic of the port it rewrites, so it takes the global key's
    // place; those ports belong to the remap hub instead.
    _isGlobalPort(portData) {
      return (
        portData?.is_global === true &&
        portData.is_remapped !== true &&
        portData.topic?.length > 0
      );
    }

    _globalTopicOf(portData) {
      if (!this._isGlobalPort(portData)) return null;
      return this.globalTopics.get("/" + portData.topic.join("/")) || null;
    }

    findMaxDepth(instance, depth = 0) {
      if (!instance?.children?.length) return depth;
      return Math.max(
        ...instance.children.map((c) => this.findMaxDepth(c, depth + 1)),
      );
    }

    getContainerTarget(data) {
      if (!data) return "";
      return (
        data.container_target ||
        data.launch?.container_target ||
        data.launch_config?.container_target ||
        data.launcher?.container_target ||
        ""
      );
    }

    _injectRemapHub(rootNode) {
      // Only collect boundary ports of top-level modules — inner ports also receive
      // is_remapped=true via _force_remap_port reference-chain propagation, so we
      // must restrict to ports whose parent node is a direct child of rootNode.
      const topLevelNodeIds = new Set(
        (rootNode?.children || []).map((c) => c.id),
      );
      const remappedPortEntries = [];
      for (const [id, data] of this.elementData) {
        if (
          data.is_remapped === true &&
          topLevelNodeIds.has(this.portToNode.get(id))
        ) {
          remappedPortEntries.push({ id, data });
        }
      }
      if (remappedPortEntries.length === 0 || !rootNode) return;

      const style = this.getLayerStyle(1);
      const remapNodeId = "__remap_hub__";

      this.elementData.set(remapNodeId, {
        unique_id: remapNodeId,
        name: "Remap Hub",
        namespace: "System Remaps",
        entity_type: "remap_hub",
      });

      const hubNode = {
        id: remapNodeId,
        labels: [{ text: "System Remaps" }, { text: "Remap Hub" }],
        namespace: "System Remaps",
        width: 0,
        height: 0,
        children: [],
        ports: [],
        properties: {
          "org.eclipse.elk.portConstraints": "FIXED_SIDE",
          "org.eclipse.elk.nodeLabels.placement": "H_CENTER V_TOP",
          "org.eclipse.elk.portLabels.placement": "INSIDE",
          "org.eclipse.elk.portAlignment.default": "CENTER",
          "org.eclipse.elk.spacing.portPort": String(style.portSpacing),
          "org.eclipse.elk.spacing.nodeNode": String(style.nodeSpacing),
          "org.eclipse.elk.padding": `[top=${style.elkPadding},left=${style.elkPadding},bottom=${style.elkPadding},right=${style.elkPadding}]`,
        },
      };

      let maxTopicW = 0;
      remappedPortEntries.forEach(({ id, data }) => {
        const hubPortId = `__remap_hub_port__${id}`;
        const topicName = data.topic?.length
          ? "/" + data.topic.join("/")
          : data.name || "unknown";
        maxTopicW = Math.max(
          maxTopicW,
          this.measureTextWidth(topicName, style.portLabelFontSz),
        );

        this.elementData.set(hubPortId, {
          unique_id: hubPortId,
          name: topicName,
          is_remap_hub_port: true,
          original_port_id: id,
          topic: data.topic,
          msg_type: data.msg_type || "remap",
        });
        this.portToNode.set(hubPortId, remapNodeId);

        hubNode.ports.push({
          id: hubPortId,
          width: style.portSize,
          height: style.portSize,
          properties: { "org.eclipse.elk.port.side": "WEST" },
          labels: [
            {
              text: topicName,
              width: this.measureTextWidth(topicName, style.portLabelFontSz),
              height: style.portSize,
            },
          ],
        });
      });

      const innerPad = style.portSize * 3;
      hubNode.width = Math.max(
        style.nodeWidth,
        maxTopicW + innerPad,
        this.measureTextWidth("Remap Hub", style.fontSize) + innerPad,
      );
      hubNode.height = Math.max(
        style.nodeBaseH,
        style.nodeBaseH +
          remappedPortEntries.length * (style.portSize + style.portSpacing * 2),
      );

      if (!rootNode.children) rootNode.children = [];
      rootNode.children.push(hubNode);

      if (!rootNode.edges) rootNode.edges = [];
      remappedPortEntries.forEach(({ id }) => {
        const hubPortId = `__remap_hub_port__${id}`;
        const edgeId = `__remap_edge__${id}`;
        const sources = [id];
        const targets = [hubPortId];

        this.elementData.set(edgeId, {
          unique_id: edgeId,
          from_port: { unique_id: sources[0] },
          to_port: { unique_id: targets[0] },
          is_remap_edge: true,
        });
        [sources[0], targets[0]].forEach((portId) => {
          if (!this.portToEdges.has(portId)) this.portToEdges.set(portId, []);
          this.portToEdges.get(portId).push(edgeId);
        });
        rootNode.edges.push({ id: edgeId, sources, targets, properties: {} });
      });
    }

    // ── Styling / metrics ────────────────────────────────────────────────────────

    getLayerScale(depth) {
      const SCALE_RATIO = 1.9;
      return Math.pow(SCALE_RATIO, this.maxDepth - depth);
    }

    getLayerStyle(depth) {
      const s = this.getLayerScale(depth);
      return {
        nodeWidth: Math.round(120 * s),
        nodeBaseH: Math.round(44 * s),
        portSize: Math.round(5 * s),
        portSpacing: Math.round(2.5 * s),
        nodeSpacing: Math.round(5 * s),
        edgeNodeSpacing: Math.round(2 * s),
        edgeNodeBetweenLayers: Math.round(2 * s),
        edgeEdgeSpacing: Math.round(3 * s),
        edgeEdgeBetweenLayers: Math.round(3 * s),
        elkPadding: Math.round(20 * s),
        fontSize: Math.round(8 * s),
        nsSize: Math.round(5 * s),
        cornerR: Math.max(1, Math.round(2 * s)),
        borderW: (1.5 * s).toFixed(1),
        edgeW: (0.3 * s).toFixed(1),
        portLabelFontSz: Math.round(5 * s),
        portLabelOffset: Math.round(3 * s),
        badgeH: Math.round(8 * s),
        badgePad: Math.round(3 * s),
        badgeCharW: Math.round(3 * s),
        badgeFontSz: Math.round(4 * s),
        arrowW: (2 * s).toFixed(1),
        arrowH: (1.4 * s).toFixed(1),
      };
    }

    measureTextWidth(text, fontSize) {
      if (!this._measureCtx) {
        this._measureCtx = document.createElement("canvas").getContext("2d");
        this._textMeasureCache = new Map();
        this._measureFontFamily = null;
      }
      if (!this._measureFontFamily) {
        this._measureFontFamily =
          getComputedStyle(this.container).fontFamily || "sans-serif";
      }
      const key = `${fontSize}|${text}`;
      if (this._textMeasureCache.has(key))
        return this._textMeasureCache.get(key);
      const font = `${fontSize}px ${this._measureFontFamily}`;
      if (this._measureCtx.font !== font) this._measureCtx.font = font;
      const width = this._measureCtx.measureText(text).width;
      this._textMeasureCache.set(key, width);
      return width;
    }

    calculateNodeWidth(instance, style) {
      const maxWestLabelW = (instance.in_ports || []).reduce(
        (max, p) =>
          Math.max(
            max,
            this.measureTextWidth(p.name || "Port", style.portLabelFontSz),
          ),
        0,
      );
      const maxEastLabelW = (instance.out_ports || []).reduce(
        (max, p) =>
          Math.max(
            max,
            this.measureTextWidth(p.name || "Port", style.portLabelFontSz),
          ),
        0,
      );
      const titleName = instance.name || String(instance.unique_id) || "";
      const titleW = this.measureTextWidth(titleName, style.fontSize);
      const innerPad = style.portSize * 3;
      return Math.max(
        style.nodeWidth,
        maxWestLabelW + maxEastLabelW + innerPad,
        titleW + innerPad,
      );
    }

    // ── Layout + render ──────────────────────────────────────────────────────────

    async layoutAndRenderNodeDiagram(graphData) {
      if (!this.elk) throw new Error("ELK instance not initialized");

      const graph = await this.elk.layout(graphData, {
        layoutOptions: {
          algorithm: "layered",
          "org.eclipse.elk.direction": "RIGHT",
          "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
          // Layer assignment minimizes total edge length, so connected boxes stay
          // adjacent. Placement keeps the linear-time Brandes-Koepf pass with its
          // edge-straightening step off, which is what spreads boxes apart.
          "org.eclipse.elk.layered.layering.strategy": "NETWORK_SIMPLEX",
          "org.eclipse.elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
          "org.eclipse.elk.layered.nodePlacement.bk.edgeStraightening": "NONE",
          // Horizontal compaction pass over the placed graph. SCANLINE
          // constraints reject this graph's hitboxes.
          "org.eclipse.elk.layered.compaction.postCompaction.strategy": "LEFT",
          "org.eclipse.elk.layered.compaction.postCompaction.constraints":
            "QUADRATIC",
          "org.eclipse.elk.padding": "[top=50,left=50,bottom=50,right=50]",
        },
      });

      this.renderNodeDiagram(graph);
      this.fitToScreen();
    }

    renderNodeDiagram(graph) {
      this.container.innerHTML = "";
      this.globalOverlay = null;
      this.activeGlobalTopic = null;

      const svgRoot = document.createElementNS(SVG_NS, "svg");
      svgRoot.setAttribute("width", "100%");
      svgRoot.setAttribute("height", "100%");
      svgRoot.style.width = "100%";
      svgRoot.style.height = "100%";
      svgRoot.style.cursor = "grab";

      const svg = document.createElementNS(SVG_NS, "g");
      svg.id = "zoom-layer";
      svgRoot.appendChild(svg);
      this.container.appendChild(svgRoot);

      this.setupZoomPan(svgRoot, svg);
      this.updateTransform(svg);
      this._computeThemeStyles();

      const computedStyle = getComputedStyle(document.documentElement);
      const arrowColor = this.isDarkMode()
        ? computedStyle.getPropertyValue("--text-muted").trim() || "#6c757d"
        : computedStyle.getPropertyValue("--border-hover").trim() || "#adb5bd";

      svgRoot.insertBefore(this._buildArrowDefs(arrowColor), svg);

      this.portAbsPos.clear();
      this.renderNode(graph, svg);

      // Topic stars are drawn last so straight lines cross over the boxes they
      // connect; the layer never takes pointer events.
      this.globalOverlay = document.createElementNS(SVG_NS, "g");
      this.globalOverlay.setAttribute("id", "global-topic-overlay");
      this.globalOverlay.setAttribute("pointer-events", "none");
      svg.appendChild(this.globalOverlay);

      this.currentGraph = graph;
      this.currentSvgRoot = svgRoot;
    }

    _computeThemeStyles() {
      const newFontFamily =
        getComputedStyle(this.container).fontFamily || "sans-serif";
      if (newFontFamily !== this._measureFontFamily) {
        this._measureFontFamily = newFontFamily;
        this._textMeasureCache?.clear();
      }

      const cs = getComputedStyle(document.documentElement);

      this.colorPresets = {
        default: {
          name: "default",
          edge: cs.getPropertyValue("--highlight").trim() || "#0d6efd",
          port: cs.getPropertyValue("--highlight").trim() || "#0d6efd",
        },
        red: { name: "red", edge: "#dc3545", port: "#dc3545" },
        green: { name: "green", edge: "#28a745", port: "#28a745" },
        orange: { name: "orange", edge: "#fd7e14", port: "#fd7e14" },
        purple: { name: "purple", edge: "#6f42c1", port: "#6f42c1" },
        teal: { name: "teal", edge: "#20c997", port: "#20c997" },
      };

      this.styleDefaults = {
        dark: {
          bg: cs.getPropertyValue("--bg-secondary").trim() || "#2d2d2d",
          nodeBg: cs.getPropertyValue("--bg-secondary").trim() || "#2d2d2d",
          stroke: cs.getPropertyValue("--text-muted").trim() || "#666",
          text: cs.getPropertyValue("--text-primary").trim() || "#e9ecef",
          rootBg: "#1e1e1e",
        },
        light: {
          bg: cs.getPropertyValue("--bg-secondary").trim() || "#ffffff",
          nodeBg: cs.getPropertyValue("--bg-secondary").trim() || "#ffffff",
          stroke: "#333",
          text: cs.getPropertyValue("--text-primary").trim() || "#333",
          rootBg: "#f5f5f5",
        },
      };
    }

    _buildArrowDefs(arrowColor) {
      const defs = document.createElementNS(SVG_NS, "defs");
      const maxDepth = this.maxDepth || 0;

      const markup = Array.from({ length: maxDepth + 1 }, (_, d) => {
        const { arrowW: mw, arrowH: mh } = this.getLayerStyle(d);
        const rx = mw;
        const ry = +(mh / 2).toFixed(2);
        const coloredMarkers = Object.keys(this.colorPresets)
          .map(
            (preset) =>
              `<marker id="arrowhead-highlighted-${preset}-depth-${d}" markerWidth="${mw}" markerHeight="${mh}" refX="${rx}" refY="${ry}" orient="auto" markerUnits="userSpaceOnUse">` +
              `<polygon points="0 0, ${mw} ${ry}, 0 ${mh}" fill="${this.colorPresets[preset].edge}" /></marker>`,
          )
          .join("");
        return (
          `<marker id="arrowhead-depth-${d}" markerWidth="${mw}" markerHeight="${mh}" refX="${rx}" refY="${ry}" orient="auto" markerUnits="userSpaceOnUse">` +
          `<polygon points="0 0, ${mw} ${ry}, 0 ${mh}" fill="${arrowColor}" /></marker>` +
          coloredMarkers
        );
      }).join("");

      // Scaled by the line width, which the star keeps constant on screen.
      const globalMarkers = Object.keys(this.colorPresets)
        .map(
          (preset) =>
            `<marker id="arrowhead-global-${preset}" markerWidth="4" markerHeight="3" refX="4" refY="1.5" orient="auto" markerUnits="strokeWidth">` +
            `<polygon points="0 0, 4 1.5, 0 3" fill="${this.colorPresets[preset].edge}" /></marker>`,
        )
        .join("");

      defs.innerHTML = markup + globalMarkers;
      return defs;
    }

    renderNode(node, parentGroup, depth = 0, originX = 0, originY = 0) {
      const style = this.getLayerStyle(depth);
      const userData = this.elementData.get(node.id) || {};
      // ELK coordinates are parent-relative; the topic star draws straight lines
      // across the hierarchy and needs them in the zoom layer's own space.
      const absX = originX + (node.x || 0);
      const absY = originY + (node.y || 0);

      const g = document.createElementNS(SVG_NS, "g");
      g.setAttribute("transform", `translate(${node.x},${node.y})`);
      g.setAttribute("id", node.id);
      g.classList.add("node-group");

      g.appendChild(this._buildNodeRect(node, depth, userData, style));

      const containerTarget = this.getContainerTarget(userData);
      if (containerTarget) {
        this._appendBadge(g, node, style, containerTarget);
      }

      if (node.labels?.length > 0) {
        this._appendLabels(g, node, userData, style, depth);
      }

      if (node.ports) {
        node.ports.forEach((port) => {
          const cx = (port.x || 0) + port.width / 2;
          this.portAbsPos.set(port.id, {
            x: absX + cx,
            y: absY + (port.y || 0) + port.height / 2,
            r: port.width / 2,
            side: cx > (node.width || 0) / 2 ? "EAST" : "WEST",
          });
          g.appendChild(this._buildPortGroup(port, userData, style));
        });
      }

      if (node.children) {
        node.children.forEach((child) =>
          this.renderNode(child, g, depth + 1, absX, absY),
        );
      }

      if (node.edges) {
        node.edges.forEach((edge) => {
          if (!edge.sections) return;
          g.appendChild(this._buildEdgePath(edge, depth, style));
        });
      }

      parentGroup.appendChild(g);
    }

    _buildNodeRect(node, depth, userData, style) {
      const visGuide = userData.vis_guide || {};
      const defaults = this.styleDefaults;

      let fillColor, strokeColor;
      if (this.isDarkMode()) {
        fillColor =
          visGuide.dark_background_color ||
          visGuide.background_color ||
          defaults.dark.bg;
        if (userData.entity_type === "node") {
          fillColor =
            visGuide.dark_medium_color ||
            visGuide.medium_color ||
            defaults.dark.nodeBg;
        }
        strokeColor =
          visGuide.dark_color || visGuide.color || defaults.dark.stroke;
        if (depth === 0) fillColor = defaults.dark.rootBg;
      } else {
        fillColor = visGuide.background_color || defaults.light.bg;
        if (userData.entity_type === "node") {
          fillColor = visGuide.medium_color || defaults.light.nodeBg;
        }
        strokeColor = visGuide.color || defaults.light.stroke;
        if (depth === 0) fillColor = defaults.light.rootBg;
      }

      if (userData.entity_type === "remap_hub") {
        fillColor = this.isDarkMode() ? "#2a1800" : "#fff8e1";
        strokeColor = this.isDarkMode()
          ? defaults.dark.stroke
          : defaults.light.stroke;
      }

      const rect = document.createElementNS(SVG_NS, "rect");
      rect.setAttribute("width", node.width);
      rect.setAttribute("height", node.height);
      rect.setAttribute("rx", style.cornerR);
      rect.setAttribute("fill", fillColor);
      rect.setAttribute("stroke", strokeColor);
      rect.setAttribute("stroke-width", style.borderW);
      if (userData.entity_type === "remap_hub") {
        const dw = parseFloat(style.borderW);
        rect.setAttribute("stroke-dasharray", `${dw * 5} ${dw * 2.5}`);
        rect.setAttribute("stroke-linecap", "round");
      }
      rect.classList.add("node-rect");

      rect.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.updateInfoPanel(userData, "Node");
        this.clearHighlights();
        if (depth === 0) return;

        const nodeGroup = document.getElementById(node.id);

        if (userData.entity_type === "remap_hub") {
          nodeGroup?.classList.add("highlighted");
          for (const [portId, nodeId] of this.portToNode) {
            if (nodeId !== node.id) continue;
            this._applyPortHighlight(portId, "orange");
            for (const edgeId of this.portToEdges.get(portId) || []) {
              const edgeData = this.elementData.get(edgeId);
              if (!edgeData?.is_remap_edge) continue;
              this._applyEdgeHighlight(edgeId, "orange");
              const fromId = String(edgeData.from_port?.unique_id ?? "");
              const toId = String(edgeData.to_port?.unique_id ?? "");
              const originalPortId = fromId === portId ? toId : fromId;
              if (originalPortId)
                this._applyPortHighlight(originalPortId, "orange");
            }
          }
          return;
        }

        if (node.children?.length) {
          this.highlightModule(node, nodeGroup);
        } else {
          nodeGroup?.classList.add("highlighted");
        }

        const outwardInPortIds = (userData.in_ports || [])
          .filter((p) => p.unique_id && p.is_outward !== false)
          .map((p) => String(p.unique_id));
        const outwardOutPortIds = (userData.out_ports || [])
          .filter((p) => p.unique_id && p.is_outward !== false)
          .map((p) => String(p.unique_id));

        if (outwardInPortIds.length > 0)
          this.highlightBoundaryChain(outwardInPortIds, "upstream", "green");
        if (outwardOutPortIds.length > 0)
          this.highlightBoundaryChain(
            outwardOutPortIds,
            "downstream",
            "orange",
          );
      };

      return rect;
    }

    _appendBadge(g, node, style, containerTarget) {
      const badgeText = String(containerTarget);
      const badgeH = style.badgeH;
      const badgePad = style.badgePad;
      const badgeWidth = Math.min(
        node.width - badgePad * 2,
        Math.max(
          badgeH * 2,
          this.measureTextWidth(badgeText, style.badgeFontSz) + badgePad * 2,
        ),
      );
      const badgeX = (node.width - badgeWidth) / 2;
      const badgeY = node.height - badgeH - badgePad;

      const badgeRect = document.createElementNS(SVG_NS, "rect");
      badgeRect.setAttribute("x", badgeX);
      badgeRect.setAttribute("y", badgeY);
      badgeRect.setAttribute("width", badgeWidth);
      badgeRect.setAttribute("height", badgeH);
      badgeRect.setAttribute(
        "rx",
        Math.max(1, Math.round(style.cornerR * 0.6)),
      );
      badgeRect.setAttribute("stroke-width", style.borderW);
      badgeRect.style.fill = this.isDarkMode() ? "rgba(0,0,0,0.25)" : "#e9ecef";
      badgeRect.style.stroke = this.isDarkMode() ? "#6c757d" : "#adb5bd";
      g.appendChild(badgeRect);

      const badgeLabel = document.createElementNS(SVG_NS, "text");
      badgeLabel.setAttribute("x", node.width / 2);
      badgeLabel.setAttribute("y", badgeY + badgeH / 2 + 0.5);
      this._truncateSVGText(
        badgeLabel,
        badgeText,
        badgeWidth - badgePad * 2,
        style.badgeFontSz,
      );
      badgeLabel.classList.add("node-label");
      badgeLabel.style.fontSize = style.badgeFontSz + "px";
      badgeLabel.style.fill = this.isDarkMode() ? "#dee2e6" : "#495057";
      g.appendChild(badgeLabel);
    }

    _appendLabels(g, node, userData, style, depth) {
      const visGuide = userData.vis_guide || {};
      const fontSize = style.fontSize;
      let yOffset = Math.round(fontSize * 0.8);

      if (node.labels.length > 1 && node.labels[0].text) {
        const nsText = document.createElementNS(SVG_NS, "text");
        nsText.setAttribute("x", node.width / 2);
        nsText.setAttribute("y", yOffset);
        nsText.classList.add("node-label");
        nsText.style.fontSize = style.nsSize + "px";
        nsText.style.fill = this.isDarkMode()
          ? visGuide.dark_text_color || "#adb5bd"
          : visGuide.text_color || "#6c757d";
        const nsLines = this._wrapSVGText(
          nsText,
          node.namespace,
          node.width / 2,
          node.width - style.badgePad * 2,
          style.nsSize,
        );
        g.appendChild(nsText);
        yOffset += (style.nsSize + 2) * nsLines;
      }

      const nameText = document.createElementNS(SVG_NS, "text");
      nameText.setAttribute("x", node.width / 2);
      nameText.setAttribute("y", yOffset + fontSize / 2);
      nameText.textContent = node.labels[node.labels.length - 1].text;
      nameText.classList.add("node-label");
      nameText.style.fontSize = `${fontSize}px`;
      nameText.style.fill = this.isDarkMode()
        ? visGuide.dark_text_color || "#e9ecef"
        : visGuide.text_color || "#333";
      if (depth <= 1) nameText.style.fontWeight = "bold";
      g.appendChild(nameText);
    }

    _buildPortGroup(port, userData, style) {
      const portData = this.elementData.get(port.id) || {};
      const isRemapHub = portData.is_remap_hub_port === true;
      const isRemapped = portData.is_remapped === true;
      const isGlobal = portData.is_global === true;
      const visGuide = userData.vis_guide || {};

      let prect;
      if (isRemapped) {
        // Circle: topic overridden by a system remap entry
        prect = document.createElementNS(SVG_NS, "circle");
        const r = port.width / 2;
        prect.setAttribute("cx", r);
        prect.setAttribute("cy", r);
        prect.setAttribute("r", r);
      } else if (isGlobal) {
        // Diamond: topic fixed by node-level global key
        prect = document.createElementNS(SVG_NS, "polygon");
        const ps = port.width;
        const h = ps / 2;
        prect.setAttribute(
          "points",
          `${h},${-h * 0.4} ${ps + h * 0.4},${h} ${h},${ps + h * 0.4} ${-h * 0.4},${h}`,
        );
      } else {
        prect = document.createElementNS(SVG_NS, "rect");
        prect.setAttribute("width", port.width);
        prect.setAttribute("height", port.height);
      }
      prect.classList.add("port-rect");
      if (isGlobal && !isRemapped) prect.classList.add("port-global");

      const topicHint =
        !isRemapHub && portData.topic?.length
          ? " → /" + portData.topic.join("/")
          : "";
      const titlePrefix = isRemapHub
        ? "[remap-topic]"
        : isRemapped
          ? "[remap]"
          : isGlobal
            ? "[global]"
            : "";
      const title = document.createElementNS(SVG_NS, "title");
      title.textContent = titlePrefix
        ? `${titlePrefix} ${portData.name || "Port"}${topicHint}`
        : portData.name || "Port";
      prect.appendChild(title);

      const pg = document.createElementNS(SVG_NS, "g");
      pg.setAttribute("id", port.id);
      pg.setAttribute("transform", `translate(${port.x},${port.y})`);
      pg.style.cursor = "pointer";
      pg.appendChild(prect);

      pg.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        const topicEntry = this._globalTopicOf(portData);
        this.updateInfoPanel(
          topicEntry
            ? {
                ...portData,
                global_topic: this._describeGlobalTopic(topicEntry),
              }
            : portData,
          "Port",
        );
        this.highlightConnected(port.id);
        if (topicEntry) this.highlightGlobalTopic(topicEntry);
      };

      if (port.labels) {
        port.labels.forEach((label) => {
          const text = document.createElementNS(SVG_NS, "text");
          const lx = (label.x || 0) + (label.width || 0) / 2;
          const offsetDir = lx >= 0 ? 1 : -1;
          text.setAttribute("x", lx + offsetDir * style.portLabelOffset);
          text.setAttribute("y", (label.y || 0) + (label.height || 0) / 2);
          text.textContent = label.text;
          text.classList.add("port-label");
          text.style.fontSize = style.portLabelFontSz + "px";
          text.style.fill = this.isDarkMode()
            ? visGuide.dark_text_color || "#e9ecef"
            : visGuide.text_color || "#333";
          pg.appendChild(text);
        });
      }

      return pg;
    }

    _buildEdgePath(edge, depth, style) {
      let d = "";
      edge.sections.forEach((section) => {
        d += `M ${section.startPoint.x} ${section.startPoint.y} `;
        if (section.bendPoints) {
          section.bendPoints.forEach((bp) => (d += `L ${bp.x} ${bp.y} `));
        }
        d += `L ${section.endPoint.x} ${section.endPoint.y} `;
      });

      const edgeData = this.elementData.get(edge.id) || {};

      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("id", edge.id);
      path.setAttribute("d", d);
      path.classList.add("edge-path");
      path.setAttribute("data-depth", String(depth));
      path.setAttribute("stroke-width", style.edgeW);

      path.setAttribute("marker-end", `url(#arrowhead-depth-${depth})`);
      if (edgeData.is_remap_edge) {
        const ew = parseFloat(style.edgeW);
        path.setAttribute("stroke-dasharray", `${ew} ${ew * 6}`);
        path.setAttribute("stroke-linecap", "round");
      }

      path.onclick = (e) => {
        if (this.hasDragged) return;
        e.stopPropagation();
        this.updateInfoPanel(edgeData, "Link");
        this.highlightConnected(edge.id);
      };

      return path;
    }

    // ── Text utilities ────────────────────────────────────────────────────────────

    _wrapSVGText(textEl, text, x, maxWidth, fontSize) {
      if (this.measureTextWidth(text, fontSize) <= maxWidth) {
        textEl.textContent = text;
        return 1;
      }
      textEl.textContent = "";
      const lines = [];
      let remaining = text;
      while (remaining.length > 0) {
        if (this.measureTextWidth(remaining, fontSize) <= maxWidth) {
          lines.push(remaining);
          break;
        }
        let lo = 1,
          hi = remaining.length - 1;
        while (lo < hi) {
          const mid = Math.ceil((lo + hi) / 2);
          if (
            this.measureTextWidth(remaining.slice(0, mid), fontSize) <= maxWidth
          ) {
            lo = mid;
          } else {
            hi = mid - 1;
          }
        }
        let breakIdx = lo;
        for (let i = lo; i >= Math.ceil(lo * 0.5); i--) {
          if (remaining[i] === "/" || remaining[i] === "_") {
            breakIdx = i + 1;
            break;
          }
        }
        lines.push(remaining.slice(0, breakIdx));
        remaining = remaining.slice(breakIdx);
      }
      const lineSpacing = fontSize + 2;
      lines.forEach((line, i) => {
        const tspan = document.createElementNS(SVG_NS, "tspan");
        tspan.setAttribute("x", x);
        if (i > 0) tspan.setAttribute("dy", lineSpacing + "px");
        tspan.textContent = line;
        textEl.appendChild(tspan);
      });
      return lines.length;
    }

    _truncateSVGText(textEl, text, maxWidth, fontSize) {
      if (this.measureTextWidth(text, fontSize) <= maxWidth) {
        textEl.textContent = text;
        return;
      }
      let lo = 0,
        hi = text.length - 1;
      while (lo < hi) {
        const mid = Math.ceil((lo + hi) / 2);
        if (
          this.measureTextWidth(text.slice(0, mid) + "…", fontSize) <= maxWidth
        ) {
          lo = mid;
        } else {
          hi = mid - 1;
        }
      }
      textEl.textContent = lo > 0 ? text.slice(0, lo) + "…" : "…";
    }

    // ── Viewport / navigation ─────────────────────────────────────────────────────

    setupZoomPan(svgRoot, svg) {
      this.releaseDragHandlers();

      svgRoot.addEventListener("wheel", (e) => {
        e.preventDefault();
        const zoomIntensity = 0.1;
        const delta = e.deltaY > 0 ? -zoomIntensity : zoomIntensity;
        const oldScale = this.transform.k;
        const newScale = Math.min(
          Math.max(oldScale * (1 + delta), this.getMinZoom()),
          MAX_ZOOM,
        );
        const scaleRatio = newScale / oldScale;

        const rect = svgRoot.getBoundingClientRect();
        const centerX = rect.width / 2;
        const centerY = rect.height / 2;

        this.transform.x = centerX - (centerX - this.transform.x) * scaleRatio;
        this.transform.y = centerY - (centerY - this.transform.y) * scaleRatio;
        this.transform.k = newScale;
        this.updateTransform(svg);
      });

      svgRoot.addEventListener("mousedown", (e) => {
        this.isDragging = true;
        this.hasDragged = false;
        this.dragStartRaw = { x: e.clientX, y: e.clientY };
        svgRoot.style.cursor = "grabbing";
        this.startPoint = {
          x: e.clientX - this.transform.x,
          y: e.clientY - this.transform.y,
        };
      });

      this._mouseMoveHandler = (e) => {
        if (!this.isDragging) return;
        e.preventDefault();
        const dx = e.clientX - this.dragStartRaw.x;
        const dy = e.clientY - this.dragStartRaw.y;
        if (dx * dx + dy * dy > 25) this.hasDragged = true;
        this.transform.x = e.clientX - this.startPoint.x;
        this.transform.y = e.clientY - this.startPoint.y;
        this.updateTransform(svg);
      };
      window.addEventListener("mousemove", this._mouseMoveHandler);

      this._mouseUpHandler = () => {
        this.isDragging = false;
        svgRoot.style.cursor = "grab";
      };
      window.addEventListener("mouseup", this._mouseUpHandler);
    }

    releaseDragHandlers() {
      if (this._mouseMoveHandler) {
        window.removeEventListener("mousemove", this._mouseMoveHandler);
        this._mouseMoveHandler = null;
      }
      if (this._mouseUpHandler) {
        window.removeEventListener("mouseup", this._mouseUpHandler);
        this._mouseUpHandler = null;
      }
    }

    destroy() {
      this.releaseDragHandlers();
      super.destroy();
    }

    updateTransform(svg) {
      svg.setAttribute(
        "transform",
        `translate(${this.transform.x},${this.transform.y}) scale(${this.transform.k})`,
      );
      if (this.activeGlobalTopic) {
        this._drawGlobalTopicStar(this.activeGlobalTopic);
      }
    }

    fitToScreen() {
      const svg = this.container.querySelector("#zoom-layer");
      if (!svg) return;

      const bbox = svg.getBBox();
      if (bbox.width === 0 || bbox.height === 0) return;

      this.graphBBox = bbox;
      const containerRect = this.container.getBoundingClientRect();
      this.transform.k = this.getMinZoom();
      this.transform.x =
        (containerRect.width - bbox.width * this.transform.k) / 2 -
        bbox.x * this.transform.k;
      this.transform.y =
        (containerRect.height - bbox.height * this.transform.k) / 2 -
        bbox.y * this.transform.k;
      this.updateTransform(svg);
    }

    // Zoom-out floor: the scale that fits the whole graph in the viewport, so the
    // initial view is also the widest one. Derived from the live container size,
    // so it follows window resizes; the graph bbox is fixed by the layout.
    getMinZoom() {
      const bbox = this.graphBBox;
      if (!bbox?.width || !bbox?.height) return FIT_SCALE_CAP;
      const rect = this.container.getBoundingClientRect();
      const fit = Math.min(
        (rect.width - FIT_MARGIN) / bbox.width,
        (rect.height - FIT_MARGIN) / bbox.height,
      );
      return Math.min(fit, FIT_SCALE_CAP);
    }

    updateTheme() {
      if (this.currentGraph && this.currentSvgRoot) {
        this.renderNodeDiagram(this.currentGraph);
      }
    }

    // ── Highlighting ──────────────────────────────────────────────────────────────

    clearHighlights() {
      this.nodeConnectionDirections.clear();
      this.activeGlobalTopic = null;
      if (this.globalOverlay) this.globalOverlay.replaceChildren();

      const scope = this.currentSvgRoot || this.container;
      if (!scope) return;

      scope.querySelectorAll(".highlighted").forEach((el) => {
        el.classList.remove("highlighted");
        if (el.tagName === "path") {
          const d = parseInt(el.getAttribute("data-depth") || "0", 10);
          el.setAttribute("marker-end", `url(#arrowhead-depth-${d})`);
          el.style.stroke = "";
          el.style.strokeWidth = "";
        }
      });
      scope.querySelectorAll(".module-highlighted").forEach((el) => {
        el.classList.remove("module-highlighted");
        const rect = el.querySelector(":scope > .node-rect");
        if (rect) rect.style.strokeWidth = "";
      });
      scope.querySelectorAll(".child-highlighted").forEach((el) => {
        el.classList.remove("child-highlighted");
        el.style.strokeWidth = "";
      });
      scope.querySelectorAll(".port-highlighted").forEach((el) => {
        el.classList.remove("port-highlighted");
        el.style.fill = "";
        el.style.stroke = "";
      });
      scope.querySelectorAll(".node-connection-highlight").forEach((el) => {
        el.classList.remove("node-connection-highlight");
        el.style.stroke = "";
        el.style.strokeWidth = "";
      });
    }

    highlightModule(node, moduleGroup) {
      moduleGroup.classList.add("module-highlighted");

      const moduleRect = moduleGroup.querySelector(":scope > .node-rect");
      if (moduleRect) {
        const currentBorderW = parseFloat(
          moduleRect.getAttribute("stroke-width") || "1",
        );
        moduleRect.style.strokeWidth = (currentBorderW * 2).toFixed(1) + "px";
      }

      Array.from(moduleGroup.children)
        .filter(
          (child) =>
            child.tagName === "path" && child.classList.contains("edge-path"),
        )
        .forEach((path) => {
          path.classList.add("highlighted");
          const d = parseInt(path.getAttribute("data-depth") || "0", 10);
          path.style.strokeWidth =
            (parseFloat(this.getLayerStyle(d).edgeW) * 2).toFixed(1) + "px";
        });

      if (node.children?.length > 0) {
        node.children.forEach((childNode) => {
          const childGroup = Array.from(moduleGroup.children).find(
            (child) =>
              child.tagName === "g" &&
              child.classList.contains("node-group") &&
              child.id === childNode.id,
          );
          const childRect = childGroup?.querySelector(".node-rect");
          if (childRect) {
            childRect.classList.add("child-highlighted");
            const currentBorderW = parseFloat(
              childRect.getAttribute("stroke-width") || "1",
            );
            childRect.style.strokeWidth =
              (currentBorderW * 2).toFixed(1) + "px";
          }
        });
      }
    }

    highlightConnected(
      startIds,
      clearExisting = true,
      colorPreset = "default",
    ) {
      if (!this.colorPresets[colorPreset]) colorPreset = "default";
      if (!Array.isArray(startIds)) startIds = [startIds];
      if (clearExisting) this.clearHighlights();

      const queue = [...startIds];
      const visited = new Set();

      while (queue.length > 0) {
        const currentId = queue.shift();
        if (visited.has(currentId)) continue;
        visited.add(currentId);

        const data = this.elementData.get(currentId);
        if (!data) continue;

        if (this._isPort(data)) {
          this._applyPortHighlight(currentId, colorPreset);
          (this.portToEdges.get(currentId) || []).forEach((edgeId) => {
            if (!visited.has(edgeId)) queue.push(edgeId);
          });
          data.connected_ids?.forEach((connectedId) => {
            if (!visited.has(connectedId)) queue.push(connectedId);
          });
        } else if (this._isEdge(data)) {
          this._applyEdgeHighlight(currentId, colorPreset);
          const fromId =
            data.from_port?.unique_id ??
            (typeof data.from_port === "string" ? data.from_port : null);
          const toId =
            data.to_port?.unique_id ??
            (typeof data.to_port === "string" ? data.to_port : null);
          if (fromId) queue.push(String(fromId));
          if (toId) queue.push(String(toId));
        }
      }
    }

    // "upstream"  – in-ports:  external publisher → boundary in-port
    // "downstream"– out-ports: boundary out-port → external consumer
    // connected_ids is intentionally not followed here to avoid fan-out across unrelated topic subscribers.
    highlightBoundaryChain(startIds, direction, colorPreset = "default") {
      if (!this.colorPresets[colorPreset]) colorPreset = "default";
      if (!Array.isArray(startIds)) startIds = [startIds];

      const queue = [...startIds];
      const visited = new Set();

      while (queue.length > 0) {
        const currentId = queue.shift();
        if (visited.has(currentId)) continue;
        visited.add(currentId);

        const data = this.elementData.get(currentId);
        if (!data) continue;

        if (this._isPort(data)) {
          this._applyPortHighlight(currentId, colorPreset, direction);

          (this.portToEdges.get(currentId) || []).forEach((edgeId) => {
            if (visited.has(edgeId)) return;
            const edgeData = this.elementData.get(edgeId);
            if (!edgeData) return;
            const fromId = String(
              edgeData.from_port?.unique_id ?? edgeData.from_port ?? "",
            );
            const toId = String(
              edgeData.to_port?.unique_id ?? edgeData.to_port ?? "",
            );
            if (direction === "upstream" && toId === currentId)
              queue.push(edgeId);
            if (direction === "downstream" && fromId === currentId)
              queue.push(edgeId);
          });
        } else if (this._isEdge(data)) {
          this._applyEdgeHighlight(currentId, colorPreset);

          if (direction === "upstream") {
            const fromId = String(
              data.from_port?.unique_id ?? data.from_port ?? "",
            );
            if (fromId && !visited.has(fromId)) queue.push(fromId);
          } else {
            const toId = String(data.to_port?.unique_id ?? data.to_port ?? "");
            if (toId && !visited.has(toId)) queue.push(toId);
          }
        }
      }
    }

    // ── Global topics ─────────────────────────────────────────────────────────────

    _describeGlobalTopic(entry) {
      const describe = (id) => {
        const port = this.elementData.get(id) || {};
        return { name: port.name || "Port", path: port.port_path || "" };
      };
      return {
        topic: entry.topic,
        publishers: entry.publishers.map(describe),
        subscribers: entry.subscribers.map(describe),
      };
    }

    // Publisher and subscriber sides keep the diagram's direction colors:
    // an output feeding the topic is orange, an input reading it is green.
    _globalSideColor(direction) {
      if (direction === "downstream") return "orange";
      if (direction === "upstream") return "green";
      return "teal";
    }

    highlightGlobalTopic(entry) {
      entry.members.forEach((id) =>
        this._applyPortHighlight(
          id,
          this._globalSideColor(this._getPortDirection(id)),
        ),
      );
      this.activeGlobalTopic = entry;
      this._drawGlobalTopicStar(entry);
    }

    // Members of a global topic share no link, so the star is drawn on demand
    // between the member ports and one representative point. Redrawn on every
    // viewport change, which is what keeps its stroke and label screen-sized.
    _drawGlobalTopicStar(entry) {
      const overlay = this.globalOverlay;
      if (!overlay) return;
      overlay.replaceChildren();

      const positions = (ids) =>
        ids.map((id) => this.portAbsPos.get(id)).filter(Boolean);
      const publishers = positions(entry.publishers);
      const subscribers = positions(entry.subscribers);
      const members = publishers.concat(subscribers);
      if (members.length === 0) return;

      const metrics = this._globalStarMetrics();
      const hub = this._globalHubPoint(members, metrics);
      const gap = metrics.hubRadius + metrics.lineWidth;

      publishers.forEach((port) =>
        overlay.appendChild(
          this._globalTopicLine(
            this._pointAlong(port, hub, port.r * 1.5),
            this._pointAlong(hub, port, gap),
            metrics,
            "orange",
          ),
        ),
      );
      subscribers.forEach((port) =>
        overlay.appendChild(
          this._globalTopicLine(
            this._pointAlong(hub, port, gap),
            this._pointAlong(port, hub, port.r * 1.5),
            metrics,
            "green",
          ),
        ),
      );

      // One-sided topics reach a producer or consumer outside the system.
      const oneSided = publishers.length === 0 || subscribers.length === 0;
      this._appendGlobalHub(overlay, entry.topic, hub, metrics, oneSided);
    }

    _globalStarMetrics() {
      const scale = this.transform.k || 1;
      return {
        lineWidth: STAR_LINE_WIDTH / scale,
        hubRadius: STAR_HUB_RADIUS / scale,
        fontSize: STAR_FONT_SIZE / scale,
      };
    }

    _globalHubPoint(members, metrics) {
      if (members.length === 1) {
        const only = members[0];
        return {
          x:
            only.x +
            (only.side === "WEST" ? -1 : 1) *
              Math.max(metrics.hubRadius * 6, only.r * 20),
          y: only.y,
        };
      }
      const sum = members.reduce(
        (acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y }),
        { x: 0, y: 0 },
      );
      return { x: sum.x / members.length, y: sum.y / members.length };
    }

    // Point at `distance` from `from`, on the segment towards `to`.
    _pointAlong(from, to, distance) {
      const dx = to.x - from.x;
      const dy = to.y - from.y;
      const length = Math.hypot(dx, dy) || 1;
      const ratio = Math.min(1, distance / length);
      return { x: from.x + dx * ratio, y: from.y + dy * ratio };
    }

    _globalTopicLine(from, to, metrics, colorPreset) {
      const width = metrics.lineWidth;
      const line = document.createElementNS(SVG_NS, "line");
      line.setAttribute("x1", from.x);
      line.setAttribute("y1", from.y);
      line.setAttribute("x2", to.x);
      line.setAttribute("y2", to.y);
      line.classList.add("global-topic-line");
      line.style.stroke = this.colorPresets[colorPreset].edge;
      line.style.strokeWidth = width + "px";
      line.setAttribute("stroke-dasharray", `${width * 4} ${width * 2.5}`);
      line.setAttribute("marker-end", `url(#arrowhead-global-${colorPreset})`);
      return line;
    }

    _appendGlobalHub(overlay, topic, hub, metrics, oneSided) {
      const color = this.colorPresets.teal.edge;
      const background = this.isDarkMode()
        ? this.styleDefaults.dark.rootBg
        : this.styleDefaults.light.rootBg;
      const strokeW = metrics.lineWidth;
      const radius = metrics.hubRadius;

      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", hub.x);
      circle.setAttribute("cy", hub.y);
      circle.setAttribute("r", radius);
      circle.classList.add("global-topic-hub");
      circle.style.fill = background;
      circle.style.stroke = color;
      circle.style.strokeWidth = strokeW + "px";
      if (oneSided) {
        circle.setAttribute(
          "stroke-dasharray",
          `${strokeW * 2} ${strokeW * 1.5}`,
        );
      }
      overlay.appendChild(circle);

      const fontSize = metrics.fontSize;
      const pad = fontSize * 0.4;
      const boxWidth = this.measureTextWidth(topic, fontSize) + pad * 2;
      const boxHeight = fontSize + pad * 2;
      const boxY = hub.y - radius - pad - boxHeight;

      const box = document.createElementNS(SVG_NS, "rect");
      box.setAttribute("x", hub.x - boxWidth / 2);
      box.setAttribute("y", boxY);
      box.setAttribute("width", boxWidth);
      box.setAttribute("height", boxHeight);
      box.setAttribute("rx", pad);
      box.classList.add("global-topic-label-box");
      box.style.fill = background;
      box.style.stroke = color;
      box.style.strokeWidth = strokeW + "px";
      overlay.appendChild(box);

      const label = document.createElementNS(SVG_NS, "text");
      label.setAttribute("x", hub.x);
      label.setAttribute("y", boxY + boxHeight / 2);
      label.textContent = topic;
      label.classList.add("global-topic-label");
      label.style.fontSize = fontSize + "px";
      label.style.fill = color;
      overlay.appendChild(label);
    }

    _isPort(data) {
      return !!(data.msg_type && !data.from_port);
    }

    _isEdge(data) {
      return !!(data.from_port && data.to_port);
    }

    _getPortDirection(portId) {
      const nodeId = this.portToNode.get(String(portId));
      if (!nodeId) return null;

      const nodeData = this.elementData.get(nodeId);
      if (!nodeData) return null;

      const pid = String(portId);
      if ((nodeData.in_ports || []).some((p) => String(p.unique_id) === pid))
        return "upstream";
      if ((nodeData.out_ports || []).some((p) => String(p.unique_id) === pid))
        return "downstream";
      return null;
    }

    _applyNodeConnectionHighlight(portId, directionHint = null) {
      const nodeId = this.portToNode.get(String(portId));
      if (!nodeId) return;

      const nodeData = this.elementData.get(nodeId);
      if (!nodeData || nodeData.entity_type !== "node") return;

      const direction = directionHint || this._getPortDirection(portId);
      if (direction !== "upstream" && direction !== "downstream") return;

      const nodeGroup = document.getElementById(nodeId);
      if (!nodeGroup) return;
      const rect = nodeGroup.querySelector(".node-rect");
      if (!rect) return;

      const state = this.nodeConnectionDirections.get(nodeId) || {
        upstream: false,
        downstream: false,
      };
      state[direction] = true;
      this.nodeConnectionDirections.set(nodeId, state);

      const strokeColor =
        state.upstream && state.downstream
          ? this.colorPresets.purple.edge
          : state.upstream
            ? this.colorPresets.green.edge
            : this.colorPresets.orange.edge;

      rect.classList.add("node-connection-highlight");
      rect.style.stroke = strokeColor;
      const currentBorderW = parseFloat(
        rect.getAttribute("stroke-width") || "1",
      );
      rect.style.strokeWidth = (currentBorderW * 2).toFixed(1) + "px";
    }

    _applyPortHighlight(id, colorPreset, directionHint = null) {
      const portGroup = document.getElementById(id);
      if (!portGroup) return;
      const rect = portGroup.querySelector(".port-rect");
      if (!rect) return;
      rect.classList.add("port-highlighted");
      rect.style.fill = this.colorPresets[colorPreset].port;
      rect.style.stroke = this.colorPresets[colorPreset].port;
      this._applyNodeConnectionHighlight(id, directionHint);
    }

    _applyEdgeHighlight(id, colorPreset) {
      const edgePath = document.getElementById(id);
      if (!edgePath) return;
      edgePath.classList.add("highlighted");
      const depth = parseInt(edgePath.getAttribute("data-depth") || "0", 10);
      edgePath.setAttribute(
        "marker-end",
        `url(#arrowhead-highlighted-${colorPreset}-depth-${depth})`,
      );
      edgePath.style.stroke = this.colorPresets[colorPreset].edge;
      edgePath.style.strokeWidth =
        (parseFloat(this.getLayerStyle(depth).edgeW) * 2).toFixed(1) + "px";
      if (edgePath.parentNode) edgePath.parentNode.appendChild(edgePath);
    }
  }

  window.NodeDiagramModule = NodeDiagramModule;
})();
