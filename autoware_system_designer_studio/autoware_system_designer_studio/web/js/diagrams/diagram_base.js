// Diagram Base Module
// Shared lifecycle for the diagram modules the overview page loads on demand.

(function () {
  class DiagramBase {
    // Third-party renderers, pinned here so the page tags and the module
    // fallbacks that run when a tag is unavailable request the same builds.
    static CDN = {
      elk: "https://cdn.jsdelivr.net/npm/elkjs@0.9.3/lib/elk.bundled.js",
    };

    // Page global the data scripts fill, keyed by mode; kept in sync with
    // pages/assets.py DATA_GLOBAL.
    static DATA_GLOBAL = "systemStructureData";

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

    constructor(container, options = {}) {
      this.container = container;
      this.options = {
        mode: options.mode || "default",
        deployment: options.deployment || "",
        ...options,
        dataBase: DiagramBase.withSlash(options.dataBase || "data/"),
      };
      // The container follows the panel layout; each size change re-measures the viewport.
      this.resizeObserver =
        typeof ResizeObserver === "undefined"
          ? null
          : new ResizeObserver(() => this.onContainerResize());
      this.resizeObserver?.observe(container);
    }

    onContainerResize() {}

    isDarkMode() {
      return document.documentElement.getAttribute("data-theme") === "dark";
    }

    // The mode's system structure from <dataBase><mode>.js, as a copy the
    // caller owns: every diagram of the page reads the same loaded object.
    async loadModeData(mode) {
      if (!window[DiagramBase.DATA_GLOBAL]?.[mode]) {
        await DiagramBase.loadScript(`${this.options.dataBase}${mode}.js`);
      }
      const data = window[DiagramBase.DATA_GLOBAL]?.[mode];
      if (!data) throw new Error(`No data available for mode: ${mode}`);
      return structuredClone(data);
    }

    static withSlash(base) {
      return base.endsWith("/") ? base : `${base}/`;
    }

    showError(message) {
      this.container.innerHTML = "";
      const box = document.createElement("div");
      box.className = "diagram-error";
      box.textContent = message;
      this.container.appendChild(box);
    }

    // Reports the selected element; `entity` is the structure instance (node or
    // module) the selection belongs to, when it has one.
    updateInfoPanel(data, type, entity = null) {
      if (this.options.onInfoUpdate) {
        this.options.onInfoUpdate(data, type, entity);
      }
    }

    destroy() {
      this.resizeObserver?.disconnect();
      this.container.innerHTML = "";
    }
  }

  window.DiagramBase = DiagramBase;

  // Diagram type -> module class; each diagram script registers itself on load.
  const registry = new Map();
  SD.diagrams = {
    register(type, ModuleClass) {
      registry.set(type, ModuleClass);
    },
    get(type) {
      return registry.get(type) || null;
    },
  };
})();
