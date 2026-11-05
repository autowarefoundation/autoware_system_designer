// Explore: entity search over the mode's namespace tree; a hit is located on
// the diagram through `onLocate(uniqueId)`.
(function () {
  const SD = window.SD;
  const { element, section } = SD.widgets;
  const explore = (SD.activities.explore ||= {});

  function entitySearch(config, store, onLocate) {
    const input = element("input", "panel-search");
    input.type = "search";
    input.placeholder = "Search nodes and namespaces";
    input.setAttribute("aria-label", "Search nodes and namespaces");
    const summary = element("div", "panel-hint");
    const tree = element("div", "ns-tree");
    const results = element("div", "search-results");
    results.hidden = true;
    results.append(summary, tree);
    const group = section("Search", input, results);

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
    return group;
  }

  explore.entitySearch = entitySearch;
})();
