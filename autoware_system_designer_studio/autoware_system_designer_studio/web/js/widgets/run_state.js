// Run-state marks shared by the Launch monitor and the diagram overlays: one
// shape and colour per token (tokens.css), as a DOM mark or an SVG glyph, and
// the legend naming them.
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

  SD.RunState = { LEGEND, RANK, mark, svgMark, legend };
})();
