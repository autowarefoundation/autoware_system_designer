// Run-state marks shared by the Launch monitor and the diagram overlays: one
// shape and colour per token (tokens.css), as a DOM mark or an SVG glyph, the
// legend naming them, and the roll-up of member states over a design tree.
(function () {
  const SD = window.SD;
  const SVG_NS = "http://www.w3.org/2000/svg";

  // Legend entries: token, label, meaning.
  const LEGEND = [
    ["running", "running", "process running or node loaded"],
    ["ok", "exited", "exited with code 0"],
    ["warning", "warning", "loading, respawning, or exited with another code"],
    ["failed", "failed", "failed to start or load"],
    ["idle", "idle", "pending, done, or not launched"],
  ];

  // Tokens by severity; the severest among a group names the group.
  const RANK = { idle: 0, ok: 1, running: 2, warning: 3, failed: 4 };

  function mark(token) {
    const node = document.createElement("span");
    node.className = `state-dot ${token}`;
    return node;
  }

  // The mark's shape as an SVG element of `size` units, centred on (cx, cy):
  // a disc for running, a square for exited, a triangle for warning, a diamond
  // for failed and a ring for idle, coloured by the token's class.
  function svgMark(token, cx, cy, size) {
    const r = size / 2;
    let shape;
    switch (token) {
      case "ok":
        shape = document.createElementNS(SVG_NS, "rect");
        shape.setAttribute("x", cx - r);
        shape.setAttribute("y", cy - r);
        shape.setAttribute("width", size);
        shape.setAttribute("height", size);
        shape.setAttribute("rx", size * 0.12);
        break;
      case "warning":
        shape = document.createElementNS(SVG_NS, "polygon");
        shape.setAttribute(
          "points",
          `${cx},${cy - r} ${cx + r},${cy + r} ${cx - r},${cy + r}`,
        );
        break;
      case "failed":
        shape = document.createElementNS(SVG_NS, "polygon");
        shape.setAttribute(
          "points",
          `${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`,
        );
        break;
      default:
        shape = document.createElementNS(SVG_NS, "circle");
        shape.setAttribute("cx", cx);
        shape.setAttribute("cy", cy);
        shape.setAttribute("r", token === "idle" ? r * 0.8 : r);
    }
    shape.classList.add("state-glyph", token);
    return shape;
  }

  // Legend rows with their marks; `className` names the container's style.
  function legend(className) {
    const node = document.createElement("div");
    node.className = className;
    LEGEND.forEach(([token, label, meaning]) => {
      const item = document.createElement("span");
      item.className = "state-legend-item";
      item.title = meaning;
      const text = document.createElement("span");
      text.textContent = label;
      item.append(mark(token), text);
      node.appendChild(item);
    });
    return node;
  }

  // Live state of a group from its parts' states: the severest token, the
  // members per token (`counts`, summed through nested groups) as the label,
  // dead when every part is down, and the busy-log note of the part whose
  // output grows fastest. Restarts belong to members; a group has none.
  function summarize(parts) {
    const counts = new Map();
    parts.forEach((part) => {
      const own = part.counts || new Map([[part.token, part.members]]);
      own.forEach((count, token) =>
        counts.set(token, (counts.get(token) || 0) + count),
      );
    });
    const worst = parts.reduce((a, b) =>
      (RANK[b.token] || 0) > (RANK[a.token] || 0) ? b : a,
    );
    const busiest = parts.reduce((a, b) =>
      (b.rate || 0) > (a.rate || 0) ? b : a,
    );
    return {
      token: worst.token,
      label: Array.from(counts)
        .sort((a, b) => (RANK[b[0]] || 0) - (RANK[a[0]] || 0))
        .map(([token, count]) => `${count} ${token}`)
        .join(" \u00b7 "),
      dead: parts.every((part) => part.dead) ? "every member down" : null,
      restarts: null,
      rate: busiest.rate || 0,
      busy: busiest.busy || "",
      members: parts.reduce((sum, part) => sum + part.members, 0),
      counts,
    };
  }

  // Live state per design instance under `root`, keyed by unique_id: a
  // node's own entry of `states` (node path -> state) as one member, a
  // module's the summary of the nodes within. An instance with no launched
  // member within has no entry.
  function rollup(root, states) {
    const byId = new Map();
    const visit = (instance) => {
      if (!instance?.unique_id) return null;
      let info = null;
      if (instance.entity_type === "node") {
        const state = instance.path && states.get(instance.path);
        if (state) info = { ...state, members: 1 };
      } else {
        const parts = (instance.children || []).map(visit).filter(Boolean);
        if (parts.length) info = summarize(parts);
      }
      if (info) byId.set(String(instance.unique_id), info);
      return info;
    };
    if (states?.size) visit(root);
    return byId;
  }

  SD.RunState = { LEGEND, RANK, mark, svgMark, legend, summarize, rollup };
})();
