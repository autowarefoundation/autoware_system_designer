// Replay activity: the scope of a deterministic replay chosen on the mode's
// namespace tree, drawn as a graph inside its boundary: the inputs a bag
// feeds, each joined to the bag topic that feeds it and remapped where the bag
// names it otherwise, and the outputs recorded. The view stacks three zones,
// each folded or expanded and sized by the dividers between them: the diagram;
// the inputs, outputs and service ports that leave the scope, stacked in one
// scrolled page with the rosbag in its head; and the run, whose popup holds the replay configuration and
// command the choices give. The bag is files or a path dropped on the view, files
// opened, or a path typed. With a daemon, the bag is read by its path and the
// replay runs as a job.
(function () {
  const SD = window.SD;
  const { element, mainView } = SD.widgets;
  const {
    model,
    lists,
    scopeTree,
    scopeDiagram,
    bagSource,
    commandsPopup,
    runPanel,
    zoneStack,
    setups,
  } = SD.activities.replay;

  const { ACCEPT, INPUT_NOTE } = model;
  // Preference: height share and fold of each zone of the view.
  const ZONES_KEY = "replay.zones";
  // Weights are heights in pixels, the scale a divider drag stores.
  const ZONES = [
    { id: "diagram", title: "Diagram", weight: 440 },
    { id: "boundary", title: "Inputs and outputs", weight: 260 },
    { id: "run", title: "Run", weight: 300 },
  ];
  const NO_SCOPE = "Check nodes or modules to replay.";

  const plural = (count, noun) => `${count} ${noun}${count === 1 ? "" : "s"}`;

  // The choices of a replay beside its scope: the source of each boundary
  // input, the names accepted unfed and the outputs recorded.
  function replayChoices() {
    // Recorded topic -> boundary input it is fed as.
    const remap = {};
    const accept = new Set();
    const recordedInternals = new Set();
    // Boundary outputs left unrecorded.
    const skippedOutputs = new Set();
    // Recorded outputs of a loaded setup not applied yet, by mode.
    let pendingRecorded = null;

    // Source of a boundary input: a recorded topic, remapped where the names
    // differ, nothing, or ACCEPT to accept it unfed.
    function setSource(topic, value) {
      Object.keys(remap).forEach((recorded) => {
        if (remap[recorded] === topic) delete remap[recorded];
      });
      accept.delete(topic);
      if (value === ACCEPT) accept.add(topic);
      else if (value && value !== topic) remap[value] = topic;
    }

    return {
      setSource,
      accepted: (name) => accept.has(name),
      setAccepted(name, on) {
        if (on) accept.add(name);
        else accept.delete(name);
      },
      // An internal topic is recorded once chosen, a boundary output unless skipped.
      setRecorded(output, on) {
        if (output.boundary) {
          if (on) skippedOutputs.delete(output.topic);
          else skippedOutputs.add(output.topic);
        } else if (on) recordedInternals.add(output.topic);
        else recordedInternals.delete(output.topic);
      },
      // Remaps, accepted names and outputs of topics `bound` still holds,
      // as keys of the replay configuration; `bag` is the bag's topics or null.
      current(bound, bag) {
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
      },
      // Takes the configuration, input sources and recorded outputs of a
      // setup; the outputs wait for the boundary of `mode`.
      load(setup, mode) {
        const saved = setup.config;
        Object.keys(remap).forEach((key) => delete remap[key]);
        Object.assign(remap, saved.remap || {});
        [
          [accept, saved.accept],
          [recordedInternals, saved.outputs],
          [skippedOutputs, saved.skip_outputs],
        ].forEach(([set, items]) => {
          set.clear();
          (items || []).forEach((item) => set.add(item));
        });
        Object.entries(setup.inputs || {}).forEach(([topic, source]) =>
          setSource(topic, source === null ? ACCEPT : source),
        );
        pendingRecorded = setup.recorded
          ? { mode, topics: setup.recorded }
          : null;
      },
      // Recorded outputs of a loaded setup, applied once the nodes of its mode
      // give the boundary: a listed output is recorded, another one is not.
      applyRecorded(bound, mode) {
        if (!pendingRecorded || pendingRecorded.mode !== mode) return;
        const wanted = new Set(pendingRecorded.topics);
        pendingRecorded = null;
        skippedOutputs.clear();
        recordedInternals.clear();
        bound.outputs.forEach((output) => {
          if (output.boundary && !wanted.has(output.topic))
            skippedOutputs.add(output.topic);
          else if (!output.boundary && wanted.has(output.topic))
            recordedInternals.add(output.topic);
        });
      },
    };
  }

  // Table of the service ports that leave the scope, unaccepted clients
  // first; `toggle(name, on)` takes a change of an accept box.
  function serviceList(accepted, toggle) {
    const block = lists.block(
      "Services leaving the scope",
      "Replay feeds no service: a client without its server stops the pre-flight unless accepted.",
    );
    const services = lists.table(["Service", "Node", "Role", "Accept"]);
    block.element.appendChild(services.element);
    return {
      element: block.element,
      set(bound) {
        services.clear();
        const rank = (edge) =>
          (accepted(edge.name) ? 2 : 0) + (edge.role === "client" ? 0 : 1);
        const leaving = bound.services
          .filter((edge) => !edge.scopePeers.length)
          .map((edge, index) => ({ edge, index }))
          .sort((a, b) => rank(a.edge) - rank(b.edge) || a.index - b.index)
          .map(({ edge }) => edge);
        leaving.forEach((edge) => {
          services.row(
            [
              lists.topicCell(edge.name, edge.type),
              edge.node,
              edge.role,
              lists.checkbox(accepted(edge.name), (on) =>
                toggle(edge.name, on),
              ),
            ],
            `replay-service ${edge.role}${accepted(edge.name) ? " accepted" : ""}`,
          );
        });
        const open = leaving.filter((edge) => !accepted(edge.name)).length;
        block.count.textContent =
          `${leaving.length}` + (open ? `, ${open} unaccepted` : "");
        block.element.hidden = !leaving.length;
      },
    };
  }

  // Rows of the output list: the boundary outputs, then the internal topics.
  function outputItems(bound, kept) {
    const skipped = new Set(kept.skip_outputs);
    return [
      ...bound.outputs.filter((output) => output.boundary),
      ...bound.outputs.filter((output) => !output.boundary),
    ].map((output) => ({
      topic: output.topic,
      note: output.boundary ? "boundary" : "internal",
      level: output.boundary ? "boundary" : "internal",
      checked: output.boundary
        ? !skipped.has(output.topic)
        : kept.outputs.includes(output.topic),
    }));
  }

  // The scope graph with its titles and summary, as the diagram shows it.
  function drawing({
    nodes,
    scope,
    bound,
    matched,
    kept,
    bag,
    bagName,
    members,
  }) {
    const skipped = new Set(kept.skip_outputs);
    const ambient = matched.filter(
      (input) => input.status === "ambient",
    ).length;
    const unfed = matched.filter((input) =>
      ["missing", "type"].includes(input.status),
    ).length;
    const remapped = matched.filter(
      (input) => input.status === "remapped",
    ).length;
    const recorded =
      bound.outputs.filter((o) => o.boundary).length -
      skipped.size +
      kept.outputs.length;
    return {
      ...model.graph(
        nodes,
        scope,
        bound,
        matched,
        new Set(kept.outputs),
        skipped,
      ),
      bagTitle: bag ? `Bag: ${bagName}` : "Bag",
      bagNote: bag
        ? "no topic of the bag feeds the scope"
        : "no bag read: drop one on the view",
      scopeTitle: `Replay boundary: ${members}`,
      summary:
        plural(matched.length - ambient, "input") +
        (bag ? `, ${unfed} unfed` : "") +
        (ambient ? `, ${ambient} ambient` : "") +
        (remapped ? `, ${remapped} remapped` : "") +
        `, ${recorded} of ${bound.outputs.length} outputs recorded`,
      blocked: unfed > 0,
    };
  }

  // Bag topic each matched input is fed from; null for one accepted unfed.
  function inputSources(matched, kept) {
    const accepted = new Set(kept.accept);
    const sourceOf = Object.fromEntries(
      Object.entries(kept.remap).map(([recorded, fed]) => [fed, recorded]),
    );
    const inputs = {};
    matched.forEach((input) => {
      if (input.status === "ambient") return;
      if (accepted.has(input.topic)) inputs[input.topic] = null;
      else if (input.recordedName) inputs[input.topic] = input.recordedName;
      else if (sourceOf[input.topic])
        inputs[input.topic] = sourceOf[input.topic];
    });
    return inputs;
  }

  function mount({ side, main }, { config, store, daemon }) {
    store.define("scope", "");
    store.syncUrl();

    let nodes = [];
    // Mode the shown nodes are of.
    let nodesMode = null;
    let selected = new Set();
    const choices = replayChoices();
    // Topic of the input selected in the list or the diagram, or null.
    let selectedInput = null;
    // Boundary of the scope shown.
    let shownBound = { inputs: [], outputs: [] };
    // Parameters of a replay of the shown choices, or what it still lacks.
    let request = { blocker: NO_SCOPE };
    // Scope and choices of the shown replay as keys of the replay
    // configuration, and the arguments of its command.
    let shown = { configuration: {}, args: {} };
    // The activity's slots are visible from mount until another activity takes over.
    let visible = true;

    const patterns = () => model.patternsOf(nodes, selected);

    function setSelected(next) {
      selected = next;
      store.set("scope", patterns().join(","));
      tree.render();
      render();
    }

    function setRecorded(topic, on) {
      const output = shownBound.outputs.find((o) => o.topic === topic);
      if (!output) return;
      choices.setRecorded(output, on);
      render();
    }

    function chooseSource(input, value) {
      choices.setSource(input.topic, value);
      render();
    }

    function selectInput(topic) {
      selectedInput = topic;
      render();
    }

    // -- side: saved setups above the scope selector ----------------------
    const setupSlot = element("div");
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
    const sideBlocks = SD.widgets.sideSplit(side);
    sideBlocks.top.append(setupSlot, tree.element);
    sideBlocks.scroll.appendChild(tree.list);

    // -- main: the diagram, the boundary and the run, a zone each ---------
    const view = mainView({ title: "Replay scope" });
    main.appendChild(view.element);
    const stack = zoneStack(ZONES_KEY, ZONES);
    const {
      diagram: diagramZone,
      boundary: boundaryZone,
      run: runZone,
    } = stack.zones;
    view.body.appendChild(stack.element);

    const bag = bagSource({
      daemon,
      dropTarget: view.element,
      changed: render,
    });
    const sourcesOf = (input) => model.inputChoices(input, bag.topics());
    const diagram = scopeDiagram({
      choices: sourcesOf,
      choose: chooseSource,
      record: setRecorded,
      select: selectInput,
    });
    diagramZone.body.appendChild(diagram.element);

    const dropGuide = element("span", "replay-drop-guide", "Drop a rosbag");
    dropGuide.title = "A rosbag dropped anywhere on the view is read";
    boundaryZone.actions.appendChild(dropGuide);
    boundaryZone.body.appendChild(bag.element);
    const selectorBlock = element("div", "replay-block");
    const inputList = lists.inputSelector(
      sourcesOf,
      chooseSource,
      (inputs) => {
        inputs.forEach((input) => choices.setSource(input.topic, ACCEPT));
        render();
      },
      selectInput,
    );
    const outputSelector = lists.topicSelector("Outputs", setRecorded);
    const selectors = element("div", "replay-selectors");
    selectors.append(inputList.element, outputSelector.element);
    selectorBlock.appendChild(selectors);
    const services = serviceList(choices.accepted, (name, on) => {
      choices.setAccepted(name, on);
      render();
    });
    const boundaryLists = element("div", "replay-boundary-lists");
    boundaryLists.append(selectorBlock, services.element);
    boundaryZone.body.appendChild(boundaryLists);

    const panel = runPanel(daemon, () => request, bag.span);
    const commands = commandsPopup(() => ({
      configuration: configuration(),
      args: shown.args,
    }));
    runZone.actions.appendChild(commands.button);
    // Without the daemon's replay, the popup's commands run the replay.
    const runNote = element(
      "p",
      "panel-note",
      "Running a replay from here needs the studio daemon; without it, run the commands of Configuration and command in a shell.",
    );
    runZone.body.append(runNote, panel.element);
    const setupBar = setups.setupBar(daemon, capture, applySetup);
    setupSlot.replaceWith(setupBar.element);
    // Parts a scope fills.
    const scopeBlocks = [selectorBlock, services.element, commands.button];

    // The replay configuration of the shown choices and run options.
    function configuration() {
      const { leadIn, driver } = panel.getOptions();
      return {
        ...shown.configuration,
        ...(leadIn === null ? {} : { lead_in: leadIn }),
        ...(Object.keys(driver).length ? { driver } : {}),
      };
    }

    function refreshRun() {
      panel.refresh();
      runNote.hidden = !panel.element.hidden;
      setupBar.render();
    }

    // The scope, its boundary and the kept choices, with the inputs matched
    // against the bag.
    function resolve() {
      const scope = model.selectScope(nodes, patterns());
      if (!scope.nodes.length) return { scope };
      const bound = model.boundary(nodes, scope.nodes);
      choices.applyRecorded(bound, nodesMode);
      const kept = choices.current(bound, bag.topics());
      const matched = model.matchInputs(
        bound,
        bag.topics(),
        kept.remap,
        new Set(kept.accept),
      );
      return { scope, bound, kept, matched };
    }

    function render() {
      bag.render();
      const { scope, bound, kept, matched } = resolve();
      scopeBlocks.forEach((node) => {
        node.hidden = !scope.nodes.length;
      });
      if (!scope.nodes.length) {
        commands.close();
        if (visible) diagram.show(null);
        view.setTitle("Replay scope");
        request = { blocker: NO_SCOPE };
        refreshRun();
        return;
      }
      const members =
        plural(scope.nodes.length, "node") +
        (scope.containers.length
          ? `, ${plural(scope.containers.length, "container")}`
          : "");
      view.setTitle(`Replay scope: ${members}`);
      shownBound = bound;
      if (!matched.some((input) => input.topic === selectedInput))
        selectedInput = null;
      inputList.set(matched, INPUT_NOTE, selectedInput);
      outputSelector.set(outputItems(bound, kept));
      if (visible) {
        diagram.show(
          drawing({
            nodes,
            scope,
            bound,
            matched,
            kept,
            bag: bag.topics(),
            bagName: bag.name(),
            members,
          }),
        );
        diagram.select(selectedInput);
      }
      services.set(bound);

      const args = {
        structure: model.structurePath(store.get("mode")),
        bag: bag.path(),
        deploy: store.get("deploy") || "",
      };
      shown = { configuration: { scope: patterns(), ...kept }, args };
      const blocker = bag.blocker();
      request = blocker
        ? { blocker }
        : {
            params: {
              system: config.systemId,
              mode: store.get("mode"),
              bag: args.bag,
              deploy: args.deploy,
              ...shown.configuration,
            },
          };
      refreshRun();
    }

    // The shown replay as a saved setup, or what it lacks.
    function capture() {
      const { scope, bound, kept, matched } = resolve();
      if (!scope.nodes.length)
        return { blocker: "Check nodes or modules to save a setup." };
      const skipped = new Set(kept.skip_outputs);
      const recorded = [
        ...bound.outputs
          .filter((o) => o.boundary && !skipped.has(o.topic))
          .map((o) => o.topic),
        ...kept.outputs,
      ].sort();
      const { run } = panel.getOptions();
      return {
        setup: {
          system: config.systemId,
          mode: store.get("mode"),
          ...(store.get("deploy") ? { deploy: store.get("deploy") } : {}),
          ...(bag.path() ? { bag: bag.path() } : {}),
          config: configuration(),
          inputs: inputSources(matched, kept),
          recorded,
          run,
        },
      };
    }

    // Shows a saved setup; gives the parts this page could not take, or "".
    function applySetup(setup) {
      const missed = [];
      const saved = setup.config;
      const known = config.availableModes.includes(setup.mode);
      choices.load(setup, known ? setup.mode : store.get("mode"));
      panel.setOptions(setup.run || {}, saved.lead_in, saved.driver);
      if (setup.system && setup.system !== config.systemId)
        missed.push(`saved for ${setup.system}`);
      if (setup.deploy) {
        if (config.deployNames.includes(setup.deploy))
          store.set("deploy", setup.deploy);
        else missed.push(`no deploy ${setup.deploy}`);
      }
      store.set("scope", saved.scope.join(","));
      if (setup.mode && !known) missed.push(`no mode ${setup.mode}`);
      if (known && setup.mode !== store.get("mode"))
        store.set("mode", setup.mode);
      else selectScopePaths();
      bag.show(setup.bag);
      return missed.join(", ");
    }

    async function loadMode() {
      const mode = store.get("mode");
      const data = await SD.NamespaceTree.loadStructure(config.dataBase, mode);
      nodes = model.nodes(data);
      nodesMode = mode;
      tree.setStructure(data);
      selectScopePaths();
    }

    // Selects the nodes the stored scope paths name.
    function selectScopePaths() {
      const wanted = (store.get("scope") || "").split(",").filter(Boolean);
      selected = new Set(
        nodes
          .filter((node) => wanted.some((p) => model.selects(node, p)))
          .map((node) => node.path),
      );
      store.set("scope", patterns().join(","));
      tree.render();
      render();
    }

    ["daemon.health", "daemon.connection"].forEach((topic) =>
      SD.on(topic, render),
    );
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
  }

  SD.workbench.registerActivity({
    id: "replay",
    title: "Replay",
    group: "Test",
    order: 25,
    daemon: false,
    subsystem: false,
    inspector: false,
    icon: "M16 4v12M13 10l-9-6v12z",
    status: (store) => {
      const scope =
        store.get("scope") ??
        new URLSearchParams(window.location.search).get("scope");
      const patterns = (scope || "").split(",").filter(Boolean);
      return [
        patterns.length
          ? `scope: ${plural(patterns.length, "path")}`
          : "no scope",
      ];
    },
    mount,
  });
})();
