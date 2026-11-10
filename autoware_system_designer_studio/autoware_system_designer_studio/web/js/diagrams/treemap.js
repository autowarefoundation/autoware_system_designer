// Treemap: the run state of every node of the mode at a glance, one section
// per compute unit. A section holds rows of slots named by the first path
// segment of the top-level components, in fixed places: sensing,
// localization, perception and planning on top; api, system and control in
// the middle; node containers and every other segment below. Blocks nest as
// the design does, cut at the depth option, every node an equal area. A
// block's colour is its live state, a module's the severest within; a block
// with no launched member within stays neutral. A click selects a block or a
// unit, and search locates one. The `unitControls` option puts the host's
// controls of a unit in the section head, after its name.
(function () {
  const SD = window.SD;
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Slot of the top-level node containers.
  const CONTAINERS = "containers";
  // Slots of each row in place; the last row takes the containers and then
  // every other segment in design order.
  const ROWS = [
    ["sensing", "localization", "perception", "planning"],
    ["api", "system", "control"],
    [CONTAINERS],
  ];
  // Section of the nodes declared on no compute unit.
  const UNASSIGNED = "";
  const UNIT_ID_PREFIX = "__compute_unit__";

  // Geometry in canvas pixels.
  const G = {
    pad: 4,
    gap: 3,
    unitHead: 22,
    slotHead: 16,
    moduleHead: 14,
    minUnit: 140,
    minRow: 40,
    minSlot: 24,
    // Smallest inside still laid out with children.
    minOpen: 10,
    font: { unit: 13, slot: 11, block: 10 },
    mark: 9,
  };

  const TOKENS = ["running", "ok", "warning", "failed", "idle"];

  function isNode(instance) {
    return instance.entity_type === "node";
  }

  // Slot of a top-level component: the containers slot for a node container,
  // else the first segment of its path, its name when the path has none.
  function slotOf(component) {
    if (
      isNode(component) &&
      component.launcher?.launch_state === "node_container"
    )
      return CONTAINERS;
    const segment = String(component.path || "")
      .split("/")
      .find(Boolean);
    return segment || component.name || "";
  }

  // Nodes within an instance, itself when a node.
  function nodeCount(instance) {
    if (isNode(instance)) return 1;
    return (instance.children || []).reduce(
      (sum, child) => sum + nodeCount(child),
      0,
    );
  }

  // Block weight: the nodes within, one for an empty module.
  function weightOf(instance) {
    return Math.max(1, nodeCount(instance));
  }

  function unitOf(instance) {
    return instance.compute_unit || UNASSIGNED;
  }

  // Copy of `instance` holding the nodes of `unit` only, null when it holds
  // none; a module without any node stays on its own unit.
  function prune(instance, unit) {
    const own = unitOf(instance) === unit;
    if (isNode(instance)) return own ? instance : null;
    const kept = (instance.children || [])
      .filter((child) => child.unique_id)
      .map((child) => prune(child, unit))
      .filter(Boolean);
    if (kept.length) return { ...instance, children: kept };
    return own && !nodeCount(instance) ? { ...instance, children: [] } : null;
  }

  // Sections in order: the declared units in declaration order, then the
  // units only the design names, the unassigned nodes last. Each carries the
  // top-level components pruned to its nodes; a unit without one is left out.
  function unitSections(root) {
    const declared = (root.compute_units || []).filter((unit) => unit?.name);
    const names = declared.map((unit) => unit.name);
    const seen = new Set(names);
    const collect = (instance) => {
      if (isNode(instance) || !nodeCount(instance)) {
        const unit = unitOf(instance);
        if (!seen.has(unit)) {
          seen.add(unit);
          names.push(unit);
        }
        return;
      }
      (instance.children || []).forEach(collect);
    };
    (root.children || []).forEach(collect);
    const ordered = names.filter((name) => name !== UNASSIGNED);
    if (seen.has(UNASSIGNED)) ordered.push(UNASSIGNED);
    return ordered
      .map((name) => {
        const unit = declared.find((u) => u.name === name);
        const children = (root.children || [])
          .filter((child) => child.unique_id)
          .map((child) => prune(child, name))
          .filter(Boolean);
        return {
          name,
          kind: unit
            ? unit.kind || "onboard"
            : name === UNASSIGNED
              ? ""
              : "undeclared",
          description: unit?.description || "",
          children,
          nodes: children.reduce((sum, child) => sum + nodeCount(child), 0),
        };
      })
      .filter((section) => section.children.length);
  }

  // Rows of slots of a section's components: each row's slots in their fixed
  // order, the last row's other segments after the containers in design
  // order; empty slots and rows are left out.
  function slotRows(components) {
    const bySlot = new Map();
    components.forEach((component) => {
      const slot = slotOf(component);
      if (!bySlot.has(slot)) bySlot.set(slot, []);
      bySlot.get(slot).push(component);
    });
    const order = ROWS.map((row) => [...row]);
    bySlot.forEach((_, slot) => {
      if (!ROWS.some((row) => row.includes(slot)))
        order[ROWS.length - 1].push(slot);
    });
    return order
      .map((row) =>
        row
          .filter((slot) => bySlot.has(slot))
          .map((slot) => ({
            slot,
            components: bySlot.get(slot),
            weight: bySlot
              .get(slot)
              .reduce((sum, component) => sum + weightOf(component), 0),
          })),
      )
      .filter((row) => row.length);
  }

  // Lengths of `weights` along `length`: each at least `min`, the rest in
  // proportion. When the minimums alone exceed the length, every length is
  // the minimum with `overflow`, else an equal share.
  function proportional(weights, length, min, overflow = false) {
    const n = weights.length;
    if (!n) return [];
    if (n * min >= length)
      return weights.map(() => (overflow ? min : length / n));
    const total = weights.reduce((sum, w) => sum + w, 0);
    const free = length - n * min;
    if (!total) return weights.map(() => length / n);
    return weights.map((w) => min + (free * w) / total);
  }

  // Squarified treemap of `items` ({weight}) over `rect`: one cell per item,
  // heaviest first, in rows along the shorter side. Areas are proportional to
  // the weights and tile the rect.
  function squarify(items, rect) {
    const total = items.reduce((sum, item) => sum + item.weight, 0);
    if (!total || rect.w <= 0 || rect.h <= 0) return [];
    const scale = (rect.w * rect.h) / total;
    const sorted = items
      .map((item, index) => ({ item, index, area: item.weight * scale }))
      .sort((a, b) => b.area - a.area || a.index - b.index);
    const cells = [];
    let { x, y, w, h } = rect;
    let i = 0;
    while (i < sorted.length) {
      const across = w < h;
      const side = across ? w : h;
      const row = [];
      let rowArea = 0;
      let best = Infinity;
      while (i < sorted.length) {
        const area = sorted[i].area;
        const candidateArea = rowArea + area;
        const areas = row.map((entry) => entry.area).concat(area);
        const worst = Math.max(
          (side * side * Math.max(...areas)) / (candidateArea * candidateArea),
          (candidateArea * candidateArea) / (side * side * Math.min(...areas)),
        );
        if (worst > best) break;
        row.push(sorted[i]);
        rowArea = candidateArea;
        best = worst;
        i += 1;
      }
      const thickness = side > 0 ? rowArea / side : 0;
      let offset = 0;
      row.forEach((entry) => {
        const length = thickness > 0 ? entry.area / thickness : 0;
        cells.push(
          across
            ? { item: entry.item, x: x + offset, y, w: length, h: thickness }
            : { item: entry.item, x, y: y + offset, w: thickness, h: length },
        );
        offset += length;
      });
      if (across) {
        y += thickness;
        h -= thickness;
      } else {
        x += thickness;
        w -= thickness;
      }
    }
    return cells;
  }

  function inset(rect, d) {
    return {
      x: rect.x + d,
      y: rect.y + d,
      w: Math.max(0, rect.w - 2 * d),
      h: Math.max(0, rect.h - 2 * d),
    };
  }

  // Boxes of the sections laid over `width` x `height`: sections take the
  // height in proportion to their nodes, each at least minUnit tall, so the
  // layout may run past `height`; `unitHead` is the height of a section's
  // head. A box carries its kind (unit, slot, module
  // or node), rect, and the section or instance it draws; a module box is
  // `open` when its children are laid inside it.
  function layout(
    sections,
    width,
    height,
    depthLimit = Infinity,
    unitHead = G.unitHead,
  ) {
    const boxes = [];
    const heights = proportional(
      sections.map((section) => section.nodes),
      height,
      G.minUnit,
      true,
    );
    let y = 0;
    sections.forEach((section, index) => {
      const h = heights[index];
      boxes.push({
        kind: "unit",
        id: `${UNIT_ID_PREFIX}${section.name}`,
        section: index,
        x: 0,
        y,
        w: width,
        h,
      });
      layoutRows(
        slotRows(section.children),
        {
          x: G.pad,
          y: y + unitHead,
          w: width - 2 * G.pad,
          h: h - unitHead - G.pad,
        },
        index,
        depthLimit,
        boxes,
      );
      y += h;
    });
    return { height: y, boxes };
  }

  function layoutRows(rows, rect, section, depthLimit, boxes) {
    const heights = proportional(
      rows.map((row) => row.reduce((sum, slot) => sum + slot.weight, 0)),
      rect.h - G.gap * (rows.length - 1),
      G.minRow,
    );
    let y = rect.y;
    rows.forEach((row, i) => {
      const widths = proportional(
        row.map((slot) => slot.weight),
        rect.w - G.gap * (row.length - 1),
        G.minSlot,
      );
      let x = rect.x;
      row.forEach((slot, j) => {
        boxes.push({
          kind: "slot",
          slot: slot.slot,
          section,
          x,
          y,
          w: widths[j],
          h: heights[i],
        });
        const inner = {
          x: x + G.pad,
          y: y + G.slotHead,
          w: widths[j] - 2 * G.pad,
          h: heights[i] - G.slotHead - G.pad,
        };
        squarify(
          slot.components.map((c) => ({ weight: weightOf(c), instance: c })),
          inner,
        ).forEach((cell) =>
          layoutInstance(
            cell.item.instance,
            inset(cell, G.gap / 2),
            1,
            section,
            depthLimit,
            boxes,
          ),
        );
        x += widths[j] + G.gap;
      });
      y += heights[i] + G.gap;
    });
  }

  function layoutInstance(instance, rect, depth, section, depthLimit, boxes) {
    if (rect.w <= 0 || rect.h <= 0) return;
    const children = isNode(instance) ? [] : instance.children || [];
    let inner = null;
    if (children.length && depth < depthLimit) {
      const candidate = {
        x: rect.x + G.pad,
        y: rect.y + G.moduleHead,
        w: rect.w - 2 * G.pad,
        h: rect.h - G.moduleHead - G.pad,
      };
      if (candidate.w >= G.minOpen && candidate.h >= G.minOpen)
        inner = candidate;
    }
    boxes.push({
      kind: isNode(instance) ? "node" : "module",
      id: String(instance.unique_id),
      instance,
      section,
      depth,
      open: !!inner,
      ...rect,
    });
    if (!inner) return;
    squarify(
      children.map((c) => ({ weight: weightOf(c), instance: c })),
      inner,
    ).forEach((cell) =>
      layoutInstance(
        cell.item.instance,
        inset(cell, G.gap / 2),
        depth + 1,
        section,
        depthLimit,
        boxes,
      ),
    );
  }

  function svgElement(tag, attributes = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attributes).forEach(([name, value]) =>
      node.setAttribute(name, String(value)),
    );
    return node;
  }

  class TreemapModule extends DiagramBase {
    constructor(container, options = {}) {
      super(container, options);
      container.classList.add("treemap-canvas");
      // Deepest design level drawn: top-level components are level 1, and a
      // module at the limit is drawn as one closed block.
      this.depthLimit = Number.isInteger(options.depth)
        ? Math.max(1, options.depth)
        : Infinity;
      // unitControls(name) -> element or null: the host's controls of a unit,
      // laid over the section head; unitHead is the head's height.
      this.unitControls =
        typeof options.unitControls === "function"
          ? options.unitControls
          : null;
      this.unitHead = options.unitHead || G.unitHead;
      // Unit name -> the slot over its section head holding the host's controls.
      this.controlSlots = new Map();
      this.structureRoot = null;
      this.sections = [];
      // Instance and parent id of every entity in the structure, drawn or not.
      this.instanceById = new Map();
      this.parentOf = new Map();
      // Node path -> live member state, drawn now and after every layout.
      this.liveStates = null;
      this.svg = null;
      this.boxes = [];
      this.selectedId = null;
      this.frame = null;
      // Settles once the first layout is drawn.
      this.ready = this.init();
    }

    async init() {
      try {
        const data = await this.loadModeData(this.options.mode);
        this.structureRoot = data;
        this._index(data);
        this.sections = unitSections(data);
        this.render();
      } catch (error) {
        console.error("Error loading treemap:", error);
        this.showError(`Error loading treemap: ${error.message}`);
      }
    }

    _index(root) {
      const walk = (instance, parentId) => {
        if (!instance?.unique_id) return;
        const id = String(instance.unique_id);
        this.instanceById.set(id, instance);
        if (parentId) this.parentOf.set(id, parentId);
        (instance.children || []).forEach((child) => walk(child, id));
      };
      walk(root, null);
    }

    // The layout follows the canvas width; one relayout per frame.
    onContainerResize() {
      if (this.frame || !this.svg) return;
      this.frame = requestAnimationFrame(() => {
        this.frame = null;
        this.render();
      });
    }

    render() {
      const width = this.container.clientWidth;
      const height = this.container.clientHeight;
      if (!width || !height) return;
      if (!this.sections.length) {
        this.showError("No node of this mode is placed on a compute unit.");
        return;
      }
      const laid = layout(
        this.sections,
        width,
        height,
        this.depthLimit,
        this.unitHead,
      );
      this.boxes = laid.boxes;
      const svg = svgElement("svg", {
        class: "treemap",
        width,
        height: Math.max(height, laid.height),
      });
      laid.boxes.forEach((box) => svg.appendChild(this._draw(box)));
      this.container.replaceChildren(svg);
      this.svg = svg;
      this._layControlSlots();
      this.applyLiveStates();
      this._markSelected();
    }

    _draw(box) {
      const g = svgElement("g", { transform: `translate(${box.x},${box.y})` });
      const rect = svgElement("rect", {
        class: "tm-rect",
        width: box.w,
        height: box.h,
        rx: 3,
      });
      g.appendChild(rect);
      if (box.kind === "unit") {
        const section = this.sections[box.section];
        g.classList.add("tm-unit");
        g.dataset.id = box.id;
        rect.setAttribute("pointer-events", "stroke");
        const title = svgElement("text", {
          class: "tm-title",
          x: G.pad + G.mark + G.pad,
          y: this.unitHead / 2,
        });
        title.style.fontSize = `${G.font.unit}px`;
        title.textContent = section.name || "unassigned";
        g.appendChild(title);
        const kind = svgElement("text", {
          class: "tm-kind",
          x: box.w - G.pad,
          y: this.unitHead / 2,
          "text-anchor": "end",
        });
        kind.style.fontSize = `${G.font.slot}px`;
        kind.textContent = section.kind
          ? `${section.kind} · ${section.nodes} nodes`
          : `${section.nodes} nodes`;
        g.appendChild(kind);
        g.appendChild(svgElement("title")).textContent =
          section.description || section.name || "unassigned";
        const select = (event) => {
          event.stopPropagation();
          this.select(box);
        };
        rect.addEventListener("click", select);
        title.addEventListener("click", select);
        return g;
      }
      if (box.kind === "slot") {
        g.classList.add("tm-slot");
        const text = svgElement("text", { x: G.pad, y: G.slotHead / 2 });
        text.style.fontSize = `${G.font.slot}px`;
        text.textContent = this._fit(box.slot, box.w - 2 * G.pad, G.font.slot);
        g.appendChild(text);
        return g;
      }
      g.classList.add("tm-block");
      g.dataset.kind = box.kind;
      g.dataset.id = box.id;
      g.appendChild(svgElement("title"));
      this._label(g, box);
      g.addEventListener("click", (event) => {
        event.stopPropagation();
        this.select(box);
      });
      return g;
    }

    // Block name in the label strip of an open module, centred in a closed
    // block when it fits.
    _label(g, box) {
      const name = box.instance.name || "";
      const size = G.font.block;
      if (box.open) {
        const text = this._fit(name, box.w - 2 * G.pad, size);
        if (!text) return;
        const label = svgElement("text", { x: G.pad, y: G.moduleHead / 2 });
        label.style.fontSize = `${size}px`;
        label.textContent = text;
        g.appendChild(label);
        return;
      }
      if (box.h < size + 2) return;
      const text = this._fit(name, box.w - 2 * G.pad, size);
      if (!text) return;
      const label = svgElement("text", {
        x: box.w / 2,
        y: box.h / 2,
        "text-anchor": "middle",
      });
      label.style.fontSize = `${size}px`;
      label.textContent = text;
      g.appendChild(label);
    }

    // `text` within `maxWidth`, shortened with an ellipsis down to a few
    // characters; empty when even that does not fit.
    _fit(text, maxWidth, fontSize) {
      if (maxWidth <= 0 || !text) return "";
      if (this._measure(text, fontSize) <= maxWidth) return text;
      for (let length = text.length - 1; length >= 3; length -= 1) {
        const short = `${text.slice(0, length)}…`;
        if (this._measure(short, fontSize) <= maxWidth) return short;
      }
      return "";
    }

    _measure(text, fontSize) {
      if (!this._measureCtx) {
        this._measureCtx = document.createElement("canvas").getContext("2d");
        this._measureFamily =
          getComputedStyle(this.container).fontFamily || "sans-serif";
      }
      if (!this._measureCtx) return text.length * fontSize * 0.6;
      this._measureCtx.font = `${fontSize}px ${this._measureFamily}`;
      return this._measureCtx.measureText(text).width;
    }

    // ── Unit controls ───────────────────────────────────────────────────────

    // One slot per section head, from the end of the unit name to the start
    // of the kind text, in a layer over the drawing.
    _layControlSlots() {
      this.controlSlots = new Map();
      if (!this.unitControls) return;
      const layer = document.createElement("div");
      layer.className = "tm-controls";
      this.boxes.forEach((box, index) => {
        if (box.kind !== "unit") return;
        const g = this.svg.childNodes[index];
        const width = (selector) =>
          g.querySelector(selector)?.getComputedTextLength?.() || 0;
        const slot = document.createElement("div");
        slot.className = "tm-unit-controls";
        slot.style.left = `${3 * G.pad + G.mark + width(".tm-title")}px`;
        slot.style.right = `${2 * G.pad + width(".tm-kind")}px`;
        slot.style.top = `${box.y}px`;
        slot.style.height = `${this.unitHead}px`;
        layer.appendChild(slot);
        this.controlSlots.set(this.sections[box.section].name, slot);
      });
      this.container.appendChild(layer);
      this.renderUnitControls();
    }

    // Refills every slot from the host; called by the host when its controls changed.
    renderUnitControls() {
      this.controlSlots.forEach((slot, name) => {
        const controls = this.unitControls(name);
        slot.replaceChildren(...(controls ? [controls] : []));
      });
    }

    // Names of the units drawn as sections.
    unitNames() {
      return this.sections.map((section) => section.name);
    }

    // ── Selection ───────────────────────────────────────────────────────────

    select(box) {
      this.selectedId = box.id;
      this._markSelected();
      if (box.kind === "unit") {
        this.updateInfoPanel(this._unitInfo(box), "Compute unit");
        return;
      }
      const instance = this.instanceById.get(box.id) || box.instance;
      this.updateInfoPanel(
        instance,
        box.kind === "node" ? "Node" : "Module",
        instance,
      );
    }

    _unitInfo(box) {
      const section = this.sections[box.section];
      return {
        unique_id: box.id,
        name: section.name || "unassigned",
        namespace: section.kind,
        entity_type: "compute_unit",
        kind: section.kind,
        description: section.description,
        nodes: section.nodes,
        members: section.children.map((child) => child.name),
      };
    }

    _markSelected() {
      if (!this.svg) return;
      this.svg
        .querySelectorAll(".selected")
        .forEach((node) => node.classList.remove("selected"));
      this._drawnOf(this.selectedId).forEach((node) =>
        node.classList.add("selected"),
      );
    }

    // Elements drawing the entity `id`: a module split across units has one per section.
    _drawnOf(id) {
      if (!this.svg || !id) return [];
      return Array.from(
        this.svg.querySelectorAll(`[data-id="${CSS.escape(String(id))}"]`),
      );
    }

    // Selects the entity drawn with `id` as a click would and scrolls it into
    // view. An entity inside a closed block is stood in for by that block;
    // false when this diagram draws neither.
    focusEntity(id) {
      let drawnId = String(id);
      while (drawnId && !this._drawnOf(drawnId).length)
        drawnId = this.parentOf.get(drawnId);
      const [drawn] = this._drawnOf(drawnId);
      if (!drawn) return false;
      drawn.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      if (typeof drawn.scrollIntoView === "function")
        drawn.scrollIntoView({ block: "nearest", inline: "nearest" });
      return true;
    }

    // ── Live state ──────────────────────────────────────────────────────────

    setLiveStates(states) {
      this.liveStates = states;
      this.applyLiveStates();
    }

    // Live state per block of each section, rolled up over the section's
    // pruned tree so a module split across units shows its own unit's nodes.
    _liveStateBySection() {
      const states = this.liveStates || new Map();
      return this.sections.map((section) =>
        SD.RunState.rollup(
          {
            unique_id: `${UNIT_ID_PREFIX}${section.name}`,
            entity_type: "compute_unit",
            children: section.children,
          },
          states,
        ),
      );
    }

    applyLiveStates() {
      if (!this.svg || !this.structureRoot) return;
      const bySection = this._liveStateBySection();
      this.boxes.forEach((box, index) => {
        if (box.kind === "slot") return;
        const g = this.svg.childNodes[index];
        const info = bySection[box.section].get(box.id);
        TOKENS.forEach((token) => g.classList.remove(`state-${token}`));
        if (info) g.classList.add(`state-${info.token}`);
        g.classList.toggle("live-dead", !!info?.dead);
        g.querySelector(":scope > .live-mark")?.remove();
        if (box.kind === "unit") {
          if (info) {
            const mark = svgElement("g", { class: "live-mark" });
            mark.appendChild(
              SD.RunState.svgMark(
                info.token,
                G.pad + G.mark / 2,
                this.unitHead / 2,
                G.mark,
              ),
            );
            g.appendChild(mark);
          }
          return;
        }
        g.querySelector(":scope > title").textContent = this._title(box, info);
      });
    }

    _title(box, info) {
      const path = box.instance.path || box.instance.name || "";
      if (!info) return path;
      const restarts = info.restarts
        ? info.restarts.restarts + info.restarts.respawns
        : 0;
      return [
        path,
        info.dead ? `${info.label} · down` : info.label,
        restarts ? `↻${restarts}` : "",
      ]
        .filter(Boolean)
        .join(" · ");
    }

    destroy() {
      if (this.frame) cancelAnimationFrame(this.frame);
      this.frame = null;
      this.svg = null;
      super.destroy();
    }
  }

  TreemapModule.ROWS = ROWS;
  TreemapModule.CONTAINERS = CONTAINERS;
  TreemapModule.GEOMETRY = G;
  TreemapModule.slotOf = slotOf;
  TreemapModule.nodeCount = nodeCount;
  TreemapModule.unitSections = unitSections;
  TreemapModule.slotRows = slotRows;
  TreemapModule.proportional = proportional;
  TreemapModule.squarify = squarify;
  TreemapModule.layout = layout;

  SD.diagrams.register("treemap", TreemapModule);
})();
