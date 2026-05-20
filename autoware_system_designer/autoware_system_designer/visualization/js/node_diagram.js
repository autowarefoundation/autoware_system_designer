const SVG_NS = "http://www.w3.org/2000/svg";

class NodeDiagramModule extends DiagramBase {
  // ── Initialization ──────────────────────────────────────────────────────────

  constructor(container, options = {}) {
    super(container, options);

    this.currentGraph = null;
    this.currentSvgRoot = null;
    this.transform = { x: 0, y: 0, k: 1 };
    this.isDragging = false;
    this.hasDragged = false;
    this.dragStartRaw = null;
    this.startPoint = { x: 0, y: 0 };
    this.elementData = new Map();
    this.portToEdges = new Map();
    this.portToNode = new Map();
    this.nodeConnectionDirections = new Map();
    this.colorPresets = null;
    this.styleDefaults = null;

    this.init();
  }

  async init() {
    if (typeof ELK === "undefined") {
      await this.loadScript("https://unpkg.com/elkjs@0.8.2/lib/elk.bundled.js");
      await new Promise((resolve) => setTimeout(resolve, 100));
      if (typeof ELK === "undefined") {
        throw new Error("ELK library failed to load");
      }
    }

    let elkConstructor;
    if (typeof ELK === "function") {
      elkConstructor = ELK;
    } else if (ELK?.default) {
      elkConstructor = ELK.default;
    } else {
      elkConstructor = ELK.ELK || ELK.Elk || ELK;
    }

    if (!elkConstructor) {
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
          maxPorts * (style.portSpacing * 3) +
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

            if (!this.portToEdges.has(fromId)) this.portToEdges.set(fromId, []);
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
    return rootNode;
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
      style.nodeBaseH + remappedPortEntries.length * (style.portSpacing * 3),
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
      portSpacing: Math.round(4 * s),
      nodeSpacing: Math.round(5 * s),
      edgeNodeSpacing: Math.round(3 * s),
      edgeNodeBetweenLayers: Math.round(3 * s),
      edgeEdgeSpacing: Math.round(4 * s),
      edgeEdgeBetweenLayers: Math.round(4 * s),
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
    if (this._textMeasureCache.has(key)) return this._textMeasureCache.get(key);
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
        "org.eclipse.elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
        "org.eclipse.elk.layered.layering.strategy": "INTERACTIVE",
        "org.eclipse.elk.padding": "[top=50,left=50,bottom=50,right=50]",
      },
    });

    this.renderNodeDiagram(graph);
    this.fitToScreen();
  }

  renderNodeDiagram(graph) {
    this.container.innerHTML = "";

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

    this.renderNode(graph, svg);
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

    defs.innerHTML = markup;
    return defs;
  }

  renderNode(node, parentGroup, depth = 0) {
    const style = this.getLayerStyle(depth);
    const userData = this.elementData.get(node.id) || {};

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
      node.ports.forEach((port) =>
        g.appendChild(this._buildPortGroup(port, userData, style)),
      );
    }

    if (node.children) {
      node.children.forEach((child) => this.renderNode(child, g, depth + 1));
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
        this.highlightBoundaryChain(outwardOutPortIds, "downstream", "orange");
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
    badgeRect.setAttribute("rx", Math.max(1, Math.round(style.cornerR * 0.6)));
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
      this.updateInfoPanel(portData, "Port");
      this.highlightConnected(port.id);
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
    if (this._mouseMoveHandler)
      window.removeEventListener("mousemove", this._mouseMoveHandler);
    if (this._mouseUpHandler)
      window.removeEventListener("mouseup", this._mouseUpHandler);

    svgRoot.addEventListener("wheel", (e) => {
      e.preventDefault();
      const zoomIntensity = 0.1;
      const delta = e.deltaY > 0 ? -zoomIntensity : zoomIntensity;
      const oldScale = this.transform.k;
      const newScale = Math.min(Math.max(oldScale * (1 + delta), 0.03), 5);
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

  updateTransform(svg) {
    svg.setAttribute(
      "transform",
      `translate(${this.transform.x},${this.transform.y}) scale(${this.transform.k})`,
    );
  }

  fitToScreen() {
    const svg = this.container.querySelector("#zoom-layer");
    if (!svg) return;

    const bbox = svg.getBBox();
    if (bbox.width === 0 || bbox.height === 0) return;

    const containerRect = this.container.getBoundingClientRect();
    const scale = Math.min(
      (containerRect.width - 40) / bbox.width,
      (containerRect.height - 40) / bbox.height,
    );

    this.transform.k = Math.min(scale, 1);
    this.transform.x =
      (containerRect.width - bbox.width * this.transform.k) / 2 -
      bbox.x * this.transform.k;
    this.transform.y =
      (containerRect.height - bbox.height * this.transform.k) / 2 -
      bbox.y * this.transform.k;
    this.updateTransform(svg);
  }

  updateTheme() {
    if (this.currentGraph && this.currentSvgRoot) {
      this.renderNodeDiagram(this.currentGraph);
    }
  }

  // ── Highlighting ──────────────────────────────────────────────────────────────

  clearHighlights() {
    this.nodeConnectionDirections.clear();

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
          childRect.style.strokeWidth = (currentBorderW * 2).toFixed(1) + "px";
        }
      });
    }
  }

  highlightConnected(startIds, clearExisting = true, colorPreset = "default") {
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
    const currentBorderW = parseFloat(rect.getAttribute("stroke-width") || "1");
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
