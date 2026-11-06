// Explore: canvas hosting one diagram module at a time for the selected
// diagram and mode. The module reports the selected element through
// `inspector.show`, and a reload clears it through `inspector.reset`. A
// selected node or module is also recorded in the store under `entity`, and
// `focusKey` locates the store's entity on the diagram.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const explore = (SD.activities.explore ||= {});

  // Highlight state a module may leave on nodes outside its own container.
  const HIGHLIGHT_CLASSES = ["highlighted", "port-highlighted"];

  function clearStaleHighlights() {
    HIGHLIGHT_CLASSES.forEach((className) => {
      document
        .querySelectorAll(`.${className}`)
        .forEach((node) => node.classList.remove(className));
    });
  }

  // Nodes and modules are the elements shared with the other activities.
  function isEntity(data) {
    return (
      (data?.entity_type === "node" || data?.entity_type === "module") &&
      typeof data.path === "string"
    );
  }

  function diagramHost(main, inspector, config, store) {
    const container = element("div", "diagram-container");
    const loading = element("div", "diagram-loading");
    loading.append(
      element("div", "spinner"),
      element("div", "diagram-loading-text", "Loading..."),
    );
    main.classList.add("canvas-view");
    main.append(container, loading);
    let module = null;

    function showLoading(text) {
      loading.hidden = !text;
      loading.querySelector(".diagram-loading-text").textContent = text || "";
    }

    async function load() {
      const diagram = config.diagrams.find(
        (d) => d.type === store.get("diagram"),
      );
      const mode = store.get("mode");
      module?.destroy();
      module = null;
      // A module renders asynchronously, so each one gets its own canvas: the
      // switch detaches the previous canvas and a late render lands off-page.
      const canvas = element("div", "diagram-canvas");
      container.replaceChildren(canvas);
      clearStaleHighlights();
      inspector.reset();
      showLoading(`Loading ${diagram.label.toLowerCase()}...`);
      try {
        await DiagramBase.loadScript(diagram.script);
        const ModuleClass = SD.diagrams.get(diagram.type);
        if (!ModuleClass)
          throw new Error(
            `Module class not found for diagram type: ${diagram.type}`,
          );
        module = new ModuleClass(canvas, {
          mode,
          deployment: config.deploymentName,
          dataBase: config.dataBase,
          latencyModes: config.latencyModes,
          onInfoUpdate(data, type, entity) {
            inspector.show(data, type);
            if (isEntity(entity))
              store.set("entity", SD.NamespaceTree.entityKey(entity));
          },
        });
      } catch (error) {
        console.error("Error loading diagram:", error);
        container.replaceChildren(
          element(
            "div",
            "diagram-error",
            `Error loading diagram: ${error.message || "Unknown error occurred"}`,
          ),
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

    // Locates the entity with `key` in the mode's structure; false when it has none.
    async function focusKey(key) {
      const data = await SD.NamespaceTree.loadStructure(
        config.dataBase,
        store.get("mode"),
      );
      const entity = SD.NamespaceTree.entities(data).find(
        (e) => SD.NamespaceTree.entityKey(e) === key,
      );
      if (!entity) return false;
      await focus(String(entity.unique_id));
      return true;
    }

    function updateTheme() {
      if (typeof module?.updateTheme === "function") module.updateTheme();
    }

    return { load, focus, focusKey, updateTheme };
  }

  explore.diagramHost = diagramHost;
})();
