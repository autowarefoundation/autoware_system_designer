// Launch activity: launch options and per-unit launch controls beside the
// launch commands table of the mode and deploy, or the state monitor of the
// launched system when the daemon reports one.
(function () {
  const SD = window.SD;
  const { element, field, boundSelect, segmented, copyButton, terminal } = SD.widgets;
  const METHOD_KEY = "workbench.launchMethod";
  const METHODS = [
    ["native", "Runtime"],
    ["xml", "XML"],
  ];
  const METHOD_NOTES = {
    native: "Runs the system structure under the designer runtime supervisor, one supervised process per node.",
    xml: "Runs the exported XML launcher through ros2 launch.",
  };
  const STATES = ["running", "ok", "warning", "failed", "idle"];

  function launchRows(config, store) {
    const mode = store.get("mode");
    const deploy = store.get("deploy");
    return config.launchCommands.filter((r) => r.mode === mode && r.deploy === deploy);
  }

  // Launch commands of the selected mode and deploy, one row per compute unit.
  function commandsTable(container) {
    const table = element("table", "launch-table");
    const head = element("thead");
    const headRow = element("tr");
    ["Compute unit", "Status", "Command", ""].forEach((label) => headRow.appendChild(element("th", "", label)));
    head.appendChild(headRow);
    const body = element("tbody");
    table.append(head, body);
    const empty = element("p", "main-empty");
    container.append(table, empty);

    function render(rows, method, unitStatus, emptyText) {
      table.hidden = !rows.length;
      empty.hidden = !!rows.length;
      empty.textContent = emptyText;
      table.classList.toggle("no-status", !unitStatus.size);
      body.replaceChildren(
        ...rows.map((row) => {
          const tr = element("tr");
          const status = unitStatus.get(row.unit) || "";
          const statusCell = element("td", "launch-status-cell");
          if (status) statusCell.appendChild(element("span", `unit-status ${status}`, status.replace(/_/g, " ")));
          const command = row[method];
          const commandCell = element("td", "command-cell");
          const copyCell = element("td", "copy-cell");
          if (command) {
            commandCell.appendChild(element("code", "", command));
            copyCell.appendChild(copyButton(command));
          } else {
            commandCell.appendChild(element("span", "command-missing", "Not available for this method"));
          }
          tr.append(element("td", "launch-unit", row.unit), statusCell, commandCell, copyCell);
          return tr;
        }),
      );
    }
    return { render };
  }

  // Launched members as a namespace tree with run states beside the selected member's log.
  function stateMonitor(container, config, store) {
    const treePane = element("div", "monitor-tree");
    const filter = element("input", "panel-search");
    filter.type = "search";
    filter.placeholder = "Filter by namespace or name";
    filter.setAttribute("aria-label", "Filter members");
    const tree = element("div", "ns-tree");
    treePane.append(filter, tree);

    const logPane = element("div", "log-view");
    const head = element("div", "main-view-head");
    const pathLabel = element("span", "main-view-title member-path");
    const badge = element("span", "member-state idle", "idle");
    head.append(pathLabel, badge);
    const empty = element("p", "main-empty", "Select a node to follow its log.");
    logPane.append(head, empty);
    container.append(treePane, logPane);

    // Member path -> run state; path -> its terminal, kept while the page lives.
    const states = new Map();
    const logs = new Map();
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

    function logOf(path) {
      if (!logs.has(path)) logs.set(path, terminal("No output yet."));
      return logs.get(path);
    }

    function renderTree() {
      if (!root) return;
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
      logPane.querySelector(".terminal-log")?.remove();
      empty.hidden = true;
      logPane.appendChild(logOf(path).element);
      pathLabel.textContent = path;
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

    // Member state and log lines, keyed by member path.
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
  }

  SD.workbench.registerActivity({
    id: "launch",
    title: "Launch",
    order: 20,
    daemon: false,
    inspector: false,
    scope: ["deploy"],
    icon: "M6 4l10 6-10 6z",
    mount({ side, main }, { config, store, daemon }) {
      const hasNative = config.launchCommands.some((record) => record.native);
      let method = SD.storage.get(METHOD_KEY);
      if (method !== "xml" && !(method === "native" && hasNative)) method = hasNative ? "native" : "xml";
      let unitStatus = new Map();
      let view = "commands";

      // -- side: options and launch controls
      if (config.deployNames.length) {
        side.appendChild(field("Deploy", boundSelect("deploy", config.deployNames.map((name) => [name, name]))));
      }
      const methodSwitch = segmented(
        METHODS.filter(([value]) => value !== "native" || hasNative),
        (value) => {
          method = value;
          SD.storage.set(METHOD_KEY, value);
          render();
        },
      );
      const methodRow = element("div", "panel-row");
      methodRow.append(element("span", "panel-row-label", "Command"), methodSwitch.element);
      const methodNote = element("p", "panel-note");
      side.append(methodRow, methodNote);

      const unitsTitle = element("div", "panel-section-title", "Compute units");
      const unitList = element("div", "launch-list");
      side.append(unitsTitle, unitList);

      // -- main: commands table or state monitor
      const viewSwitch = segmented(
        [
          ["monitor", "Monitor"],
          ["commands", "Commands"],
        ],
        (value) => {
          view = value;
          render();
        },
      );
      const mainHead = element("div", "main-view-head");
      const mainTitle = element("span", "main-view-title");
      mainHead.append(mainTitle, viewSwitch.element);
      const commandsView = element("div", "launch-commands-view");
      const monitorView = element("div", "launch-monitor-view");
      main.append(mainHead, commandsView, monitorView);
      const table = commandsTable(commandsView);
      let monitorMounted = false;

      function canMonitor() {
        return daemon.offers("launch.status");
      }

      function renderUnits(rows) {
        const canLaunch = daemon.offers("launch.start");
        unitsTitle.hidden = unitList.hidden = !canLaunch;
        unitList.replaceChildren();
        if (!canLaunch) return;
        rows.forEach((row) => {
          const card = element("div", "launch-card");
          const status = unitStatus.get(row.unit);
          const head = element("div", "launch-card-head");
          head.appendChild(element("span", "launch-unit", row.unit));
          if (status) head.appendChild(element("span", `unit-status ${status}`, status.replace(/_/g, " ")));
          const launch = element("button", "panel-button primary", "Launch");
          launch.type = "button";
          launch.disabled = status !== "available";
          launch.addEventListener("click", () => {
            const params = { system: config.systemId, mode: row.mode, compute_unit: row.unit };
            if (row.deploy) params.deploy = row.deploy;
            launch.disabled = true;
            daemon
              .rpc("launch.start", params)
              .then(() => {
                view = "monitor";
                render();
              })
              .catch((error) => {
                launch.disabled = false;
                launch.title = error.message;
              });
          });
          card.append(head, launch);
          unitList.appendChild(card);
        });
      }

      function render() {
        methodSwitch.mark(method);
        methodNote.textContent = METHOD_NOTES[method];
        const rows = launchRows(config, store);
        const mode = store.get("mode");
        renderUnits(rows);

        if (!canMonitor()) view = "commands";
        viewSwitch.element.hidden = !canMonitor();
        viewSwitch.mark(view);
        commandsView.hidden = view !== "commands";
        monitorView.hidden = view !== "monitor";
        const deploy = store.get("deploy");
        mainTitle.textContent =
          view === "monitor" ? "State monitor" : `Launch commands · ${mode}${deploy ? ` · ${deploy}` : ""}`;
        if (view === "monitor" && !monitorMounted) {
          monitorMounted = true;
          stateMonitor(monitorView, config, store);
        }
        table.render(rows, method, unitStatus, `No launchers for mode ${mode}.`);
      }

      async function loadUnitStatus() {
        try {
          const system = await daemon.system(config.systemId);
          unitStatus = new Map((system?.compute_units || []).map((u) => [u.name, u.status]));
        } catch (error) {
          console.warn("launch:", error);
        }
        render();
      }

      ["mode", "deploy"].forEach((key) => store.onChange(key, render));
      SD.on("daemon.health", loadUnitStatus);
      SD.on("daemon.systems.changed", loadUnitStatus);
      if (daemon.health) {
        if (canMonitor()) view = "monitor";
        loadUnitStatus();
      }
      render();
    },
  });
})();
