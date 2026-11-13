// Treemap layout: the sections of a mode's compute units, the slots of their
// top-level components in fixed rows, and the nested blocks of every node at
// an equal area, as boxes in canvas coordinates.
(function () {
  const SD = window.SD;

  // Slot of the top-level node containers.
  const CONTAINERS = "containers";
  // Slots of each row in place; the last row takes the containers and then
  // every other segment in design order.
  const ROWS = [
    ["sensing", "localization", "perception", "planning"],
    ["api", "adapi", "system", "control"],
    [CONTAINERS],
  ];
  // Section of the nodes declared on no compute unit.
  const UNASSIGNED = "";
  const UNIT_ID_PREFIX = "__compute_unit__";

  // Geometry in canvas pixels.
  const G = {
    pad: 4,
    gap: 3,
    unitHead: 34,
    slotHead: 16,
    moduleHead: 14,
    minUnit: 140,
    // Section area per node, and the least height of a row of slots, at a
    // section's own height.
    nodeArea: 6400,
    unitRow: 64,
    minRow: 40,
    minSlot: 24,
    // Smallest inside still laid out with children.
    minOpen: 10,
    font: { unit: 15, kind: 12, slot: 11, block: 10, restarts: 9 },
    // Line pitch of a block's wrapped name.
    blockLine: 12,
    // State mark of a unit, and of a block.
    mark: 11,
    blockMark: 7,
    // Busy-log mark: its width and height.
    busy: 8,
  };

  const TOKENS = ["running", "ok", "warning", "failed", "idle"];
  // Rising bars of the busy-log mark: [x offset, height].
  const BUSY_BARS = [
    [0, 3],
    [3, 5.5],
    [6, 8],
  ];

  // Times the member of a live state was started again, respawns among them.
  function restartCount(info) {
    return info.restarts ? info.restarts.restarts : 0;
  }

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
  // top-level components pruned to its nodes; a declared unit without one
  // stays as an empty section, any other is left out.
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
          declared: !!unit,
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
      .filter((section) => section.children.length || section.declared);
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

  // `text` in lines of at most `maxWidth` by `measure(text)`, at most
  // `maxLines`: broken after an underscore, slash or dot where one fits the
  // line, anywhere otherwise; the last line ends in an ellipsis when the text
  // runs past it. Empty when a line holds less than a few characters.
  function wrap(text, maxWidth, maxLines, measure) {
    if (!text || maxWidth <= 0 || maxLines < 1) return [];
    const fits = (part) => measure(part) <= maxWidth;
    if (fits(text)) return [text];
    if (!fits(`${text.slice(0, 3)}…`)) return [];
    const lines = [];
    let rest = text;
    while (rest && lines.length < maxLines) {
      if (fits(rest)) {
        lines.push(rest);
        rest = "";
        break;
      }
      let end = 1;
      while (end < rest.length && fits(rest.slice(0, end + 1))) end += 1;
      const at = Math.max(
        ...["_", "/", "."].map((mark) => rest.lastIndexOf(mark, end - 1)),
      );
      if (at >= 2) end = at + 1;
      lines.push(rest.slice(0, end));
      rest = rest.slice(end);
    }
    if (rest) {
      let last = lines.pop() + rest;
      while (last.length > 3 && !fits(`${last}…`)) last = last.slice(0, -1);
      lines.push(`${last}…`);
    }
    return lines;
  }

  function inset(rect, d) {
    return {
      x: rect.x + d,
      y: rect.y + d,
      w: Math.max(0, rect.w - 2 * d),
      h: Math.max(0, rect.h - 2 * d),
    };
  }

  // Own height of a filled section at `width`: its head over a body of
  // nodeArea per node, the body at least unitRow per row of slots and the
  // whole at least minUnit.
  function sectionHeight(section, width, unitHead = G.unitHead) {
    const rows = slotRows(section.children).length;
    const body = Math.max(
      rows * G.unitRow + (rows - 1) * G.gap,
      (section.nodes * G.nodeArea) / Math.max(1, width - 2 * G.pad),
    );
    return Math.max(G.minUnit, unitHead + body + G.pad);
  }

  // Boxes of the sections laid over `width` x `height`: an empty section is
  // its head alone, the others are their own height, so the layout may run
  // past `height`; height left over is shared among them in proportion to
  // their nodes. `unitHead` is the height of a section's head. A box carries
  // its kind (unit, slot, module or node), rect, and the section or instance
  // it draws; a module box is `open` when its children are laid inside it.
  function layout(
    sections,
    width,
    height,
    depthLimit = Infinity,
    unitHead = G.unitHead,
  ) {
    const boxes = [];
    const filled = sections.filter((section) => section.children.length);
    const shares = filled.map((section) =>
      sectionHeight(section, width, unitHead),
    );
    const free =
      height -
      (sections.length - filled.length) * unitHead -
      shares.reduce((sum, h) => sum + h, 0);
    const nodes = filled.reduce((sum, section) => sum + section.nodes, 0);
    if (free > 0)
      filled.forEach((section, i) => {
        shares[i] += nodes
          ? (free * section.nodes) / nodes
          : free / filled.length;
      });
    let y = 0;
    sections.forEach((section, index) => {
      const h = section.children.length
        ? shares[filled.indexOf(section)]
        : unitHead;
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
    return { height: free > 0 ? height : y, boxes };
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
          guide: slot.components[0].vis_guide || null,
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

  SD.treemapLayout = {
    CONTAINERS,
    ROWS,
    UNIT_ID_PREFIX,
    G,
    TOKENS,
    BUSY_BARS,
    restartCount,
    slotOf,
    nodeCount,
    unitSections,
    slotRows,
    proportional,
    squarify,
    wrap,
    sectionHeight,
    layout,
  };
})();
