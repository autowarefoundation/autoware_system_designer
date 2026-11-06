// Namespace tree of a mode's system structure: one tree entry per path segment,
// each carrying the entities (nodes, modules) whose path ends there. Explore
// search and the Monitor panel draw their entity lists with it.
(function () {
  const SD = window.SD;

  // Mode's system structure, shared with the diagram modules through the page data global.
  async function loadStructure(dataBase, mode) {
    const data = window[DiagramBase.DATA_GLOBAL]?.[mode];
    if (data) return data;
    await DiagramBase.loadScript(`${dataBase}${mode}.js`);
    return window[DiagramBase.DATA_GLOBAL]?.[mode] || null;
  }

  // Entities with a path, in design order; the system root is left out.
  function entities(root) {
    const list = [];
    const walk = (instance) => {
      (instance.children || []).forEach((child) => {
        if (child.path && child.unique_id) list.push(child);
        walk(child);
      });
    };
    if (root) walk(root);
    return list;
  }

  // Key naming an entity across activities: its path, with a node's name
  // appended when the path ends elsewhere (a node placed at a module's own path).
  function entityKey(entity) {
    if (entity.entity_type !== "node") return entity.path;
    const last = entity.path.split("/").filter(Boolean).pop();
    return last === entity.name ? entity.path : `${entity.path}/${entity.name}`;
  }

  // { segment, path, entities[], children: Map } rooted at "/".
  function build(entityList) {
    const root = { segment: "/", path: "/", entities: [], children: new Map() };
    entityList.forEach((entity) => {
      let node = root;
      entity.path
        .split("/")
        .filter(Boolean)
        .forEach((segment) => {
          if (!node.children.has(segment)) {
            const path =
              node.path === "/" ? `/${segment}` : `${node.path}/${segment}`;
            node.children.set(segment, {
              segment,
              path,
              entities: [],
              children: new Map(),
            });
          }
          node = node.children.get(segment);
        });
      node.entities.push(entity);
    });
    return root;
  }

  function matches(node, query) {
    return node.segment.toLowerCase().includes(query);
  }

  // Entries that match `query` and their ancestors; every entry when the query is empty.
  function visiblePaths(root, query) {
    const visible = new Set();
    const visit = (node, ancestors) => {
      const chain = [...ancestors, node.path];
      if (!query || matches(node, query))
        chain.forEach((path) => visible.add(path));
      node.children.forEach((child) => visit(child, chain));
    };
    root.children.forEach((child) => visit(child, []));
    return visible;
  }

  /**
   * Renders the tree into `container`.
   * options: query, expanded (Set of open paths), selectedPath, onSelect(node),
   * onToggle(node) after a fold or unfold, selectable(node) whether a click on
   * the row selects it (default: it carries an entity), decorate(node, rowElement)
   * Returns the number of rendered entries that match the query.
   */
  function render(container, root, options = {}) {
    const query = (options.query || "").trim().toLowerCase();
    const visible = visiblePaths(root, query);
    const expanded = options.expanded || new Set();
    const selectable =
      options.selectable || ((node) => node.entities.length > 0);
    let matchCount = 0;
    container.replaceChildren();

    const renderLevel = (parent, list, depth) => {
      Array.from(parent.children.values())
        .sort((a, b) => a.segment.localeCompare(b.segment))
        .forEach((node) => {
          if (!visible.has(node.path)) return;
          const hit = !!query && matches(node, query);
          if (hit) matchCount += 1;
          const hasChildren = Array.from(node.children.values()).some((c) =>
            visible.has(c.path),
          );
          const open = !!query || expanded.has(node.path);

          const row = document.createElement("div");
          row.className = "ns-row";
          row.style.paddingLeft = `${depth * 14 + 4}px`;
          row.classList.toggle("selected", node.path === options.selectedPath);
          row.classList.toggle("has-entity", node.entities.length > 0);
          row.title = node.path;

          // The twisty folds; the rest of the row selects. A row that is not
          // selectable folds from anywhere.
          const toggle = () => {
            if (expanded.has(node.path)) expanded.delete(node.path);
            else expanded.add(node.path);
          };
          const twisty = document.createElement("span");
          twisty.className = "ns-twisty";
          twisty.textContent = hasChildren ? (open ? "▾" : "▸") : "";
          if (hasChildren) {
            twisty.addEventListener("click", (event) => {
              event.stopPropagation();
              toggle();
              options.onToggle?.(node);
            });
          }
          row.appendChild(twisty);

          const kind = node.entities[0]?.entity_type || "namespace";
          const label = document.createElement("span");
          label.className = `ns-label ns-${kind}`;
          label.textContent = node.segment;
          if (hit) label.classList.add("match");
          row.appendChild(label);
          options.decorate?.(node, row);

          row.addEventListener("click", () => {
            if (hasChildren && !selectable(node)) {
              toggle();
              options.onToggle?.(node);
            }
            options.onSelect?.(node);
          });
          list.appendChild(row);
          if (hasChildren && open) renderLevel(node, list, depth + 1);
        });
    };
    renderLevel(root, container, 0);
    return matchCount;
  }

  SD.NamespaceTree = { loadStructure, entities, entityKey, build, render };
})();
