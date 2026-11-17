// Replay activity: the scope of a deterministic replay chosen on the mode's
// namespace tree, drawn as a graph inside its boundary: the inputs a bag
// feeds, each joined to the bag topic that feeds it and remapped where the bag
// names it otherwise, and the outputs recorded. The view stacks three zones,
// each folded or expanded and sized by the dividers between them: the diagram;
// the inputs, outputs and service ports that leave the scope, with the rosbag
// in its head; and the run, whose popup holds the replay configuration and
// command the choices give. The bag is a file or path dropped on the view, a file
// opened, or a path typed. With a daemon, the bag is read by its path and the
// replay runs as a job.
(function () {
  const SD = window.SD;
  const { element, copyButton, mainView, popup } = SD.widgets;
  const { model, scopeTree, scopeDiagram, bagFile, runPanel, zoneStack } =
    SD.activities.replay;

  const { ACCEPT } = model;
  const INPUT_NOTE = {
    fed: "in the bag",
    remapped: "remapped",
    accepted: "accepted unfed",
    type: "other type in the bag",
    unpublished: "no publisher, not in the bag",
    missing: "not in the bag",
    unknown: "no bag read",
  };
  // Preference: height share and fold of each zone of the view.
  const ZONES_KEY = "replay.zones";
  // Weights are heights in pixels, the scale a divider drag stores.
  const ZONES = [
    { id: "diagram", title: "Diagram", weight: 440 },
    { id: "boundary", title: "Inputs and outputs", weight: 260 },
    { id: "run", title: "Run", weight: 300 },
  ];
  // Preference: directories of the bags read by path, as a JSON list.
  const BAG_DIRS_KEY = "replay.bagDirs";
  const BAG_DIRS_KEPT = 5;

  function table(columns) {
    const node = element("table", "replay-table");
    const head = node.createTHead().insertRow();
    columns.forEach((title) => head.appendChild(element("th", "", title)));
    const body = node.createTBody();
    return {
      element: node,
      clear: () => body.replaceChildren(),
      row(cells, className) {
        const row = body.insertRow();
        if (className) row.className = className;
        cells.forEach((cell) => {
          const td = row.insertCell();
          if (cell instanceof Node) td.appendChild(cell);
          else td.textContent = cell ?? "";
        });
        return row;
      },
    };
  }

  function topicCell(topic, type) {
    const cell = element("div", "replay-topic");
    cell.append(
      element("span", "replay-topic-name", topic),
      element("span", "replay-topic-type", type),
    );
    return cell;
  }

  // Check list of topics under a head that clears every topic it may, or
  // checks them all once none is; `toggle(topic, on)` takes each change.
  function topicSelector(title, toggle) {
    const node = element("div", "replay-selector");
    const all = document.createElement("input");
    all.type = "checkbox";
    const count = element("span", "replay-block-count");
    const head = element("label", "replay-selector-head");
    head.append(all, element("span", "", title), count);
    const list = element("div", "replay-selector-list");
    node.append(head, list);
    let items = [];
    all.addEventListener("change", () => {
      const on = !items.some((item) => item.checked && !item.fixed);
      items
        .filter((item) => !item.fixed && item.checked !== on)
        .forEach((item) => toggle(item.topic, on));
    });
    return {
      element: node,
      // `next` is [{ topic, note, level, checked, fixed }].
      set(next) {
        items = next;
        const checked = items.filter((item) => item.checked).length;
        all.checked = items.length > 0 && checked === items.length;
        all.indeterminate = checked > 0 && !all.checked;
        all.disabled = items.every((item) => item.fixed);
        count.textContent = `${checked} of ${items.length}`;
        list.replaceChildren(
          ...items.map((item) => {
            const box = document.createElement("input");
            box.type = "checkbox";
            box.checked = item.checked;
            box.disabled = !!item.fixed;
            box.addEventListener("change", () =>
              toggle(item.topic, box.checked),
            );
            const row = element("label", `replay-selector-item ${item.level}`);
            row.append(
              box,
              element("span", "replay-topic-name", item.topic),
              element("span", "replay-selector-note", item.note),
            );
            return row;
          }),
        );
      },
    };
  }

  function block(title, note) {
    const node = element("section", "replay-block");
    const head = element("div", "replay-block-head");
    const count = element("span", "replay-block-count");
    head.append(element("h3", "", title), count);
    node.appendChild(head);
    if (note) node.appendChild(element("p", "panel-note", note));
    return { element: node, count };
  }

  function codeBlock(title) {
    const node = element("div", "replay-code");
    const head = element("div", "replay-code-head");
    const actions = element("div", "replay-code-actions");
    head.append(element("span", "", title), actions);
    const pre = element("pre");
    node.append(head, pre);
    return {
      element: node,
      actions,
      set(text, ...buttons) {
        pre.textContent = text;
        actions.replaceChildren(copyButton(text), ...buttons);
      },
    };
  }

  SD.workbench.registerActivity({
    id: "replay",
    title: "Replay",
    group: "Replay",
    order: 25,
    daemon: false,
    inspector: false,
    icon: "M4 4v12M7 10l9-6v12z",
    status: (store) => {
      const scope =
        store.get("scope") ??
        new URLSearchParams(window.location.search).get("scope");
      const patterns = (scope || "").split(",").filter(Boolean);
      return [
        patterns.length
          ? `scope: ${patterns.length} path${patterns.length > 1 ? "s" : ""}`
          : "no scope",
      ];
    },
    mount({ side, main }, { config, store, daemon }) {
      store.define("scope", "");
      store.syncUrl();

      let nodes = [];
      let selected = new Set();
      // Bag topics by recorded name; null until a bag is read.
      let bag = null;
      let bagName = "";
      // What reading the bag last failed with, shown in the bag's place.
      let bagNote = "";
      // Recorded topic -> boundary input it is fed as.
      const remap = {};
      const accept = new Set();
      const recordedInternals = new Set();
      // Boundary outputs left unrecorded.
      const skippedOutputs = new Set();
      // Boundary of the scope shown, with the inputs matched against the bag.
      let shownBound = { inputs: [], outputs: [] };
      // Parameters of a replay of the shown choices, or what it still lacks.
      let request = { blocker: "Check nodes or modules to replay." };

      const patterns = () => model.patternsOf(nodes, selected);

      function setSelected(next) {
        selected = next;
        store.set("scope", patterns().join(","));
        tree.render();
        render();
      }

      // An internal topic is recorded once chosen, a boundary output unless skipped.
      function setRecorded(topic, on) {
        const output = shownBound.outputs.find((o) => o.topic === topic);
        if (!output) return;
        if (output.boundary) {
          if (on) skippedOutputs.delete(topic);
          else skippedOutputs.add(topic);
        } else if (on) recordedInternals.add(topic);
        else recordedInternals.delete(topic);
        render();
      }

      // A fed input expects its topic from the bag; one not fed is accepted
      // unfed, without the remap onto it.
      function setFed(topic, on) {
        if (on) accept.delete(topic);
        else {
          Object.keys(remap).forEach((recorded) => {
            if (remap[recorded] === topic) delete remap[recorded];
          });
          accept.add(topic);
        }
        render();
      }

      const tree = scopeTree({
        nodes: () => nodes,
        selected: () => selected,
        locked: () => new Set(model.selectScope(nodes, patterns()).containers),
        toggle(path, on) {
          const next = new Set(selected);
          nodes
            .filter((node) => model.selects(node, path))
            .forEach((node) =>
              on ? next.add(node.path) : next.delete(node.path),
            );
          setSelected(next);
        },
        clear: () => setSelected(new Set()),
      });
      side.appendChild(tree.element);

      const view = mainView({ title: "Replay scope" });
      main.appendChild(view.element);
      const diagram = scopeDiagram({
        choices: (input) => model.inputChoices(input, bag),
        choose(input, value) {
          Object.keys(remap).forEach((recorded) => {
            if (remap[recorded] === input.topic) delete remap[recorded];
          });
          accept.delete(input.topic);
          if (value === ACCEPT) accept.add(input.topic);
          else if (value && value !== input.topic) remap[value] = input.topic;
          render();
        },
        record: setRecorded,
      });
      const stack = zoneStack(ZONES_KEY, ZONES);
      const { diagram: diagramZone, boundary: boundaryZone, run: runZone } =
        stack.zones;
      diagramZone.body.appendChild(diagram.element);
      view.body.appendChild(stack.element);
      // The activity's slots are visible from mount until another activity takes over.
      let visible = true;

      // -- rosbag --------------------------------------------------------
      const bagBox = element("div", "replay-bag-bar");
      const bagPath = element("input", "panel-search");
      bagPath.type = "text";
      bagPath.placeholder = "Bag path, or drop a bag";
      bagPath.title =
        "Path the replay reads the bag from. A bag dropped on the view gives its topics; " +
        "a dropped file gives no path.";
      bagPath.setAttribute("aria-label", "Bag path");
      const bagInput = document.createElement("input");
      bagInput.type = "file";
      bagInput.accept = ".yaml,.yml,.mcap";
      bagInput.hidden = true;
      const bagLoad = element("button", "panel-button", "Open file");
      bagLoad.type = "button";
      bagLoad.title = "Read the topics of a metadata.yaml or .mcap file";
      const bagRead = element("button", "panel-button", "Read");
      bagRead.type = "button";
      bagRead.title =
        "Read the topics of the bag at this path through the daemon; a bag without metadata.yaml gets one";
      const bagState = element("span", "replay-bag-state");
      const bagRow = element("div", "replay-bag-row");
      bagRow.append(bagRead, bagLoad, bagInput);
      bagBox.append(
        element("span", "replay-bag-label", "Rosbag"),
        bagPath,
        bagRow,
        bagState,
      );
      bagBox.dataset.zoneKeep = "";
      boundaryZone.head.insertBefore(bagBox, boundaryZone.actions);

      // -- boundary ------------------------------------------------------
      const selectorBlock = { element: element("div", "replay-block") };
      selectorBlock.element.appendChild(
        element(
          "p",
          "panel-note",
          "A checked input is fed from the bag, an unchecked one is accepted unfed. " +
            "A checked output is recorded; the head of a list clears it, or checks all of a cleared one.",
        ),
      );
      const inputSelector = topicSelector("Inputs", setFed);
      const outputSelector = topicSelector("Outputs", setRecorded);
      const selectors = element("div", "replay-selectors");
      selectors.append(inputSelector.element, outputSelector.element);
      selectorBlock.element.appendChild(selectors);

      const serviceBlock = block(
        "Services leaving the scope",
        "Replay feeds no service: a client without its server stops the pre-flight unless accepted.",
      );
      const services = table(["Service", "Node", "Role", "Accept"]);
      serviceBlock.element.appendChild(services.element);

      // -- configuration and command popup -------------------------------
      const commands = popup("Configuration and command");
      const configCode = codeBlock(model.CONFIG_FILE);
      const checkCode = codeBlock("Pre-flight");
      const runCode = codeBlock("Replay");
      commands.body.append(
        configCode.element,
        checkCode.element,
        runCode.element,
      );
      const commandsOpen = element(
        "button",
        "panel-button",
        "Configuration and command",
      );
      commandsOpen.type = "button";
      commandsOpen.title =
        "The replay configuration file and the commands that run it";
      commandsOpen.setAttribute("aria-haspopup", "dialog");
      commandsOpen.addEventListener("click", commands.open);

      // -- zones: the boundary, then the run --------------------------------
      const lists = element("div", "replay-boundary-lists");
      lists.append(selectorBlock.element, serviceBlock.element);
      boundaryZone.body.appendChild(lists);
      const panel = runPanel(daemon, () => request);
      runZone.actions.appendChild(commandsOpen);
      // Without the daemon's replay, the popup's commands run the replay.
      const runNote = element(
        "p",
        "panel-note",
        "Running a replay from here needs the studio daemon; without it, run the commands of Configuration and command in a shell.",
      );
      runZone.body.append(runNote, panel.element);
      // Parts a scope fills.
      const scopeBlocks = [
        selectorBlock.element,
        serviceBlock.element,
        commandsOpen,
      ];

      function checkbox(checked, onChange, disabled = false) {
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = checked;
        box.disabled = disabled;
        box.addEventListener("change", () => onChange(box.checked));
        return box;
      }

      // Remaps and accepted names of topics the scope still holds.
      function current(bound) {
        const inputTopics = new Set(bound.inputs.map((input) => input.topic));
        const leaving = new Set(
          bound.services
            .filter((edge) => !edge.scopePeers.length)
            .map((edge) => edge.name),
        );
        const internal = new Set(
          bound.outputs.filter((o) => !o.boundary).map((o) => o.topic),
        );
        return {
          remap: Object.fromEntries(
            Object.entries(remap).filter(
              ([recorded, fed]) =>
                inputTopics.has(fed) && (!bag || bag.has(recorded)),
            ),
          ),
          accept: [...accept].filter(
            (name) =>
              (inputTopics.has(name) && !bag?.has(name)) || leaving.has(name),
          ),
          outputs: [...recordedInternals].filter((t) => internal.has(t)),
          skip_outputs: bound.outputs
            .filter((o) => o.boundary && skippedOutputs.has(o.topic))
            .map((o) => o.topic),
        };
      }

      function render() {
        const scope = model.selectScope(nodes, patterns());
        bagRead.hidden = !daemon.health || !daemon.offers("replay.bag");
        bagRead.disabled = !daemon.connected || !bagPath.value.trim();
        // A bag read from a file names no path for the replay to open.
        const pathless = !!bag && !bagPath.value.trim();
        bagPath.classList.toggle("blocked", pathless);
        if (bagNote) bagState.textContent = bagNote;
        else
          bagState.textContent = bag
            ? `${bagName}: ${bag.size} topics${pathless ? ", path not given" : ""}`
            : "no bag read";
        scopeBlocks.forEach((node) => {
          node.hidden = !scope.nodes.length;
        });
        if (!scope.nodes.length) {
          commands.close();
          if (visible) diagram.show(null);
          view.setTitle("Replay scope");
          request = { blocker: "Check nodes or modules to replay." };
          panel.refresh();
          runNote.hidden = !panel.element.hidden;
          return;
        }
        const members =
          `${scope.nodes.length} node${scope.nodes.length > 1 ? "s" : ""}` +
          (scope.containers.length
            ? `, ${scope.containers.length} container${scope.containers.length > 1 ? "s" : ""}`
            : "");
        view.setTitle(`Replay scope: ${members}`);
        const bound = model.boundary(nodes, scope.nodes);
        const kept = current(bound);
        const matched = model.matchInputs(
          bound,
          bag,
          kept.remap,
          new Set(kept.accept),
        );

        shownBound = bound;
        const skipped = new Set(kept.skip_outputs);
        inputSelector.set(
          matched.map((input) => ({
            topic: input.topic,
            note: INPUT_NOTE[input.status],
            level: input.status,
            checked: input.status !== "accepted",
            // The bag feeds a topic it holds under the input's name.
            fixed: !!bag && bag.has(input.topic),
          })),
        );
        outputSelector.set(
          [
            ...bound.outputs.filter((output) => output.boundary),
            ...bound.outputs.filter((output) => !output.boundary),
          ].map((output) => ({
            topic: output.topic,
            note: output.boundary ? "boundary" : "internal",
            level: output.boundary ? "boundary" : "internal",
            checked: output.boundary
              ? !skipped.has(output.topic)
              : kept.outputs.includes(output.topic),
          })),
        );

        if (visible) {
          const unfed = matched.filter((input) =>
            ["missing", "type"].includes(input.status),
          ).length;
          const remapped = matched.filter(
            (input) => input.status === "remapped",
          ).length;
          const drawn = model.graph(
            nodes,
            scope,
            bound,
            matched,
            new Set(kept.outputs),
            skipped,
          );
          const recorded =
            bound.outputs.filter((o) => o.boundary).length -
            skipped.size +
            kept.outputs.length;
          diagram.show({
            ...drawn,
            bagTitle: bag ? `Bag: ${bagName}` : "Bag",
            bagNote: bag
              ? "no topic of the bag feeds the scope"
              : "no bag read: drop one on the view",
            scopeTitle: `Replay boundary: ${members}`,
            summary:
              `${matched.length} input${matched.length === 1 ? "" : "s"}` +
              (bag ? `, ${unfed} unfed` : "") +
              (remapped ? `, ${remapped} remapped` : "") +
              `, ${recorded} of ${bound.outputs.length} outputs recorded`,
            blocked: unfed > 0,
          });
        }

        services.clear();
        const leaving = bound.services.filter(
          (edge) => !edge.scopePeers.length,
        );
        leaving.forEach((edge) => {
          services.row(
            [
              topicCell(edge.name, edge.type),
              edge.node,
              edge.role,
              checkbox(accept.has(edge.name), (on) => {
                if (on) accept.add(edge.name);
                else accept.delete(edge.name);
                render();
              }),
            ],
            `replay-service ${edge.role}${accept.has(edge.name) ? " accepted" : ""}`,
          );
        });
        serviceBlock.count.textContent = `${leaving.length}`;
        serviceBlock.element.hidden = !leaving.length;

        const yaml = model.configYaml({ patterns: patterns(), ...kept });
        const download = element("a", "copy-btn", "Download");
        download.href = `data:text/yaml;charset=utf-8,${encodeURIComponent(yaml)}`;
        download.download = model.CONFIG_FILE;
        configCode.set(yaml, download);
        const args = {
          structure: model.structurePath(config, store.get("mode")),
          bag: bagPath.value.trim(),
          deploy: store.get("deploy") || "",
        };
        checkCode.set(model.command({ ...args, check: true }));
        runCode.set(model.command(args));
        request = args.bag
          ? {
              params: {
                system: config.systemId,
                mode: store.get("mode"),
                bag: args.bag,
                deploy: args.deploy,
                scope: patterns(),
                ...kept,
              },
            }
          : {
              blocker: bag
                ? "Type the path of the bag in the Bag field: a file gives none."
                : "Give the path of the bag.",
            };
        panel.refresh();
        runNote.hidden = !panel.element.hidden;
      }

      function setBag(topics, name) {
        bag = topics;
        bagName = name;
        bagNote = "";
        bagState.classList.remove("blocked");
        render();
      }

      function bagFailed(message) {
        bag = null;
        bagNote = message;
        bagState.classList.add("blocked");
        render();
      }

      // Directories of the bags read by path, newest first.
      function bagDirs() {
        try {
          const dirs = JSON.parse(SD.storage.get(BAG_DIRS_KEY) || "[]");
          return Array.isArray(dirs) ? dirs.filter(Boolean).map(String) : [];
        } catch (_error) {
          return [];
        }
      }

      function rememberBagDir(path) {
        const dir = model.bagCollection(path);
        if (!dir) return;
        SD.storage.set(
          BAG_DIRS_KEY,
          JSON.stringify(
            [dir, ...bagDirs().filter((d) => d !== dir)].slice(
              0,
              BAG_DIRS_KEPT,
            ),
          ),
        );
      }

      // The daemon finds the path of a file read here, under the typed path,
      // the remembered directories and its own.
      function locate(file) {
        if (!daemon.connected || !daemon.offers("replay.find")) return;
        const typed = bagPath.value.trim();
        daemon
          .rpc("replay.find", {
            name: file.name,
            size: file.size,
            directories: [...(typed ? [typed] : []), ...bagDirs()],
          })
          .then(
            (found) => {
              bagPath.value = found.path;
              readPath();
            },
            () => {},
          );
      }

      async function readFile(file) {
        try {
          setBag(
            await bagFile.readTopics(file, model.parseBagMetadata),
            file.name,
          );
        } catch (error) {
          bagFailed(error.message);
          return;
        }
        locate(file);
      }

      function readPath() {
        const path = bagPath.value.trim();
        daemon.rpc("replay.bag", { path, generate: true }).then(
          (info) => {
            rememberBagDir(info.path);
            setBag(
              new Map(
                info.topics.map((t) => [
                  t.name,
                  { type: t.type, count: t.count },
                ]),
              ),
              info.metadata === "generated"
                ? `${info.path} (metadata.yaml written)`
                : info.path,
            );
          },
          (error) => bagFailed(`${path}: ${error.message}`),
        );
      }

      // A dropped path is read through the daemon; dropped files give the
      // topics of their metadata, else of an MCAP file among them.
      async function readDrop(dataTransfer) {
        const { files, path } = await bagFile.dropped(dataTransfer);
        const file = bagFile.pick(files);
        if (path) {
          bagPath.value = path;
          if (daemon.connected && daemon.offers("replay.bag")) {
            readPath();
            return;
          }
        }
        if (file) await readFile(file);
        else if (path) render();
        else bagFailed("the drop holds no bag file, metadata.yaml or path");
      }

      async function loadMode() {
        const data = await SD.NamespaceTree.loadStructure(
          config.dataBase,
          store.get("mode"),
        );
        nodes = model.nodes(data);
        const wanted = (store.get("scope") || "").split(",").filter(Boolean);
        selected = new Set(
          nodes
            .filter((node) => wanted.some((p) => model.selects(node, p)))
            .map((node) => node.path),
        );
        store.set("scope", patterns().join(","));
        tree.setStructure(data);
        render();
      }

      bagLoad.addEventListener("click", () => bagInput.click());
      bagInput.addEventListener("change", () => {
        const file = bagInput.files[0];
        bagInput.value = "";
        if (file) readFile(file);
      });
      bagRead.addEventListener("click", readPath);
      // Drags entered minus drags left: children fire their own pairs.
      let dragDepth = 0;
      const dropping = (on) =>
        view.element.classList.toggle("replay-dropping", on);
      view.element.addEventListener("dragenter", (event) => {
        event.preventDefault();
        dragDepth += 1;
        dropping(true);
      });
      view.element.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
      });
      view.element.addEventListener("dragleave", () => {
        dragDepth = Math.max(0, dragDepth - 1);
        dropping(dragDepth > 0);
      });
      view.element.addEventListener("drop", (event) => {
        event.preventDefault();
        dragDepth = 0;
        dropping(false);
        readDrop(event.dataTransfer).catch((error) => bagFailed(error.message));
      });
      ["daemon.health", "daemon.connection"].forEach((topic) =>
        SD.on(topic, render),
      );
      bagPath.addEventListener("input", render);
      store.onChange("deploy", render);
      const reload = () =>
        loadMode().catch((error) => console.warn("replay:", error));
      store.onChange("mode", reload);
      reload();
      return {
        show() {
          visible = true;
          render();
        },
        hide() {
          visible = false;
          commands.close();
        },
      };
    },
  });
})();
