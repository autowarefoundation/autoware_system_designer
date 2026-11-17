// Replay: the scope selector, the mode's namespace tree with one checkbox per
// entry. A checked entry selects every launched node at or under its path; a
// node placed at a module's own path has an entry of its own below it.
(function () {
  const SD = window.SD;
  const { element, section } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const { selects } = replay.model;

  // `selection` is { nodes(), selected(): Set of node paths, toggle(path, on), clear() }.
  function scopeTree(selection) {
    const input = element("input", "panel-search");
    input.type = "search";
    input.placeholder = "Filter nodes and namespaces";
    input.setAttribute("aria-label", "Filter nodes and namespaces");
    const summary = element("p", "panel-note");
    const clear = element("button", "panel-button", "Clear");
    clear.type = "button";
    const head = element("div", "replay-scope-head");
    head.append(summary, clear);
    const tree = element("div", "ns-tree");
    const group = section("Scope", input, head, tree);

    let root = null;
    const expanded = new Set();

    function render() {
      const selected = selection.selected();
      const nodes = selection.nodes();
      summary.textContent = `${selected.size} of ${nodes.length} nodes`;
      clear.disabled = !selected.size;
      if (!root) {
        tree.replaceChildren(element("p", "panel-empty", "Loading…"));
        return;
      }
      SD.NamespaceTree.render(tree, root, {
        query: input.value,
        expanded,
        selectable: () => false,
        onToggle: render,
        decorate(entry, row) {
          const members = nodes.filter((node) => selects(node, entry.path));
          const chosen = members.filter((node) => selected.has(node.path));
          const box = document.createElement("input");
          box.type = "checkbox";
          box.className = "replay-scope-check";
          box.disabled = !members.length;
          box.checked = members.length > 0 && chosen.length === members.length;
          box.indeterminate = chosen.length > 0 && !box.checked;
          box.setAttribute("aria-label", `Replay ${entry.path}`);
          box.addEventListener("click", (event) => event.stopPropagation());
          box.addEventListener("change", () =>
            selection.toggle(entry.path, box.checked),
          );
          row.insertBefore(box, row.querySelector(".ns-label"));
          row.classList.toggle("in-scope", chosen.length > 0);
        },
      });
    }

    // Entities of the structure draw the tree; top-level entries start open.
    function setStructure(data) {
      root = data
        ? SD.NamespaceTree.build(
            SD.NamespaceTree.entities(data).map((entity) => ({
              ...entity,
              path: SD.NamespaceTree.entityKey(entity),
            })),
          )
        : null;
      expanded.clear();
      if (root) root.children.forEach((child) => expanded.add(child.path));
      render();
    }

    input.addEventListener("input", render);
    clear.addEventListener("click", () => selection.clear());
    return { element: group, setStructure, render };
  }

  replay.scopeTree = scopeTree;
})();
