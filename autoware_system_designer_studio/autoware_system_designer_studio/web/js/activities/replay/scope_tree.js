// Replay: the scope selector, the mode's namespace tree with one checkbox per
// entry. A checked entry selects every launched node at or under its path; a
// node placed at a module's own path has an entry of its own below it. The
// container of a selected composable node is checked and locked.
(function () {
  const SD = window.SD;
  const { element, section } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const { selects } = replay.model;

  // `selection` is { nodes(), selected(): Set of node paths, locked(): Set of
  // container paths the selected nodes need, toggle(path, on), clear() };
  // `extras` are shown between the head and the tree.
  function scopeTree(selection, ...extras) {
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
    const group = section("Scope", input, head, ...extras, tree);

    let root = null;
    const expanded = new Set();

    function render() {
      const selected = selection.selected();
      const locked = selection.locked();
      const nodes = selection.nodes();
      const inScope = new Set([...selected, ...locked]);
      summary.textContent = `${inScope.size} of ${nodes.length} nodes`;
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
          const chosen = members.filter((node) => inScope.has(node.path));
          const fixed =
            members.length > 0 &&
            members.every((node) => locked.has(node.path));
          const box = document.createElement("input");
          box.type = "checkbox";
          box.className = "replay-scope-check";
          box.disabled = !members.length || fixed;
          if (fixed) box.title = "Container of a selected composable node";
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
