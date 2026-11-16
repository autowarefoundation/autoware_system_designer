// Replay activity: the scope of a deterministic replay chosen on the mode's
// namespace tree, beside its boundary: the inputs a bag feeds, each matched
// against the bag's topics and remapped where the bag names it otherwise, the
// outputs recorded, the service ports that leave the scope, and the replay
// configuration and command they give. With a daemon, the bag is read by its
// path and the replay runs as a job.
(function () {
  const SD = window.SD;
  const { element, copyButton, mainView } = SD.widgets;
  const { model, scopeTree, runPanel } = SD.activities.replay;

  const INPUT_STATUS = {
    fed: "in the bag",
    remapped: "remapped",
    accepted: "accepted unfed",
    type: "other type in the bag",
    unpublished: "no publisher, not in the bag",
    missing: "not in the bag",
    unknown: "no bag loaded",
  };
  const ACCEPT = "\u0000accept";

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

  function pathList(paths) {
    const cell = element("div", "replay-paths");
    paths.forEach((path) => cell.appendChild(element("span", "", path)));
    return cell;
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
      // Bag topics by recorded name; null until a metadata file is read.
      let bag = null;
      let bagName = "";
      // Recorded topic -> boundary input it is fed as.
      const remap = {};
      const accept = new Set();
      const recordedInternals = new Set();
      // Parameters of a replay of the shown choices, or what it still lacks.
      let request = { blocker: "Check nodes or modules to replay." };

      const patterns = () => model.patternsOf(nodes, selected);

      function setSelected(next) {
        selected = next;
        store.set("scope", patterns().join(","));
        tree.render();
        render();
      }

      const tree = scopeTree({
        nodes: () => nodes,
        selected: () => selected,
        toggle(path, on) {
          const next = new Set(selected);
          nodes
            .filter((node) => model.under(node.path, path))
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
      const body = element("div", "replay-body");
      view.body.appendChild(body);

      // -- bag -----------------------------------------------------------
      const bagBlock = block(
        "Bag",
        "The bag's metadata.yaml names its topics; the path goes into the command.",
      );
      const bagPath = element("input", "panel-search");
      bagPath.type = "text";
      bagPath.placeholder = "Path of the bag, as the replay reads it";
      bagPath.setAttribute("aria-label", "Bag path");
      const bagFile = document.createElement("input");
      bagFile.type = "file";
      bagFile.accept = ".yaml,.yml";
      bagFile.hidden = true;
      const bagLoad = element("button", "panel-button", "Load metadata.yaml");
      bagLoad.type = "button";
      const bagRead = element("button", "panel-button", "Read bag");
      bagRead.type = "button";
      bagRead.title =
        "Read the topics of the bag at this path through the daemon";
      const bagState = element("span", "replay-bag-state");
      const bagRow = element("div", "replay-bag-row");
      bagRow.append(bagPath, bagRead, bagLoad, bagFile, bagState);
      bagBlock.element.appendChild(bagRow);

      // -- boundary ------------------------------------------------------
      const inputBlock = block(
        "Boundary inputs",
        "Topics a node outside the scope publishes; the bag feeds them.",
      );
      const inputs = table(["Topic", "Published by", "Subscribed by", "Bag"]);
      inputBlock.element.appendChild(inputs.element);

      const outputBlock = block(
        "Outputs",
        "Boundary outputs are recorded; an internal topic is recorded once checked.",
      );
      const outputs = table(["Topic", "Published by", "Read by", "Record"]);
      outputBlock.element.appendChild(outputs.element);

      const serviceBlock = block(
        "Services leaving the scope",
        "Replay feeds no service: a client without its server stops the pre-flight unless accepted.",
      );
      const services = table(["Service", "Node", "Role", "Accept"]);
      serviceBlock.element.appendChild(services.element);

      // -- result --------------------------------------------------------
      const resultBlock = block("Configuration and command");
      const configCode = codeBlock(model.CONFIG_FILE);
      const checkCode = codeBlock("Pre-flight");
      const runCode = codeBlock("Replay");
      resultBlock.element.append(
        configCode.element,
        checkCode.element,
        runCode.element,
      );

      const panel = runPanel(daemon, () => request);
      body.append(
        bagBlock.element,
        inputBlock.element,
        outputBlock.element,
        serviceBlock.element,
        resultBlock.element,
        panel.element,
      );

      function bagControl(input) {
        if (!bag) {
          const box = checkbox(accept.has(input.topic), (on) => {
            if (on) accept.add(input.topic);
            else accept.delete(input.topic);
            render();
          });
          const label = element("label", "panel-check");
          label.append(box, "accept unfed");
          return label;
        }
        const select = document.createElement("select");
        const options = bag.has(input.topic) ? [] : [["", "not fed"]];
        input.candidates.forEach((name) =>
          options.push([
            name,
            name === input.topic
              ? `${name} (${bag.get(name).count})`
              : `${name} (${bag.get(name).count}), remapped`,
          ]),
        );
        if (bag.has(input.topic) && !input.candidates.includes(input.topic))
          options.push([input.topic, `${input.topic}, ${input.recordedType}`]);
        if (!bag.has(input.topic)) options.push([ACCEPT, "accept unfed"]);
        SD.widgets.setOptions(select, options);
        select.value =
          input.status === "accepted" ? ACCEPT : input.recordedName || "";
        select.addEventListener("change", () => {
          Object.keys(remap).forEach((recorded) => {
            if (remap[recorded] === input.topic) delete remap[recorded];
          });
          accept.delete(input.topic);
          if (select.value === ACCEPT) accept.add(input.topic);
          else if (select.value && select.value !== input.topic)
            remap[select.value] = input.topic;
          render();
        });
        return select;
      }

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
        };
      }

      function render() {
        const scope = model.selectScope(nodes, patterns());
        view.setEmpty(
          scope.nodes.length
            ? ""
            : "Check nodes or modules in the scope tree to replay them from a bag.",
        );
        bagRead.hidden = !daemon.health || !daemon.offers("replay.bag");
        bagRead.disabled = !daemon.connected || !bagPath.value.trim();
        if (!scope.nodes.length) {
          request = { blocker: "Check nodes or modules to replay." };
          panel.refresh();
          return;
        }
        view.setTitle(
          `Replay scope: ${scope.nodes.length} node${scope.nodes.length > 1 ? "s" : ""}` +
            (scope.containers.length
              ? `, ${scope.containers.length} container${scope.containers.length > 1 ? "s" : ""}`
              : ""),
        );
        const bound = model.boundary(nodes, scope.nodes);
        const kept = current(bound);
        const matched = model.matchInputs(
          bound,
          bag,
          kept.remap,
          new Set(kept.accept),
        );

        bagState.textContent = bag
          ? `${bagName}: ${bag.size} topics`
          : "no metadata loaded";

        inputs.clear();
        matched.forEach((input) => {
          const cell = element("div", "replay-bag-cell");
          cell.append(
            element(
              "span",
              `replay-status ${input.status}`,
              INPUT_STATUS[input.status],
            ),
            bagControl(input),
          );
          inputs.row(
            [
              topicCell(input.topic, input.type),
              pathList(input.publishers),
              pathList(input.subscribers),
              cell,
            ],
            `replay-input ${input.status}`,
          );
        });
        const open = matched.filter((input) =>
          ["missing", "type"].includes(input.status),
        ).length;
        inputBlock.count.textContent = bag
          ? `${matched.length}, ${open} unfed`
          : `${matched.length}`;
        inputBlock.count.classList.toggle("blocked", open > 0);
        inputs.element.hidden = !matched.length;

        outputs.clear();
        [
          ...bound.outputs.filter((output) => output.boundary),
          ...bound.outputs.filter((output) => !output.boundary),
        ].forEach((output) => {
          outputs.row(
            [
              topicCell(output.topic, output.type),
              pathList(output.publishers),
              output.outsideSubscribers.length || output.scopeSubscribers.length
                ? pathList([
                    ...output.outsideSubscribers,
                    ...output.scopeSubscribers.map((path) => `${path} (scope)`),
                  ])
                : "no subscriber",
              checkbox(
                output.boundary || recordedInternals.has(output.topic),
                (on) => {
                  if (on) recordedInternals.add(output.topic);
                  else recordedInternals.delete(output.topic);
                  render();
                },
                output.boundary,
              ),
            ],
            output.boundary ? "replay-output boundary" : "replay-output",
          );
        });
        outputBlock.count.textContent = `${bound.outputs.filter((o) => o.boundary).length} boundary, ${bound.outputs.length} in all`;
        outputs.element.hidden = !bound.outputs.length;

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
          : { blocker: "Give the path of the bag." };
        panel.refresh();
      }

      function setBag(topics, name) {
        bag = topics;
        bagName = name;
        bagState.classList.remove("blocked");
        render();
      }

      function bagFailed(name, message) {
        bag = null;
        bagState.classList.add("blocked");
        render();
        bagState.textContent = `${name}: ${message}`;
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
            .filter((node) => wanted.some((p) => model.under(node.path, p)))
            .map((node) => node.path),
        );
        store.set("scope", patterns().join(","));
        tree.setStructure(data);
        render();
      }

      bagLoad.addEventListener("click", () => bagFile.click());
      bagFile.addEventListener("change", async () => {
        const file = bagFile.files[0];
        bagFile.value = "";
        if (!file) return;
        try {
          setBag(model.parseBagMetadata(await file.text()), file.name);
        } catch (error) {
          bagFailed(file.name, error.message);
        }
      });
      bagRead.addEventListener("click", () => {
        const path = bagPath.value.trim();
        daemon.rpc("replay.bag", { path }).then(
          (info) =>
            setBag(
              new Map(
                info.topics.map((t) => [
                  t.name,
                  { type: t.type, count: t.count },
                ]),
              ),
              info.path,
            ),
          (error) => bagFailed(path, error.message),
        );
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
    },
  });
})();
