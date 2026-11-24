// Launch: the treemap of the mode sized by resource use. Sections, slots and
// nesting are the Control map's; a node's area is its process's CPU, memory
// or GPU memory over the feed's window, and its colour its live state. A
// composable node has no process of its own: its container's block holds the
// use of all its nodes, unless the container split is on, which sizes each
// composable node by its callbacks' CPU and leaves the container the rest
// (executor, DDS and the nodes' own threads). The split is off unless
// switched on, since it counts inside the nodes; without it the composable
// nodes have no block. Nodes under 0.2 % of their unit's total gather in one
// low block per module; a node keeps its block until it falls under 0.1 %.
// The blocks are laid out again once per window, at most every
// RELAYOUT_MAX_S, and at once when the resource or the window changes or a
// unit has its first values; colours and the detail panel follow every
// reading. A click selects a node as the store's `entity`, shared with the
// other views, and lists the clicked block's nodes in the detail panel.
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
  // Share of a unit's total under which a node goes to its module's low
  // block, the share under which a node holding a block of its own does, and
  // the least share of a low block.
  const LOW_SHARE = 0.002;
  const LOW_KEEP_SHARE = 0.001;
  const LOW_AREA = 0.002;
  const APPLY_DELAY_MS = 80;
  const LOW_PREFIX = "__low__";

  function isComposable(instance) {
    return instance.launcher?.launch_state === "composable_node";
  }

  // `section` with the nodes valued under the cut moved into one low node
  // per module; `valueOf(node)` gives a node's value, a node path in `shown`
  // holds its block down to the keep cut, and nodes for which `held(node)`
  // holds are left out, their use counted in another block. The paths of the
  // nodes keeping a block are added to `kept`. A module left without nodes
  // is dropped.
  function groupLow(
    section,
    valueOf,
    { held = () => false, shown = new Set(), kept = new Set() } = {},
  ) {
    let total = 0;
    const visitTotal = (instance) => {
      if (instance.entity_type === "node") {
        if (!held(instance)) total += valueOf(instance);
      } else (instance.children || []).forEach(visitTotal);
    };
    section.children.forEach(visitTotal);
    const cut = total * LOW_SHARE;
    const keepCut = total * LOW_KEEP_SHARE;
    const ownsBlock = (node, value) =>
      value > 0 && (value >= cut || (shown.has(node.path) && value >= keepCut));
    // `children` with the low nodes gathered in one block after the others.
    const group = (children, owner) => {
      const low = [];
      const out = [];
      children.forEach((child) => {
        if (child.entity_type !== "node") {
          const module = group(child.children || [], child);
          if (module.length) out.push({ ...child, children: module });
          return;
        }
        if (held(child)) return;
        const value = valueOf(child);
        if (ownsBlock(child, value)) {
          kept.add(child.path);
          out.push(child);
        } else low.push(child);
      });
      if (low.length) {
        const sum = low.reduce((acc, node) => acc + valueOf(node), 0);
        out.push({
          entity_type: "node",
          low: true,
          name: "low",
          unique_id: `${LOW_PREFIX}${section.name}/${owner?.unique_id ?? ""}`,
          compute_unit: section.name,
          owner: owner?.path || owner?.name || section.name,
          members: low,
          weight: total > 0 ? Math.max(sum, total * LOW_AREA) : 1,
          title: `${low.length} node${low.length === 1 ? "" : "s"} under ${LOW_SHARE * 100} % of the unit, or without readings`,
        });
      }
      return out;
    };
    return { ...section, children: group(section.children, null) };
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
      shown = new Set();
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
      shown = new Set();
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
    const body = element("div", "resource-body");
    const host = element("div", "diagram-container");
    const detail = element("div", "resource-detail");
    body.append(host, detail);
    container.appendChild(body);

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
    // Paths of the nodes holding a block of their own at the last layout, and
    // the low blocks of that layout by id.
    let shown = new Set();
    let lowBlocks = new Map();
    // Id of the low block shown in the detail panel; null for the store's entity.
    let selectedLow = null;

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

    // Value of the member at `path` over the window now; a composable node's
    // is its callbacks' CPU while split.
    function liveValue(path, composable = false) {
      if (!composable) return valueOfUsage(resources.usage(path));
      return splitting() ? resources.nodeCpu(path) || 0 : 0;
    }

    // One row of the detail list: a node path with its value, selecting the
    // node on click.
    function detailRow(path, value) {
      const row = element("li", "resource-detail-row");
      const name = element("span", "resource-detail-path", path);
      name.title = path;
      row.append(name, element("span", "resource-detail-value", value));
      const entity = entities.find((e) => e.member_path === path);
      if (entity)
        row.addEventListener("click", () => store.set("entity", entity.key));
      return row;
    }

    function detailList(heading, rows) {
      const section = element("div", "resource-detail-section");
      section.append(element("div", "resource-detail-heading", heading));
      const list = element("ul", "resource-detail-list");
      list.append(...rows);
      section.append(list);
      return section;
    }

    // The detail panel: the clicked block's use and the nodes it holds.
    function renderDetail() {
      if (hidden) return;
      const parts = [];
      const low = selectedLow && lowBlocks.get(selectedLow);
      const key = store.get("entity");
      const entity = !low && key && entities.find((e) => e.key === key);
      if (low) {
        parts.push(
          element("div", "resource-detail-title", `low · ${low.owner}`),
          element("div", "resource-detail-line", low.title),
          detailList(
            `${low.members.length} node${low.members.length === 1 ? "" : "s"}`,
            low.members
              .map((node) => [
                node.path,
                liveValue(node.path, isComposable(node)),
              ])
              .sort((a, b) => b[1] - a[1])
              .map(([path, value]) =>
                detailRow(path, value > 0 ? valueLabel(value) : "–"),
              ),
          ),
        );
      } else if (entity?.entity_type === "node") {
        parts.push(element("div", "resource-detail-title", entity.member_path));
        if (entity.container_path) {
          const cpu = splitting()
            ? resources.nodeCpu(entity.member_path)
            : null;
          parts.push(
            element(
              "div",
              "resource-detail-line",
              cpu !== null
                ? `callbacks ${cpuLabel(cpu)} of a core`
                : "no process of its own: its use counts in its container",
            ),
            detailList("container", [
              detailRow(
                entity.container_path,
                valueLabel(liveValue(entity.container_path)),
              ),
            ]),
          );
        } else {
          usageLines(resources.usage(entity.member_path)).forEach((line) =>
            parts.push(element("div", "resource-detail-line", line)),
          );
          const inside = composablesOf().get(entity.member_path) || [];
          if (inside.length)
            parts.push(
              detailList(
                `holds ${inside.length} node${inside.length === 1 ? "" : "s"}`,
                inside.map((node) => {
                  const cpu = splitting()
                    ? resources.nodeCpu(node.member_path)
                    : null;
                  return detailRow(
                    node.member_path,
                    cpu !== null ? cpuLabel(cpu) : "",
                  );
                }),
              ),
            );
        }
      } else
        parts.push(
          element(
            "div",
            "resource-detail-line",
            "Click a block to list its nodes.",
          ),
        );
      detail.replaceChildren(...parts);
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
      renderDetail();
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
      shown = new Set();
      lowBlocks = new Map();
      selectedLow = null;
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
          weigh: (node) => (node.low ? node.weight : valueOf(node)),
          transform(sections) {
            const kept = new Set();
            const held = (node) => isComposable(node) && !splitting();
            const grouped = sections.map((section) =>
              groupLow(section, valueOf, { held, shown, kept }),
            );
            shown = kept;
            lowBlocks = new Map();
            const visit = (instance) => {
              if (instance.low) lowBlocks.set(instance.unique_id, instance);
              (instance.children || []).forEach(visit);
            };
            grouped.forEach(visit);
            return grouped;
          },
          nameOf(instance) {
            if (instance.low) return `low · ${instance.members.length}`;
            const value = instance.entity_type === "node" && valueOf(instance);
            return value
              ? `${instance.name} · ${valueLabel(value)}`
              : instance.name || "";
          },
          onInfoUpdate(_data, _type, entity) {
            selectedLow = entity?.low ? entity.unique_id : null;
            store.set(
              "entity",
              entity?.path && !entity.low
                ? SD.NamespaceTree.entityKey(entity)
                : "",
            );
            renderDetail();
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
      else if (!selectedLow) module?.clearSelection();
    }

    SD.on("launch.member", scheduleApply);
    SD.on("launch.sessions", scheduleApply);
    SD.on("launch.resources", scheduleApply);
    store.onChange("entity", () => {
      if (store.get("entity")) selectedLow = null;
      if (hidden) return;
      followEntity();
      renderDetail();
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
  launch.resourceMapRules = {
    groupLow,
    LOW_SHARE,
    LOW_KEEP_SHARE,
    LOW_AREA,
  };
})();
