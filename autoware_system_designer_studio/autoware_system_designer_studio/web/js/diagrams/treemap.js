// Treemap: the run state of every node of the mode at a glance, one section
// per compute unit. A section holds rows of slots named by the first path
// segment of the top-level components, in fixed places: sensing,
// localization, perception and planning on top; api, adapi, system and
// control in the middle; node containers and every other segment below. A
// slot's title strip carries the colour of its component. Blocks nest as the
// design does, cut at the depth option, every node an equal area. A block's
// colour and state mark are its live state, a module's the severest within,
// followed by a busy-log mark and, on a node, its restart count; a block
// with no launched member within stays neutral and unmarked. A click
// selects a block or a unit, and search locates one. The `unitControls`
// option puts the host's controls of a unit in the section head, after its
// name.
(function () {
  const SD = window.SD;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const {
    UNIT_ID_PREFIX,
    G,
    TOKENS,
    BUSY_BARS,
    restartCount,
    unitSections,
    wrap,
    layout,
  } = SD.treemapLayout;

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

    // The layout follows the canvas size once the structure is loaded, the
    // first one included when the canvas had no size; one relayout per frame.
    onContainerResize() {
      if (this.frame || !this.structureRoot) return;
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
        const bar = svgElement("rect", {
          class: "tm-head",
          width: box.w,
          height: this.unitHead,
          rx: 3,
        });
        g.appendChild(bar);
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
        kind.style.fontSize = `${G.font.kind}px`;
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
        [rect, bar, title].forEach((part) =>
          part.addEventListener("click", select),
        );
        return g;
      }
      if (box.kind === "slot") {
        g.classList.add("tm-slot");
        const guide = box.guide;
        if (guide) {
          g.style.setProperty("--slot-color", guide.medium_color);
          g.style.setProperty("--slot-text", guide.text_color);
          g.style.setProperty("--slot-color-dark", guide.dark_medium_color);
          g.style.setProperty("--slot-text-dark", guide.dark_text_color);
        }
        g.appendChild(
          svgElement("rect", {
            class: "tm-slot-head",
            width: box.w,
            height: G.slotHead,
            rx: 3,
          }),
        );
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
      g.addEventListener("click", (event) => {
        event.stopPropagation();
        this.select(box);
      });
      return g;
    }

    // Name and marks of a block for the live state `info` (none when no member
    // within is launched): the state mark, then the busy-log mark, then a
    // node's restart count, then the name. An open module has them in its
    // label strip. A closed block tall enough has the marks along its top
    // and the name centred, wrapped over the lines its height holds; a
    // shorter one has them on one line, the name centred when it clears the
    // marks and after them otherwise. Marks go before the name: those that
    // fit are drawn in order, and the name where room is left. Redrawn only
    // when the marks change.
    _dress(g, box, info) {
      const count = info && box.kind === "node" ? restartCount(info) : 0;
      const busy = !!info?.busy;
      const key = info ? `${info.token}|${busy}|${count}` : "";
      if (box.dressKey === key) return;
      box.dressKey = key;
      g.querySelector(":scope > .tm-dress")?.remove();
      const dress = svgElement("g", { class: "tm-dress" });
      g.appendChild(dress);

      const size = G.font.block;
      const name = box.instance.name || "";
      const addName = (text, attributes) => {
        const label = svgElement("text", attributes);
        label.style.fontSize = `${size}px`;
        label.textContent = text;
        dress.appendChild(label);
      };
      // Marks in order: [width, draw(x, y)] with x the left edge, y the middle.
      const marks = [];
      if (info) {
        marks.push([
          G.blockMark,
          (x, y) =>
            dress.appendChild(
              SD.RunState.svgMark(
                info.token,
                x + G.blockMark / 2,
                y,
                G.blockMark,
              ),
            ),
        ]);
        if (busy)
          marks.push([
            G.busy,
            (x, y) =>
              BUSY_BARS.forEach(([dx, height]) =>
                dress.appendChild(
                  svgElement("rect", {
                    class: "tm-busy",
                    x: x + dx,
                    y: y + G.busy / 2 - height,
                    width: 2,
                    height,
                  }),
                ),
              ),
          ]);
        if (count) {
          const text = `\u21bb${count}`;
          marks.push([
            this._measure(text, G.font.restarts),
            (x, y) => {
              const label = svgElement("text", { class: "tm-restarts", x, y });
              label.style.fontSize = `${G.font.restarts}px`;
              label.textContent = text;
              dress.appendChild(label);
            },
          ]);
        }
      }
      const gap = 3;
      // The leading marks that fit in `room`, and their width.
      const fitMarks = (room) => {
        const fit = [];
        let width = 0;
        for (const mark of marks) {
          const next = width + (fit.length ? gap : 0) + mark[0];
          if (next > room) break;
          fit.push(mark);
          width = next;
        }
        return { fit, width };
      };
      const drawMarks = ({ fit }, x, y) =>
        fit.forEach(([width, draw]) => {
          draw(x, y);
          x += width + gap;
        });

      if (box.open) {
        const y = G.moduleHead / 2;
        const fitted = fitMarks(box.w - 2 * G.pad);
        drawMarks(fitted, G.pad, y);
        const lead = G.pad + Math.max(fitted.width, G.blockMark) + G.pad;
        const text = this._fit(name, box.w - lead - G.pad, size);
        if (text) addName(text, { x: lead, y });
        return;
      }
      const centre = { x: box.w / 2, y: box.h / 2 };
      const centred = { ...centre, "text-anchor": "middle" };
      if (box.h < size + 2) {
        const fitted = fitMarks(Math.min(box.w, box.h) - 2);
        drawMarks(fitted, (box.w - fitted.width) / 2, centre.y);
        return;
      }
      const row = marks.length ? 2 + G.blockMark + 1 : 0;
      if (box.h >= size + 2 * row) {
        const below = box.h - row - 2;
        const lines = wrap(
          name,
          box.w - 2 * G.pad,
          Math.max(1, Math.floor(below / G.blockLine)),
          (text) => this._measure(text, size),
        );
        // Centred on the block while the lines clear the marks, else on the
        // room below them.
        const clear = lines.length * G.blockLine <= box.h - 2 * row;
        const middle = clear ? centre.y : row + below / 2;
        lines.forEach((line, i) =>
          addName(line, {
            ...centred,
            y: middle + (i - (lines.length - 1) / 2) * G.blockLine,
          }),
        );
        drawMarks(fitMarks(box.w - 2 * gap), gap, 2 + G.blockMark / 2);
        return;
      }
      const fitted = fitMarks(box.w - 2 * gap);
      if (!fitted.fit.length) {
        const text = this._fit(name, box.w - 2 * G.pad, size);
        if (text) addName(text, centred);
        return;
      }
      const lead = gap + fitted.width + gap;
      const beside = this._fit(name, box.w - 2 * lead, size);
      const after = beside ? "" : this._fit(name, box.w - lead - gap, size);
      if (beside) addName(beside, centred);
      else if (after) addName(after, { x: lead, y: centre.y });
      drawMarks(
        fitted,
        beside || after ? gap : (box.w - fitted.width) / 2,
        centre.y,
      );
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
        slot.style.left = `${4 * G.pad + G.mark + width(".tm-title")}px`;
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

    clearSelection() {
      this.selectedId = null;
      this._markSelected();
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
        g.querySelector(":scope > .live-mark")?.remove();
        // A unit always carries a mark: idle until a member within reports.
        if (box.kind === "unit") {
          const mark = svgElement("g", { class: "live-mark" });
          mark.appendChild(
            SD.RunState.svgMark(
              info?.token || "idle",
              G.pad + G.mark / 2,
              this.unitHead / 2,
              G.mark,
            ),
          );
          g.appendChild(mark);
          return;
        }
        g.querySelector(":scope > title").textContent = this._title(box, info);
        this._dress(g, box, info);
      });
    }

    _title(box, info) {
      const path = box.instance.path || box.instance.name || "";
      if (!info) return path;
      const restarts = box.kind === "node" ? restartCount(info) : 0;
      return [
        path,
        info.dead ? `${info.label} · down` : info.label,
        info.busy,
        restarts ? `restarted ×${restarts}` : "",
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

  SD.diagrams.register("treemap", TreemapModule);
})();
