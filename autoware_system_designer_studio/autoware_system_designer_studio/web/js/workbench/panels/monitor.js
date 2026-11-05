// Monitor panel: the launched system as a namespace tree with each member's
// run state; selecting a member opens its terminal log as a main-area tab.
(function () {
  const SD = window.SD;
  const LOG_LINES_SHOWN = 5000;
  const STATES = ["running", "ok", "warning", "failed", "idle"];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  // Terminal view of one member: header with its path and state, then the log.
  function logView(path) {
    const view = element("div", "member-log");
    const head = element("div", "member-log-head");
    head.append(element("span", "member-log-path", path), element("span", "member-state idle", "idle"));
    const log = element("pre", "terminal-log");
    log.appendChild(element("span", "terminal-placeholder", "No output yet."));
    view.append(head, log);
    return view;
  }

  function appendLines(view, lines) {
    const log = view.querySelector(".terminal-log");
    log.querySelector(".terminal-placeholder")?.remove();
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
    log.appendChild(document.createTextNode(lines.join("\n") + "\n"));
    while (log.childNodes.length > LOG_LINES_SHOWN) log.firstChild.remove();
    if (atBottom) log.scrollTop = log.scrollHeight;
  }

  SD.workbench.registerPanel({
    id: "monitor",
    title: "Monitor",
    order: 50,
    daemon: true,
    requires: ["launch.status"],
    icon: "M2 10h4l2-5 4 10 2-5h4",
    mount(body, { config, store }) {
      const filter = element("input", "panel-search");
      filter.type = "search";
      filter.placeholder = "Filter by namespace or name";
      filter.setAttribute("aria-label", "Filter members");
      const tree = element("div", "ns-tree");
      body.append(filter, tree);

      // Member path -> run state; path -> open log view.
      const states = new Map();
      const views = new Map();
      const expanded = new Set();
      let root = null;
      let selectedPath = "";

      function decorate(node, row) {
        if (!node.entities.some((e) => e.entity_type === "node")) return;
        const state = states.get(node.path) || "idle";
        const dot = element("span", `state-dot ${STATES.includes(state) ? state : "idle"}`);
        dot.title = state;
        row.insertBefore(dot, row.querySelector(".ns-label"));
      }

      function render() {
        if (!root) return;
        SD.NamespaceTree.render(tree, root, {
          query: filter.value,
          expanded,
          selectedPath,
          decorate,
          onSelect(node) {
            selectedPath = node.path;
            if (node.entities.some((e) => e.entity_type === "node")) openLog(node.path);
            render();
          },
        });
      }

      function openLog(path) {
        const id = `log:${path}`;
        const view = SD.workbench.openMainTab(id, path, views.get(path) || logView(path));
        views.set(path, view);
        showState(path);
      }

      function showState(path) {
        const badge = views.get(path)?.querySelector(".member-state");
        if (!badge) return;
        const state = states.get(path) || "idle";
        badge.textContent = state;
        badge.className = `member-state ${state}`;
      }

      async function loadMode() {
        const data = await SD.NamespaceTree.loadStructure(config.dataBase, store.get("mode"));
        root = SD.NamespaceTree.build(SD.NamespaceTree.entities(data));
        // Every namespace starts expanded.
        expanded.clear();
        const expand = (node) => node.children.forEach((child) => {
          expanded.add(child.path);
          expand(child);
        });
        expand(root);
        render();
      }

      SD.on("maintabclose", ({ id }) => {
        if (id.startsWith("log:")) views.delete(id.slice(4));
      });
      // Member state and log lines, keyed by member path.
      SD.on("daemon.member", ({ path, state }) => {
        states.set(path, state);
        showState(path);
        render();
      });
      SD.on("daemon.member.log", ({ path, lines }) => {
        const view = views.get(path);
        if (view) appendLines(view, lines);
      });
      filter.addEventListener("input", render);
      store.onChange("mode", () => loadMode().catch((error) => console.warn("monitor:", error)));
      loadMode().catch((error) => console.warn("monitor:", error));
    },
  });
})();
