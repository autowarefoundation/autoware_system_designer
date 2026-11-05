// Workbench layout: panel widths, the side panel collapse and the inspector
// visibility, each kept as a viewer preference. The inspector pane exists only
// beside an activity that owns an inspector view.
(function () {
  const SD = window.SD;

  const SIDE_PANEL_WIDTH = { key: "workbench.sidePanelWidth", min: 180, max: 640 };
  const INSPECTOR_WIDTH = { key: "workbench.inspectorWidth", min: 220, max: 720 };
  const INSPECTOR_HIDDEN_KEY = "workbench.inspectorHidden";

  const el = (id) => document.getElementById(id);

  function clampWidth(spec, value) {
    return Math.min(Math.max(value, spec.min), spec.max);
  }

  // Dragging a divider sets the width of `target`; `fromRight` measures it from the window's right edge.
  function initResizer(resizer, target, spec, fromRight) {
    const stored = parseInt(SD.storage.get(spec.key), 10);
    if (stored) target.style.width = `${clampWidth(spec, stored)}px`;
    let start = null;

    resizer.addEventListener("mousedown", (e) => {
      start = { x: e.clientX, width: target.getBoundingClientRect().width };
      resizer.classList.add("resizing");
      document.body.classList.add("resizing-panel");
      e.preventDefault();
    });
    document.addEventListener("mousemove", (e) => {
      if (!start) return;
      const delta = fromRight ? start.x - e.clientX : e.clientX - start.x;
      target.style.width = `${clampWidth(spec, start.width + delta)}px`;
    });
    document.addEventListener("mouseup", () => {
      if (!start) return;
      start = null;
      resizer.classList.remove("resizing");
      document.body.classList.remove("resizing-panel");
      SD.storage.set(spec.key, String(parseInt(target.style.width, 10)));
    });
  }

  function isSidePanelCollapsed() {
    return document.body.classList.contains("side-panel-collapsed");
  }

  function setSidePanelCollapsed(collapsed) {
    document.body.classList.toggle("side-panel-collapsed", collapsed);
  }

  function setInspectorHidden(hidden) {
    document.body.classList.toggle("inspector-hidden", hidden);
    el("inspector-toggle").setAttribute("aria-pressed", String(!hidden));
    SD.storage.set(INSPECTOR_HIDDEN_KEY, hidden ? "1" : "");
  }

  function setInspectorAvailable(available) {
    document.body.classList.toggle("no-inspector", !available);
    el("inspector-toggle").hidden = !available;
  }

  function init() {
    initResizer(el("side-panel-resizer"), el("side-panel"), SIDE_PANEL_WIDTH, false);
    initResizer(el("inspector-resizer"), el("inspector"), INSPECTOR_WIDTH, true);
    el("inspector-toggle").addEventListener("click", () => {
      setInspectorHidden(!document.body.classList.contains("inspector-hidden"));
    });
    if (SD.storage.get(INSPECTOR_HIDDEN_KEY)) setInspectorHidden(true);
  }

  SD.layout = {
    init,
    isSidePanelCollapsed,
    setSidePanelCollapsed,
    setInspectorHidden,
    setInspectorAvailable,
  };
})();
