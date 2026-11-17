// Replay: the view as zones stacked top to bottom, each under a head whose
// whole width, outside the controls it holds, folds or expands it. A divider between two zones moves height between the
// nearest expanded zones above and below it. Heights and folds persist per zone.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const replay = (SD.activities.replay ||= {});

  // Least height of an expanded zone, its head included.
  const MIN_HEIGHT = 90;
  const CHEVRON = "M5 8l5 5 5-5";
  // Parts of a head that keep their clicks: its controls other than the
  // toggle, and anything marked data-zone-keep, its actions among them.
  const HEAD_CONTROLS =
    "input, select, textarea, a, label, button:not(.replay-zone-toggle), [data-zone-keep]";

  // Indices of the expanded zones nearest above and below divider `index`
  // (between zones index and index + 1); null when a side has none.
  function neighbours(folded, index) {
    let above = index;
    while (above >= 0 && folded[above]) above--;
    let below = index + 1;
    while (below < folded.length && folded[below]) below++;
    return above < 0 || below >= folded.length ? null : { above, below };
  }

  // Zone heights after dragging divider `index` by `delta` pixels: the two
  // neighbours trade height, each keeping at least `least`; the others hold.
  function resize(heights, folded, index, delta, least = MIN_HEIGHT) {
    const pair = neighbours(folded, index);
    if (!pair) return heights;
    const { above, below } = pair;
    const total = heights[above] + heights[below];
    if (total < 2 * least) return heights;
    const top = Math.max(least, Math.min(heights[above] + delta, total - least));
    const next = heights.slice();
    next[above] = top;
    next[below] = total - top;
    return next;
  }

  function readState(key) {
    try {
      const value = JSON.parse(SD.storage.get(key) || "{}");
      return value && typeof value === "object" ? value : {};
    } catch {
      return {};
    }
  }

  // `specs` is [{ id, title, weight }], top to bottom; an expanded zone's
  // share of the height is its weight over the expanded zones' weights.
  // Returns { element, zones: { id: { element, head, actions, body } } }.
  function zoneStack(storageKey, specs) {
    const root = element("div", "replay-zones");
    const stored = readState(storageKey);
    const zones = specs.map((spec) => {
      const saved = stored[spec.id] || {};
      const node = element("section", `replay-zone replay-zone-${spec.id}`);
      const head = element("div", "replay-zone-head");
      const toggle = element("button", "replay-zone-toggle");
      toggle.type = "button";
      toggle.appendChild(SD.workbench.svgIcon(CHEVRON));
      toggle.appendChild(element("h2", "", spec.title));
      const actions = element("div", "replay-zone-actions");
      actions.dataset.zoneKeep = "";
      head.append(toggle, actions);
      const body = element("div", "replay-zone-body");
      node.append(head, body);
      return {
        id: spec.id,
        element: node,
        head,
        toggle,
        actions,
        body,
        weight: Number(saved.weight) > 0 ? Number(saved.weight) : spec.weight,
        folded: saved.folded === true,
      };
    });
    const dividers = zones.slice(1).map((_, index) => {
      const divider = element("div", "replay-divider");
      divider.setAttribute("role", "separator");
      divider.setAttribute("aria-orientation", "horizontal");
      divider.title = "Drag to share the height of the zones beside it";
      return divider;
    });
    zones.forEach((zone, index) => {
      if (index) root.appendChild(dividers[index - 1]);
      root.appendChild(zone.element);
    });

    const folds = () => zones.map((zone) => zone.folded);

    function save() {
      const state = {};
      zones.forEach((zone) => {
        state[zone.id] = {
          weight: Math.round(zone.weight),
          folded: zone.folded,
        };
      });
      SD.storage.set(storageKey, JSON.stringify(state));
    }

    function apply() {
      zones.forEach((zone) => {
        zone.element.classList.toggle("folded", zone.folded);
        zone.element.style.flex = zone.folded ? "0 0 auto" : `${zone.weight} 1 0px`;
        zone.toggle.setAttribute("aria-expanded", String(!zone.folded));
        zone.toggle.title = zone.folded ? "Expand" : "Fold";
      });
      dividers.forEach((divider, index) => {
        divider.classList.toggle("inert", !neighbours(folds(), index));
      });
    }

    zones.forEach((zone) => {
      zone.head.addEventListener("click", (event) => {
        if (event.target.closest(HEAD_CONTROLS)) return;
        zone.folded = !zone.folded;
        apply();
        save();
      });
    });

    dividers.forEach((divider, index) => {
      let start = null;
      divider.addEventListener("pointerdown", (event) => {
        if (!neighbours(folds(), index)) return;
        start = {
          y: event.clientY,
          heights: zones.map((zone) => zone.element.getBoundingClientRect().height),
        };
        // Weights in pixels, so a drag moves the edge with the pointer.
        zones.forEach((zone, i) => {
          if (!zone.folded) zone.weight = start.heights[i];
        });
        divider.setPointerCapture(event.pointerId);
        divider.classList.add("resizing");
        event.preventDefault();
      });
      divider.addEventListener("pointermove", (event) => {
        if (!start) return;
        const heights = resize(start.heights, folds(), index, event.clientY - start.y);
        zones.forEach((zone, i) => {
          if (!zone.folded) zone.weight = heights[i];
        });
        apply();
      });
      const end = () => {
        if (!start) return;
        start = null;
        divider.classList.remove("resizing");
        save();
      };
      divider.addEventListener("pointerup", end);
      divider.addEventListener("pointercancel", end);
    });

    apply();
    const byId = {};
    zones.forEach((zone) => {
      byId[zone.id] = {
        element: zone.element,
        head: zone.head,
        actions: zone.actions,
        body: zone.body,
      };
    });
    return { element: root, zones: byId };
  }

  replay.resizeZones = resize;
  replay.zoneStack = zoneStack;
})();
