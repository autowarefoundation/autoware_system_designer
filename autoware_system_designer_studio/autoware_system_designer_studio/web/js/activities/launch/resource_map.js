// Launch: the treemap of the mode sized by resource use. Sections, slots and
// nesting are the Control map's; a node's area is its process's CPU, memory
// or GPU memory over the feed's window, and its colour its live state. A
// composable node has no process of its own: its container's block holds the
// use of all its nodes, unless the container split is on, which sizes each
// composable node by its callbacks' CPU and leaves the container the rest
// (executor, DDS and the nodes' own threads). The split is off unless
// switched on, since it counts inside the nodes. Nodes under 1 % of their unit's total gather in one
// idle block per unit. The blocks are laid out again once per window, at most
// every RELAYOUT_MAX_S, and at once when the resource or the window changes
// or a unit has its first values;
// colours and tooltips follow every reading. A click selects a node as the
// store's `entity`, shared with the other views.
(function () {
  const SD = window.SD;
  const { element, segmented } = SD.widgets;
  const launch = (SD.activities.launch ||= {});

  const METRICS = [
    ["cpu", "CPU"],
    ["mem", "Memory"],
    ["gpu", "GPU memory"],
  ];
  const METRIC_KEY = "launch.resources.metric";
  const RELAYOUT_MAX_S = 10;
  // Share of a unit's total under which a node goes to the idle block, and
  // the share of the idle block.
  const IDLE_SHARE = 0.01;
  const IDLE_AREA = 0.03;
  const APPLY_DELAY_MS = 80;
  const IDLE_PREFIX = "__idle__";

  function isComposable(instance) {
    return instance.launcher?.launch_state === "composable_node";
  }

  // `section` with the nodes valued under the cut moved into one idle node;
  // `valueOf(node)` gives a node's value. A module left without nodes is
  // dropped.
  function groupIdle(section, valueOf) {
    let total = 0;
    const visitTotal = (instance) => {
      if (instance.entity_type === "node") total += valueOf(instance);
      else (instance.children || []).forEach(visitTotal);
    };
    section.children.forEach(visitTotal);
    const cut = total * IDLE_SHARE;
    const idle = [];
    const keep = (instance) => {
      if (instance.entity_type === "node") {
        const value = valueOf(instance);
        if (value > 0 && value >= cut) return instance;
        idle.push(instance);
        return null;
      }
      const children = (instance.children || []).map(keep).filter(Boolean);
      return children.length ? { ...instance, children } : null;
    };
    const children = section.children.map(keep).filter(Boolean);
    if (idle.length)
      children.push({
        entity_type: "node",
        idle: true,
        name: "idle",
        unique_id: `${IDLE_PREFIX}${section.name}`,
        compute_unit: section.name,
        members: idle,
        weight: total > 0 ? total * IDLE_AREA : 1,
        title: `${idle.length} node${idle.length === 1 ? "" : "s"} under ${Math.round(IDLE_SHARE * 100)} % of the unit, or without a process of their own`,
      });
    return { ...section, children };
  }

  function resourceMap(container, config, store, resources) {
    const { bytesLabel, cpuLabel, usageLines } = launch.resourceUsage;
    let metric = SD.storage.get(METRIC_KEY);
    if (!METRICS.some(([value]) => value === metric)) metric = "cpu";

    // -- toolbar
    const toolbar = element("div", "resource-toolbar");
    const metricSwitch = segmented(METRICS, (value) => {
      metric = value;
      SD.storage.set(METRIC_KEY, value);
      metricSwitch.mark(value);
      relayout();
    });
    metricSwitch.mark(metric);
    const windowSwitch = segmented(
      resources.WINDOWS.map((s) => [String(s), `${s} s`]),
      (value) => {
        resources.setWindow(Number(value));
        windowSwitch.mark(value);
        relayout();
      },
    );
    windowSwitch.mark(String(resources.window));
    const splitWrapper = element("label", "topic-toggle");
    splitWrapper.title =
      "Size each composable node by its callbacks' CPU time (counted inside " +
      "the nodes: two clock reads per callback; off when the view is left)";
    const splitInput = element("input");
    splitInput.type = "checkbox";
    splitInput.addEventListener("change", () => {
      resources.setSplit(splitInput.checked);
      relayout();
    });
    splitWrapper.append(splitInput, element("span", "", "Split containers"));
    const note = element("span", "resource-note");
    toolbar.append(
      element("span", "resource-toolbar-label", "Size by"),
      metricSwitch.element,
      element("span", "resource-toolbar-label", "over"),
      windowSwitch.element,
      splitWrapper,
      note,
    );
    container.appendChild(toolbar);
    const host = element("div", "diagram-container");
    container.appendChild(host);

    let module = null;
    let entities = [];
    let entitiesMode = null;
    let hidden = true;
    let stale = true;
    let loadSeq = 0;
    let applyTimer = null;
    let relayoutTimer = null;
    // Node path -> value, captured at each layout, and the units holding one.
    let values = new Map();
    let laidUnits = new Set();
    let laidSplit = false;

    function valueOfUsage(usage) {
      if (!usage) return 0;
      if (metric === "cpu") return usage.cpu || 0;
      if (metric === "mem") return usage.rss_kb || 0;
      return usage.gpu_mem_kb || 0;
    }

    function valueLabel(value) {
      return metric === "cpu" ? cpuLabel(value) : bytesLabel(value);
    }

    // Units holding a value now.
    function valuedUnits() {
      const units = new Set();
      entities.forEach((entity) => {
        if (entity.entity_type !== "node" || entity.container_path) return;
        const usage = resources.usage(entity.member_path);
        if (valueOfUsage(usage) > 0) units.add(usage.unit);
      });
      return units;
    }

    // Whether composable nodes are sized by their callbacks now.
    const splitting = () => metric === "cpu" && resources.splitting;

    // Composable entities by their container's member path.
    function composablesOf() {
      const held = new Map();
      entities.forEach((entity) => {
        if (entity.entity_type !== "node" || !entity.container_path) return;
        if (!held.has(entity.container_path))
          held.set(entity.container_path, []);
        held.get(entity.container_path).push(entity);
      });
      return held;
    }

    function capture() {
      values = new Map();
      laidUnits = new Set();
      const split = splitting();
      laidSplit = split;
      const composables = split ? composablesOf() : new Map();
      entities.forEach((entity) => {
        if (entity.entity_type !== "node" || entity.container_path) return;
        const usage = resources.usage(entity.member_path);
        let value = valueOfUsage(usage);
        const inside = composables.get(entity.member_path) || [];
        if (split && inside.length) {
          let callbacks = 0;
          inside.forEach((node) => {
            const cpu = resources.nodeCpu(node.member_path) || 0;
            callbacks += cpu;
            if (cpu > 0) values.set(node.member_path, cpu);
          });
          value = Math.max(value - callbacks, 0);
        }
        if (usage) laidUnits.add(usage.unit);
        if (value > 0) values.set(entity.member_path, value);
      });
    }

    const valueOf = (node) =>
      isComposable(node) && !splitting() ? 0 : values.get(node.path) || 0;

    function liveStates() {
      const states = launch.memberStates(
        entities,
        config.systemId,
        entitiesMode,
      );
      const containers = splitting() ? composablesOf() : new Map();
      entities.forEach((entity) => {
        const state = states.get(entity.member_path);
        if (!state) return;
        if (entity.container_path) {
          const cpu = splitting()
            ? resources.nodeCpu(entity.member_path)
            : null;
          state.usage =
            cpu !== null
              ? [`callbacks ${cpuLabel(cpu)} of a core`]
              : [
                  "no process of its own: its use counts in its container's block",
                ];
          return;
        }
        state.usage = usageLines(resources.usage(entity.member_path));
        if (
          containers.has(entity.member_path) &&
          values.has(entity.member_path)
        )
          state.usage.unshift(
            `other ${cpuLabel(values.get(entity.member_path))} of a core outside its nodes' callbacks`,
          );
      });
      return states;
    }

    function renderNote() {
      const units = resources.units();
      const parts = [];
      if (!units.length)
        parts.push("no readings: launch the mode to see its use");
      else if (
        metric === "gpu" &&
        !units.some((unit) => resources.host(unit)?.gpu.per_process)
      )
        parts.push(
          units.map((unit) => resources.host(unit)?.gpu.note).find(Boolean) ||
            "no per-process GPU use",
        );
      note.textContent = parts.join(" · ");
    }

    function applyStates() {
      applyTimer = null;
      if (!module) return;
      module.setLiveStates(liveStates());
      module.setUnitNotes(
        resources
          .units()
          .map((unit) => [
            unit,
            launch.resourceUsage.hostLabel(resources.host(unit)),
          ]),
      );
      splitInput.checked = resources.split;
      renderNote();
      if (
        splitting() !== laidSplit ||
        [...valuedUnits()].some((unit) => !laidUnits.has(unit))
      )
        relayout();
    }

    function scheduleApply() {
      if (applyTimer || hidden) return;
      applyTimer = setTimeout(applyStates, APPLY_DELAY_MS);
    }

    // Lays the blocks out from the current window, and again after the next one.
    function relayout() {
      clearTimeout(relayoutTimer);
      relayoutTimer = null;
      if (hidden || !module) return;
      capture();
      module.render();
      applyStates();
      relayoutTimer = setTimeout(
        relayout,
        Math.min(resources.window, RELAYOUT_MAX_S) * 1000,
      );
    }

    async function load() {
      const seq = ++loadSeq;
      stale = false;
      laidUnits = new Set();
      module?.destroy();
      module = null;
      const canvas = element("div", "diagram-canvas");
      host.replaceChildren(canvas);
      try {
        const mode = store.get("mode");
        const loaded = await launch.modeEntities(config.dataBase, mode);
        if (seq !== loadSeq) return;
        entities = loaded;
        entitiesMode = mode;
        const created = new (SD.diagrams.get("treemap"))(canvas, {
          mode,
          dataBase: config.dataBase,
          weigh: (node) => (node.idle ? node.weight : valueOf(node)),
          transform: (sections) =>
            sections.map((section) => groupIdle(section, valueOf)),
          nameOf(instance) {
            if (instance.idle) return `idle · ${instance.members.length}`;
            const value = instance.entity_type === "node" && valueOf(instance);
            return value
              ? `${instance.name} · ${valueLabel(value)}`
              : instance.name || "";
          },
          onInfoUpdate(_data, _type, entity) {
            store.set(
              "entity",
              entity?.path && !entity.idle
                ? SD.NamespaceTree.entityKey(entity)
                : "",
            );
          },
        });
        await created.ready;
        if (seq !== loadSeq) {
          created.destroy();
          return;
        }
        module = created;
        relayout();
      } catch (error) {
        console.error("resource map:", error);
        host.replaceChildren(
          element(
            "div",
            "diagram-error",
            `Error loading the map: ${error.message}`,
          ),
        );
      }
    }

    function followEntity() {
      const key = store.get("entity");
      const entity = key && entities.find((e) => e.key === key);
      if (entity) module?.focusEntity(String(entity.unique_id));
      else module?.clearSelection();
    }

    SD.on("launch.member", scheduleApply);
    SD.on("launch.sessions", scheduleApply);
    SD.on("launch.resources", scheduleApply);
    store.onChange("entity", () => {
      if (!hidden) followEntity();
    });
    store.onChange("mode", () => {
      if (hidden) stale = true;
      else load();
    });

    return {
      show() {
        hidden = false;
        resources.want("resources", true);
        if (stale) load();
        else relayout();
      },
      hide() {
        hidden = true;
        clearTimeout(relayoutTimer);
        relayoutTimer = null;
        resources.want("resources", false);
      },
    };
  }

  launch.resourceMap = resourceMap;
  launch.resourceMapRules = { groupIdle, IDLE_SHARE, IDLE_AREA };
})();
