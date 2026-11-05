// Launch: launched members as a namespace tree with run states beside the
// selected member's log. Member state and log lines arrive on the daemon's
// event stream, keyed by member path.
(function () {
  const SD = window.SD;
  const { element, terminal, mainView } = SD.widgets;
  const launch = (SD.activities.launch ||= {});
  const STATES = ["running", "ok", "warning", "failed", "idle"];

  function stateMonitor(container, config, store) {
    const treePane = element("div", "pane-side monitor-tree");
    const filter = element("input", "panel-search");
    filter.type = "search";
    filter.placeholder = "Filter by namespace or name";
    filter.setAttribute("aria-label", "Filter members");
    const tree = element("div", "ns-tree");
    treePane.append(filter, tree);

    const view = mainView({ empty: "Select a node to follow its log." });
    view.head.classList.add("member-head");
    const badge = element("span", "member-state idle");
    view.trailing.appendChild(badge);
    container.append(treePane, view.element);

    // Member path -> run state; path -> its terminal, kept while the page lives.
    const states = new Map();
    const logs = new Map();
    const expanded = new Set();
    let root = null;
    let selectedPath = "";
    // The tree renders only while shown; a change made while hidden renders on the next show.
    let hidden = false;
    let stale = false;

    function decorate(node, row) {
      if (!node.entities.some((e) => e.entity_type === "node")) return;
      const state = states.get(node.path) || "idle";
      const dot = element("span", `state-dot ${STATES.includes(state) ? state : "idle"}`);
      dot.title = state;
      row.insertBefore(dot, row.querySelector(".ns-label"));
    }

    function logOf(path) {
      if (!logs.has(path)) logs.set(path, terminal("No output yet."));
      return logs.get(path);
    }

    function renderTree() {
      if (!root) return;
      if (hidden) {
        stale = true;
        return;
      }
      SD.NamespaceTree.render(tree, root, {
        query: filter.value,
        expanded,
        selectedPath,
        decorate,
        onSelect(node) {
          if (node.entities.some((e) => e.entity_type === "node")) showLog(node.path);
          renderTree();
        },
      });
    }

    function showLog(path) {
      selectedPath = path;
      view.body.replaceChildren(logOf(path).element);
      view.setEmpty("");
      view.setTitle(path);
      showState();
    }

    function showState() {
      const state = states.get(selectedPath) || "idle";
      badge.textContent = selectedPath ? state : "";
      badge.className = `member-state ${state}`;
    }

    async function loadMode() {
      const data = await SD.NamespaceTree.loadStructure(config.dataBase, store.get("mode"));
      root = SD.NamespaceTree.build(SD.NamespaceTree.entities(data));
      // Every namespace starts expanded.
      expanded.clear();
      const expand = (node) =>
        node.children.forEach((child) => {
          expanded.add(child.path);
          expand(child);
        });
      expand(root);
      renderTree();
    }

    SD.on("daemon.member", ({ path, state }) => {
      states.set(path, state);
      if (path === selectedPath) showState();
      renderTree();
    });
    SD.on("daemon.member.log", ({ path, lines }) => logOf(path).append(lines));
    filter.addEventListener("input", renderTree);
    const reload = () => loadMode().catch((error) => console.warn("launch monitor:", error));
    store.onChange("mode", reload);
    reload();

    return {
      show() {
        hidden = false;
        if (!stale) return;
        stale = false;
        renderTree();
      },
      hide() {
        hidden = true;
      },
    };
  }

  launch.stateMonitor = stateMonitor;
})();
