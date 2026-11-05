// Explore activity: the diagram list and entity search beside the diagram
// canvas, with the inspector showing the selected element. Search is offered
// for diagrams that locate entities.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;

  // Highlight state a module may leave on nodes outside its own container.
  const HIGHLIGHT_CLASSES = ["highlighted", "port-highlighted"];

  function clearStaleHighlights() {
    HIGHLIGHT_CLASSES.forEach((className) => {
      document.querySelectorAll(`.${className}`).forEach((node) => node.classList.remove(className));
    });
  }

  // Canvas hosting one diagram module at a time for the selected diagram and mode.
  function diagramHost(main, config, store) {
    const container = element("div", "diagram-container");
    const loading = element("div", "diagram-loading");
    loading.append(element("div", "spinner"), element("div", "diagram-loading-text", "Loading..."));
    main.classList.add("canvas-view");
    main.append(container, loading);

    const infoPanel = document.getElementById("info-panel");
    const inspectorEmpty = document.getElementById("inspector-empty");
    let module = null;

    function showLoading(text) {
      loading.hidden = !text;
      loading.querySelector(".diagram-loading-text").textContent = text || "";
    }

    function resetInspector() {
      infoPanel.style.display = "none";
      inspectorEmpty.hidden = false;
    }

    async function load() {
      const diagram = config.diagrams.find((d) => d.type === store.get("diagram"));
      const mode = store.get("mode");
      module?.destroy();
      module = null;
      // A module renders asynchronously, so each one gets its own canvas: the
      // switch detaches the previous canvas and a late render lands off-page.
      const canvas = element("div", "diagram-canvas");
      container.replaceChildren(canvas);
      clearStaleHighlights();
      resetInspector();
      showLoading(`Loading ${diagram.label.toLowerCase()}...`);
      try {
        await DiagramBase.loadScript(diagram.script);
        const ModuleClass = SD.diagrams.get(diagram.type);
        if (!ModuleClass) throw new Error(`Module class not found for diagram type: ${diagram.type}`);
        module = new ModuleClass(canvas, {
          mode,
          deployment: config.deploymentName,
          dataBase: config.dataBase,
          latencyModes: config.latencyModes,
          onInfoUpdate: (data, type) => {
            infoPanel.style.display = "block";
            inspectorEmpty.hidden = true;
            SD.InfoPanel.render(infoPanel, data, type);
          },
        });
      } catch (error) {
        console.error("Error loading diagram:", error);
        container.replaceChildren(
          element("div", "diagram-error", `Error loading diagram: ${error.message || "Unknown error occurred"}`),
        );
      } finally {
        showLoading("");
      }
    }

    async function focus(id) {
      const current = module;
      if (typeof current?.focusEntity !== "function") return;
      await current.ready;
      if (current === module) current.focusEntity(id);
    }

    function updateTheme() {
      if (typeof module?.updateTheme === "function") module.updateTheme();
    }

    return { load, focus, updateTheme };
  }

  // Entity search over the mode's namespace tree; a hit is located on the diagram.
  function entitySearch(config, store, onLocate) {
    const section = element("div", "panel-group");
    section.appendChild(element("div", "panel-section-title", "Search"));
    const input = element("input", "panel-search");
    input.type = "search";
    input.placeholder = "Search nodes and namespaces";
    input.setAttribute("aria-label", "Search nodes and namespaces");
    const summary = element("div", "panel-hint");
    const tree = element("div", "ns-tree");
    const results = element("div", "search-results");
    results.hidden = true;
    results.append(summary, tree);
    section.append(input, results);

    let root = null;
    let selectedPath = "";

    function render() {
      const query = input.value.trim();
      results.hidden = !query;
      if (!query) return;
      if (!root) {
        summary.textContent = "Loading…";
        tree.replaceChildren();
        return;
      }
      const count = SD.NamespaceTree.render(tree, root, {
        query,
        selectedPath,
        onSelect(node) {
          selectedPath = node.path;
          render();
          const entity = node.entities[0];
          if (entity) onLocate(String(entity.unique_id));
        },
      });
      summary.textContent = count ? `${count} match${count > 1 ? "es" : ""}` : "No matches";
    }

    async function loadMode() {
      root = null;
      render();
      const data = await SD.NamespaceTree.loadStructure(config.dataBase, store.get("mode"));
      root = SD.NamespaceTree.build(SD.NamespaceTree.entities(data));
      render();
    }

    input.addEventListener("input", render);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        input.value = "";
        render();
      }
    });
    const reload = () => loadMode().catch((error) => console.warn("explore search:", error));
    store.onChange("mode", reload);
    reload();
    return section;
  }

  SD.workbench.registerActivity({
    id: "explore",
    title: "Explore",
    order: 10,
    daemon: false,
    inspector: true,
    scope: [],
    icon: "M3 4h6v4H3zM11 12h6v4h-6zM6 8v6h5",
    mount({ side, main }, { config, store }) {
      const types = config.diagrams.map((d) => d.type);
      store.define("diagram", config.defaultDiagram, (v) => types.includes(v));
      store.syncUrl();

      const host = diagramHost(main, config, store);

      side.appendChild(element("div", "panel-section-title", "Diagrams"));
      const list = element("div", "panel-list");
      config.diagrams.forEach((diagram) => {
        const item = element("button", "panel-list-item", diagram.label);
        item.type = "button";
        item.dataset.type = diagram.type;
        item.addEventListener("click", () => store.set("diagram", diagram.type));
        list.appendChild(item);
      });
      side.appendChild(list);
      const search = entitySearch(config, store, (id) => host.focus(id));
      side.appendChild(search);

      function showDiagramOptions() {
        const current = store.get("diagram");
        list.querySelectorAll(".panel-list-item").forEach((item) => {
          item.classList.toggle("active", item.dataset.type === current);
        });
        search.hidden = !config.diagrams.find((d) => d.type === current)?.searchable;
      }

      // The canvas lays out only while visible; a change made while hidden reloads on the next show.
      let stale = false;
      const reload = () => {
        if (main.hidden) stale = true;
        else host.load();
      };
      store.onChange("diagram", () => {
        showDiagramOptions();
        reload();
      });
      store.onChange("mode", reload);
      SD.on("themechange", host.updateTheme);

      showDiagramOptions();
      host.load();
      return {
        show() {
          if (!stale) return;
          stale = false;
          host.load();
        },
      };
    },
  });
})();
