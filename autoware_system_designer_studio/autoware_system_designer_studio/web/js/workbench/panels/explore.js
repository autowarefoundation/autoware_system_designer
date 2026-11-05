// Explore panel: the diagram shown on the canvas and a search over the mode's
// entities; a search hit selects its entity on the diagram.
(function () {
  const SD = window.SD;

  function diagramLabel(type) {
    return type.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  SD.workbench.registerPanel({
    id: "explore",
    title: "Explore",
    order: 10,
    daemon: false,
    icon: "M3 4h6v4H3zM11 12h6v4h-6zM6 8v6h5",
    mount(body, { config, store }) {
      body.appendChild(element("div", "panel-section-title", "Diagrams"));
      const list = element("div", "panel-list");
      config.availableDiagramTypes.forEach((type) => {
        const item = element("button", "panel-list-item diagram-type-item", diagramLabel(type));
        item.type = "button";
        item.dataset.type = type;
        item.addEventListener("click", () => store.set("diagram", type));
        list.appendChild(item);
      });
      body.appendChild(list);

      body.appendChild(element("hr", "panel-divider"));
      const search = element("input", "panel-search");
      search.type = "search";
      search.placeholder = "Search nodes and namespaces";
      search.setAttribute("aria-label", "Search nodes and namespaces");
      body.appendChild(search);

      const results = element("div", "search-results");
      results.hidden = true;
      const summary = element("div", "panel-hint");
      const tree = element("div", "ns-tree");
      results.append(summary, tree);
      body.appendChild(results);

      const markActive = (current) => {
        list.querySelectorAll(".diagram-type-item").forEach((item) => {
          item.classList.toggle("active", item.dataset.type === current);
        });
      };
      markActive(store.get("diagram"));
      store.onChange("diagram", markActive);

      let root = null;
      let selectedPath = "";

      function renderResults() {
        const query = search.value.trim();
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
            renderResults();
            const entity = node.entities[0];
            if (entity) SD.emit("focusentity", { id: String(entity.unique_id), path: node.path });
          },
        });
        summary.textContent = count ? `${count} match${count > 1 ? "es" : ""}` : "No matches";
      }

      async function loadMode() {
        root = null;
        renderResults();
        const data = await SD.NamespaceTree.loadStructure(config.dataBase, store.get("mode"));
        root = SD.NamespaceTree.build(SD.NamespaceTree.entities(data));
        renderResults();
      }

      search.addEventListener("input", renderResults);
      search.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          search.value = "";
          renderResults();
        }
      });
      store.onChange("mode", () => loadMode().catch((error) => console.warn("explore search:", error)));
      loadMode().catch((error) => console.warn("explore search:", error));
    },
  });
})();
