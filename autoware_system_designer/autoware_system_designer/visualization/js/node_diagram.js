const SVG_NS = "http://www.w3.org/2000/svg";

class NodeDiagramModule extends DiagramBase {
  constructor(container, options = {}) {
    super(container, options);

    this.currentGraph = null;
    this.transform = { x: 0, y: 0, k: 1 };
    this.isDragging = false;
    this.startPoint = { x: 0, y: 0 };
    this.elementData = new Map();
    this.portToEdges = new Map();
    this.colorPresets = null;

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
      const elkGraph = this.transformDataToElk(window.systemDesignData[this.options.mode]);
      await this.layoutAndRenderNodeDiagram(elkGraph);
    } catch (error) {
      console.error("Error loading node diagram:", error);
      this.showError(`Error loading node diagram: ${error.message}`);
    }
  }

  transformDataToElk(root) {
    this.elementData.clear();
    this.portToEdges.clear();

    const addPorts = (node, ports, side) => {
      (ports || []).forEach((port) => {
        if (!port.unique_id) return;
        const portId = String(port.unique_id);
        this.elementData.set(portId, port);
        node.ports.push({
          id: portId,
          width: 10,
          height: 10,
          properties: { "org.eclipse.elk.port.side": side },
          labels: [{
            text: port.name || "Port",
            width: (port.name?.length || 4) * 6,
            height: 10,
          }],
        });
      });
    };

    const convertNode = (instance) => {
      if (!instance?.unique_id) return null;

      const nodeId = String(instance.unique_id);
      this.elementData.set(nodeId, instance);

      const containerTarget = this.getContainerTarget(instance);
      const maxPorts = Math.max(
        (instance.in_ports || []).length,
        (instance.out_ports || []).length,
      );
      const nodeHeight = Math.max(100, 80 + maxPorts * 25 + (containerTarget ? 22 : 0));

      const node = {
        id: nodeId,
        labels: [
          { text: instance.namespace || "" },
          { text: instance.name || nodeId || "Unnamed" },
        ],
        width: 300,
        height: nodeHeight,
        children: [],
        ports: [],
        properties: {
          "org.eclipse.elk.portConstraints": "FIXED_SIDE",
          "org.eclipse.elk.nodeLabels.placement": "H_CENTER V_TOP",
          "org.eclipse.elk.portLabels.placement": "INSIDE",
          "org.eclipse.elk.portAlignment.default": "CENTER",
          "org.eclipse.elk.spacing.portPort": "15",
        },
      };

      addPorts(node, instance.in_ports, "WEST");
      addPorts(node, instance.out_ports, "EAST");

      if (instance.children?.length > 0) {
        node.children = instance.children.map(convertNode).filter(Boolean);
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

            return { id: edgeId, sources: [fromId], targets: [toId], properties: {} };
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
    return rootNode;
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

  async layoutAndRenderNodeDiagram(graphData) {
    if (!this.elk) throw new Error("ELK instance not initialized");

    const graph = await this.elk.layout(graphData, {
      layoutOptions: {
        algorithm: "layered",
        "org.eclipse.elk.direction": "RIGHT",
        "org.eclipse.elk.spacing.nodeNode": "60",
        "org.eclipse.elk.spacing.edgeNode": "30",
        "org.eclipse.elk.edgeRouting": "ORTHOGONAL",
        "org.eclipse.elk.layered.spacing.edgeNodeBetweenLayers": "30",
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

    const computedStyle = getComputedStyle(document.documentElement);

    this.colorPresets = {
      default: {
        name: "default",
        edge: computedStyle.getPropertyValue("--highlight").trim() || "#0d6efd",
        port: computedStyle.getPropertyValue("--highlight").trim() || "#0d6efd",
      },
      red:    { name: "red",    edge: "#dc3545", port: "#dc3545" },
      green:  { name: "green",  edge: "#28a745", port: "#28a745" },
      orange: { name: "orange", edge: "#fd7e14", port: "#fd7e14" },
      purple: { name: "purple", edge: "#6f42c1", port: "#6f42c1" },
      teal:   { name: "teal",   edge: "#20c997", port: "#20c997" },
    };

    this.styleDefaults = {
      dark: {
        bg:     computedStyle.getPropertyValue("--bg-secondary").trim() || "#2d2d2d",
        nodeBg: computedStyle.getPropertyValue("--bg-secondary").trim() || "#2d2d2d",
        stroke: computedStyle.getPropertyValue("--text-muted").trim()   || "#666",
        text:   computedStyle.getPropertyValue("--text-primary").trim() || "#e9ecef",
        rootBg: "#1e1e1e",
      },
      light: {
        bg:     computedStyle.getPropertyValue("--bg-secondary").trim() || "#ffffff",
        nodeBg: computedStyle.getPropertyValue("--bg-secondary").trim() || "#ffffff",
        stroke: "#333",
        text:   computedStyle.getPropertyValue("--text-primary").trim() || "#333",
        rootBg: "#f5f5f5",
      },
    };

    const arrowColor = this.isDarkMode()
      ? computedStyle.getPropertyValue("--text-muted").trim()   || "#6c757d"
      : computedStyle.getPropertyValue("--border-hover").trim() || "#adb5bd";

    const defs = document.createElementNS(SVG_NS, "defs");
    const defaultMarker = `
      <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
        <polygon points="0 0, 10 3.5, 0 7" fill="${arrowColor}" />
      </marker>`;
    const presetMarkers = Object.keys(this.colorPresets)
      .map((preset) => `
        <marker id="arrowhead-highlighted-${preset}" markerWidth="10" markerHeight="7" refX="10" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="${this.colorPresets[preset].edge}" />
        </marker>`)
      .join("");
    defs.innerHTML = defaultMarker + presetMarkers;
    svgRoot.insertBefore(defs, svg);

    this.renderNode(graph, svg);
    this.currentGraph = graph;
    this.currentSvgRoot = svgRoot;
  }

  setupZoomPan(svgRoot, svg) {
    if (this._mouseMoveHandler) window.removeEventListener("mousemove", this._mouseMoveHandler);
    if (this._mouseUpHandler) window.removeEventListener("mouseup", this._mouseUpHandler);

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

  clearHighlights() {
    document.querySelectorAll(".highlighted").forEach((el) => {
      el.classList.remove("highlighted");
      if (el.tagName === "path") {
        el.setAttribute("marker-end", "url(#arrowhead)");
        el.style.stroke = "";
        el.style.strokeWidth = "";
      }
    });
    document.querySelectorAll(".module-highlighted").forEach((el) => el.classList.remove("module-highlighted"));
    document.querySelectorAll(".child-highlighted").forEach((el) => el.classList.remove("child-highlighted"));
    document.querySelectorAll(".port-highlighted").forEach((el) => {
      el.classList.remove("port-highlighted");
      el.style.fill = "";
      el.style.stroke = "";
    });
  }

  _applyPortHighlight(id, colorPreset) {
    const portGroup = document.getElementById(id);
    if (!portGroup) return;
    const rect = portGroup.querySelector(".port-rect");
    if (!rect) return;
    rect.classList.add("port-highlighted");
    rect.style.fill = this.colorPresets[colorPreset].port;
    rect.style.stroke = this.colorPresets[colorPreset].port;
  }

  _applyEdgeHighlight(id, colorPreset) {
    const edgePath = document.getElementById(id);
    if (!edgePath) return;
    edgePath.classList.add("highlighted");
    edgePath.setAttribute("marker-end", `url(#arrowhead-highlighted-${colorPreset})`);
    edgePath.style.stroke = this.colorPresets[colorPreset].edge;
    edgePath.style.strokeWidth = "3px";
    if (edgePath.parentNode) edgePath.parentNode.appendChild(edgePath);
  }

  renderNode(node, parentGroup, depth = 0) {
    const g = document.createElementNS(SVG_NS, "g");
    g.setAttribute("transform", `translate(${node.x},${node.y})`);
    g.setAttribute("id", node.id);
    g.classList.add("node-group");

    const rect = document.createElementNS(SVG_NS, "rect");
    rect.setAttribute("width", node.width);
    rect.setAttribute("height", node.height);
    rect.setAttribute("rx", 4);
    rect.classList.add("node-rect");

    const userData = this.elementData.get(node.id) || {};
    const visGuide = userData.vis_guide || {};
    const defaults = this.styleDefaults;

    let fillColor, strokeColor;
    if (this.isDarkMode()) {
      fillColor = visGuide.dark_background_color || visGuide.background_color || defaults.dark.bg;
      if (userData.entity_type === "node") {
        fillColor = visGuide.dark_medium_color || visGuide.medium_color || defaults.dark.nodeBg;
      }
      strokeColor = visGuide.dark_color || visGuide.color || defaults.dark.stroke;
      if (depth === 0) fillColor = defaults.dark.rootBg;
    } else {
      fillColor = visGuide.background_color || defaults.light.bg;
      if (userData.entity_type === "node") {
        fillColor = visGuide.medium_color || defaults.light.nodeBg;
      }
      strokeColor = visGuide.color || defaults.light.stroke;
      if (depth === 0) fillColor = defaults.light.rootBg;
    }

    rect.setAttribute("fill", fillColor);
    rect.setAttribute("stroke", strokeColor);

    rect.onclick = (e) => {
      if (this.hasDragged) return;
      e.stopPropagation();
      this.updateInfoPanel(userData, "Node");
      this.clearHighlights();

      if (node.children?.length > 0) {
        this.highlightModule(node, g);
      } else {
        g.classList.add("highlighted");
      }

      const outwardInPortIds = (userData.in_ports || [])
        .filter((p) => p.unique_id && p.is_outward !== false)
        .map((p) => String(p.unique_id));
      const outwardOutPortIds = (userData.out_ports || [])
        .filter((p) => p.unique_id && p.is_outward !== false)
        .map((p) => String(p.unique_id));

      if (outwardInPortIds.length > 0)  this.highlightBoundaryChain(outwardInPortIds,  "upstream",   "green");
      if (outwardOutPortIds.length > 0) this.highlightBoundaryChain(outwardOutPortIds, "downstream", "orange");
    };

    g.appendChild(rect);

    const containerTarget = this.getContainerTarget(userData);
    if (containerTarget) {
      const badgePadding = 6;
      const badgeHeight = 14;
      const badgeText = String(containerTarget);
      const badgeWidth = Math.min(
        node.width - 2 * badgePadding,
        Math.max(48, badgeText.length * 6 + 12),
      );
      const badgeX = (node.width - badgeWidth) / 2;
      const badgeY = node.height - badgeHeight - badgePadding;

      const badgeRect = document.createElementNS(SVG_NS, "rect");
      badgeRect.setAttribute("x", badgeX);
      badgeRect.setAttribute("y", badgeY);
      badgeRect.setAttribute("width", badgeWidth);
      badgeRect.setAttribute("height", badgeHeight);
      badgeRect.setAttribute("rx", 3);
      badgeRect.style.fill = this.isDarkMode() ? "rgba(0,0,0,0.25)" : "#e9ecef";
      badgeRect.style.stroke = this.isDarkMode() ? "#6c757d" : "#adb5bd";
      badgeRect.style.strokeWidth = "1";
      g.appendChild(badgeRect);

      const badgeLabel = document.createElementNS(SVG_NS, "text");
      badgeLabel.setAttribute("x", node.width / 2);
      badgeLabel.setAttribute("y", badgeY + badgeHeight / 2 + 0.5);
      badgeLabel.textContent = badgeText;
      badgeLabel.classList.add("node-label");
      badgeLabel.style.fontSize = "9px";
      badgeLabel.style.fill = this.isDarkMode() ? "#dee2e6" : "#495057";
      g.appendChild(badgeLabel);
    }

    if (node.labels?.length > 0) {
      const fontSize = Math.max(12, 36 - depth * 5);
      let yOffset = 10;

      if (node.labels.length > 1 && node.labels[0].text) {
        const nsText = document.createElementNS(SVG_NS, "text");
        nsText.setAttribute("x", node.width / 2);
        nsText.setAttribute("y", yOffset);
        nsText.textContent = node.labels[0].text + "/";
        nsText.classList.add("node-label");
        nsText.style.fontSize = "5px";
        nsText.style.fill = this.isDarkMode()
          ? visGuide.dark_text_color || "#adb5bd"
          : visGuide.text_color || "#6c757d";
        g.appendChild(nsText);
        yOffset += 8;
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

    if (node.ports) {
      node.ports.forEach((port) => {
        const pg = document.createElementNS(SVG_NS, "g");
        pg.setAttribute("id", port.id);
        pg.setAttribute("transform", `translate(${port.x},${port.y})`);

        const prect = document.createElementNS(SVG_NS, "rect");
        prect.setAttribute("width", port.width);
        prect.setAttribute("height", port.height);
        prect.classList.add("port-rect");

        const portData = this.elementData.get(port.id) || {};
        const title = document.createElementNS(SVG_NS, "title");
        title.textContent = portData.name || "Port";
        prect.appendChild(title);

        pg.onclick = (e) => {
          if (this.hasDragged) return;
          e.stopPropagation();
          this.updateInfoPanel(portData, "Port");
          this.highlightConnected(port.id, "Port");
        };
        pg.style.cursor = "pointer";
        pg.appendChild(prect);

        if (port.labels) {
          port.labels.forEach((label) => {
            const text = document.createElementNS(SVG_NS, "text");
            text.setAttribute("x", (label.x || 0) + (label.width || 0) / 2);
            text.setAttribute("y", (label.y || 0) + (label.height || 0) / 2);
            text.textContent = label.text;
            text.classList.add("port-label");
            text.style.fill = this.isDarkMode()
              ? visGuide.dark_text_color || "#e9ecef"
              : visGuide.text_color || "#333";
            pg.appendChild(text);
          });
        }

        g.appendChild(pg);
      });
    }

    if (node.children) {
      node.children.forEach((child) => this.renderNode(child, g, depth + 1));
    }

    if (node.edges) {
      node.edges.forEach((edge) => {
        if (!edge.sections) return;
        const path = document.createElementNS(SVG_NS, "path");
        path.setAttribute("id", edge.id);
        let d = "";
        edge.sections.forEach((section) => {
          d += `M ${section.startPoint.x} ${section.startPoint.y} `;
          if (section.bendPoints) {
            section.bendPoints.forEach((bp) => (d += `L ${bp.x} ${bp.y} `));
          }
          d += `L ${section.endPoint.x} ${section.endPoint.y} `;
        });
        path.setAttribute("d", d);
        path.classList.add("edge-path");
        path.setAttribute("marker-end", "url(#arrowhead)");

        const edgeData = this.elementData.get(edge.id) || {};
        path.onclick = (e) => {
          if (this.hasDragged) return;
          e.stopPropagation();
          this.updateInfoPanel(edgeData, "Link");
          this.highlightConnected(edge.id, "Link");
        };

        g.appendChild(path);
      });
    }

    parentGroup.appendChild(g);
  }

  highlightModule(node, moduleGroup) {
    moduleGroup.classList.add("module-highlighted");

    Array.from(moduleGroup.children)
      .filter((child) => child.tagName === "path" && child.classList.contains("edge-path"))
      .forEach((path) => path.classList.add("highlighted"));

    if (node.children?.length > 0) {
      node.children.forEach((childNode) => {
        const childGroup = Array.from(moduleGroup.children).find(
          (child) =>
            child.tagName === "g" &&
            child.classList.contains("node-group") &&
            child.id === childNode.id,
        );
        childGroup?.querySelector("rect")?.classList.add("child-highlighted");
      });
    }
  }

  /**
   * Highlights connected elements starting from the given IDs.
   * @param {string|string[]} startIds
   * @param {string} type - 'Port', 'Link', etc.
   * @param {boolean} clearExisting
   * @param {string} colorPreset - 'default', 'red', 'green', 'orange', 'purple', 'teal'
   */
  highlightConnected(startIds, type, clearExisting = true, colorPreset = "default") {
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

      if (data.msg_type && !data.from_port) {
        this._applyPortHighlight(currentId, colorPreset);

        (this.portToEdges.get(currentId) || []).forEach((edgeId) => {
          if (!visited.has(edgeId)) queue.push(edgeId);
        });

        data.connected_ids?.forEach((connectedId) => {
          if (!visited.has(connectedId)) queue.push(connectedId);
        });
      } else if (data.from_port && data.to_port) {
        this._applyEdgeHighlight(currentId, colorPreset);

        const fromId = data.from_port?.unique_id ?? (typeof data.from_port === "string" ? data.from_port : null);
        const toId   = data.to_port?.unique_id   ?? (typeof data.to_port   === "string" ? data.to_port   : null);
        if (fromId) queue.push(String(fromId));
        if (toId)   queue.push(String(toId));
      }
    }
  }

  /**
   * Highlights outward boundary ports and their external link chains.
   * "upstream"  – in-ports:  external publisher → boundary in-port
   * "downstream"– out-ports: boundary out-port → external consumer
   * connected_ids is not followed to avoid fan-out across unrelated topic subscribers.
   */
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

      if (data.msg_type && !data.from_port) {
        this._applyPortHighlight(currentId, colorPreset);

        (this.portToEdges.get(currentId) || []).forEach((edgeId) => {
          if (visited.has(edgeId)) return;
          const edgeData = this.elementData.get(edgeId);
          if (!edgeData) return;
          const fromId = String(edgeData.from_port?.unique_id ?? edgeData.from_port ?? "");
          const toId   = String(edgeData.to_port?.unique_id   ?? edgeData.to_port   ?? "");
          if (direction === "upstream"   && toId   === currentId) queue.push(edgeId);
          if (direction === "downstream" && fromId === currentId) queue.push(edgeId);
        });
      } else if (data.from_port && data.to_port) {
        this._applyEdgeHighlight(currentId, colorPreset);

        if (direction === "upstream") {
          const fromId = String(data.from_port?.unique_id ?? data.from_port ?? "");
          if (fromId && !visited.has(fromId)) queue.push(fromId);
        } else {
          const toId = String(data.to_port?.unique_id ?? data.to_port ?? "");
          if (toId && !visited.has(toId)) queue.push(toId);
        }
      }
    }
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
    this.transform.x = (containerRect.width  - bbox.width  * this.transform.k) / 2 - bbox.x * this.transform.k;
    this.transform.y = (containerRect.height - bbox.height * this.transform.k) / 2 - bbox.y * this.transform.k;
    this.updateTransform(svg);
  }

  updateTheme() {
    if (this.currentGraph && this.currentSvgRoot) {
      this.renderNodeDiagram(this.currentGraph);
    }
  }
}

window.NodeDiagramModule = NodeDiagramModule;
