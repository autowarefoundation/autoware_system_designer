// ELK Canvas Module
// Shared canvas for the diagrams that lay out with ELK and draw their own SVG:
// layout metrics, viewport, text measurement and theme-derived colors.

(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const FIT_MARGIN = 40;
  const FIT_SCALE_CAP = 1;
  const MAX_ZOOM = 5;

  class ElkCanvas extends DiagramBase {
    static SVG_NS = SVG_NS;

    constructor(container, options = {}) {
      super(container, options);

      this.elk = null;
      this.maxDepth = 0;
      this.zoomLayer = null;
      this.currentSvgRoot = null;
      this.graphBBox = null;
      this.transform = { x: 0, y: 0, k: 1 };
      this.isDragging = false;
      this.hasDragged = false;
      this.dragStartRaw = null;
      this.startPoint = { x: 0, y: 0 };
      this.colorPresets = null;
      this.styleDefaults = null;
    }

    // ── Layout engine ───────────────────────────────────────────────────────────

    async initElk() {
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
      return this.elk;
    }

    // ── Styling / metrics ───────────────────────────────────────────────────────

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
        edgeNodeSpacing: Math.round(1 * s),
        edgeNodeBetweenLayers: Math.round(1 * s),
        edgeEdgeSpacing: Math.round(1 * s),
        edgeEdgeBetweenLayers: Math.round(1 * s),
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

    findMaxDepth(instance, depth = 0) {
      if (!instance?.children?.length) return depth;
      return Math.max(
        ...instance.children.map((c) => this.findMaxDepth(c, depth + 1)),
      );
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

    themed(visGuide, key, fallback) {
      const guide = visGuide || {};
      if (this.isDarkMode()) {
        return guide[`dark_${key}`] || guide[key] || fallback;
      }
      return guide[key] || fallback;
    }

    // ── Canvas ──────────────────────────────────────────────────────────────────

    // svgRoot > defs + g#zoom-layer; the zoom layer carries the whole drawing and
    // is the only element the viewport transform touches.
    createCanvas() {
      this.container.innerHTML = "";

      const svgRoot = document.createElementNS(SVG_NS, "svg");
      svgRoot.setAttribute("width", "100%");
      svgRoot.setAttribute("height", "100%");
      svgRoot.style.width = "100%";
      svgRoot.style.height = "100%";
      svgRoot.style.cursor = "grab";

      const layer = document.createElementNS(SVG_NS, "g");
      layer.id = "zoom-layer";
      svgRoot.appendChild(layer);
      this.container.appendChild(svgRoot);

      this.setupZoomPan(svgRoot, layer);
      this.updateTransform(layer);
      this._computeThemeStyles();

      const cs = getComputedStyle(document.documentElement);
      const arrowColor = this.isDarkMode()
        ? cs.getPropertyValue("--text-muted").trim() || "#6c757d"
        : cs.getPropertyValue("--border-hover").trim() || "#adb5bd";
      svgRoot.insertBefore(this._buildArrowDefs(arrowColor), layer);

      this.currentSvgRoot = svgRoot;
      return { svgRoot, layer };
    }

    // Ids of drawn elements come from the exported data, so they are resolved
    // inside this canvas.
    elementById(id) {
      return id && this.currentSvgRoot
        ? this.currentSvgRoot.querySelector(`#${CSS.escape(id)}`)
        : null;
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

      // Scaled by the line width, which the topic star keeps constant on screen.
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

    // ── Text utilities ──────────────────────────────────────────────────────────

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

    // ── Viewport / navigation ───────────────────────────────────────────────────

    setupZoomPan(svgRoot, svg) {
      this.releaseDragHandlers();
      this.zoomLayer = svg;

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

        // Anchored on the pointer: the drawing under it stays under it.
        const rect = svgRoot.getBoundingClientRect();
        const anchorX = e.clientX - rect.left;
        const anchorY = e.clientY - rect.top;

        this.transform.x = anchorX - (anchorX - this.transform.x) * scaleRatio;
        this.transform.y = anchorY - (anchorY - this.transform.y) * scaleRatio;
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
      const layer = svg || this.zoomLayer;
      if (!layer) return;
      layer.setAttribute(
        "transform",
        `translate(${this.transform.x},${this.transform.y}) scale(${this.transform.k})`,
      );
      this.onTransform();
    }

    // Hook for overlays drawn in screen-constant units.
    onTransform() {}

    fitToScreen() {
      const svg = this.zoomLayer || this.container.querySelector("#zoom-layer");
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
  }

  window.ElkCanvas = ElkCanvas;
})();
