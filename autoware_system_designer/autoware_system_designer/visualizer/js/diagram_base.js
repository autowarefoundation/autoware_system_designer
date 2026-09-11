// Diagram Base Module
// Shared lifecycle for the diagram modules the overview page loads on demand.

(function () {
  class DiagramBase {
    // Third-party renderers, pinned here so the page tags and the module
    // fallbacks that run when a tag is unavailable request the same builds.
    static CDN = {
      elk: "https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js",
      mermaid:
        "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js",
      svgPanZoom:
        "https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js",
    };

    // svg-pan-zoom settings every diagram shares; each module overrides the rest.
    static PAN_ZOOM_DEFAULTS = {
      zoomEnabled: true,
      controlIconsEnabled: false,
      fit: false,
      center: false,
    };

    // Loads still in flight, keyed by src: a second request for the same script
    // awaits the first load instead of resolving before the script has run.
    static pendingScripts = new Map();

    static loadScript(src) {
      const pending = DiagramBase.pendingScripts.get(src);
      if (pending) return pending;
      if (document.querySelector(`script[src="${src}"]`)) {
        return Promise.resolve();
      }

      const load = new Promise((resolve, reject) => {
        const script = document.createElement("script");
        script.src = src;
        script.onload = () => resolve();
        script.onerror = (event) => {
          const error = new Error(`Failed to load script: ${src}`);
          error.event = event;
          reject(error);
        };
        document.head.appendChild(script);
      }).catch((error) => {
        DiagramBase.pendingScripts.delete(src);
        throw error;
      });

      DiagramBase.pendingScripts.set(src, load);
      return load;
    }

    // Loads src only when globalName is still absent, so a page-level <script>
    // tag (which carries integrity metadata) always wins over the fallback.
    static async ensureLibrary(globalName, src) {
      if (typeof window[globalName] === "undefined") {
        await DiagramBase.loadScript(src);
      }
      return window[globalName];
    }

    static escapeHtml(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    constructor(container, options = {}) {
      this.container = container;
      this.options = {
        mode: options.mode || "default",
        deployment: options.deployment || "",
        ...options,
      };
      this.panZoomInstance = null;
    }

    isDarkMode() {
      return document.documentElement.getAttribute("data-theme") === "dark";
    }

    loadScript(src) {
      return DiagramBase.loadScript(src);
    }

    loadDataScript(mode, type) {
      return DiagramBase.loadScript(`data/${mode}_${type}.js`);
    }

    showError(message) {
      this.container.innerHTML = "";
      const box = document.createElement("div");
      box.className = "diagram-error";
      box.textContent = message;
      this.container.appendChild(box);
    }

    updateInfoPanel(data, type) {
      if (this.options.onInfoUpdate) {
        this.options.onInfoUpdate(data, type);
      }
    }

    getComputedStyleValue(prop, fallback) {
      return (
        getComputedStyle(document.documentElement)
          .getPropertyValue(prop)
          .trim() || fallback
      );
    }

    // ── Pan / zoom (svg-pan-zoom backed modules) ────────────────────────────────

    createPanZoom(svgElement, options = {}) {
      this.destroyPanZoom();

      const rect = svgElement.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) {
        console.warn(
          "SVG has zero dimensions, skipping pan-zoom initialization",
        );
        return null;
      }

      try {
        this.panZoomInstance = svgPanZoom(svgElement, {
          ...DiagramBase.PAN_ZOOM_DEFAULTS,
          ...options,
        });
      } catch (error) {
        console.warn("Failed to initialize svg-pan-zoom:", error);
        this.panZoomInstance = null;
      }
      return this.panZoomInstance;
    }

    // Re-fits once the browser has settled the SVG's box; a zero-sized measurement
    // means the layout is not final yet, so the attempt is skipped.
    fitPanZoom({ scale = 1, maxZoom = 1 } = {}) {
      if (!this.panZoomInstance) return false;
      try {
        this.panZoomInstance.resize();
        const sizes = this.panZoomInstance.getSizes();
        if (!sizes.width || !sizes.height) return false;

        this.panZoomInstance.fit();
        this.panZoomInstance.center();
        if (scale !== 1 || maxZoom !== Infinity) {
          this.panZoomInstance.zoom(
            Math.min(this.panZoomInstance.getZoom() * scale, maxZoom),
          );
          this.panZoomInstance.center();
        }
        return true;
      } catch (error) {
        console.warn("Failed to fit diagram:", error);
        return false;
      }
    }

    destroyPanZoom() {
      if (this.panZoomInstance) {
        this.panZoomInstance.destroy();
        this.panZoomInstance = null;
      }
    }

    destroy() {
      this.destroyPanZoom();
      this.container.innerHTML = "";
    }
  }

  // Export for use in the overview page and other modules
  window.DiagramBase = DiagramBase;
})();
