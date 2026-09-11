// Overview Page Module
// Drives the deployment overview shell: sidebar, mode/diagram selection, and
// the lifecycle of the diagram module currently on the canvas.

(function () {
  if (typeof deploymentConfig === "undefined") {
    console.error(
      "deploymentConfig is not defined. Make sure config.js is loaded.",
    );
    return;
  }

  const {
    deploymentName,
    availableDiagramTypes,
    availableModes,
    defaultDiagramType,
    defaultMode,
    packageName,
    systemsIndexPath,
    editorScheme = "vscode",
    systemDefinitionFile = "",
  } = deploymentConfig;

  // Diagram type -> the global the module script exports.
  const DIAGRAM_MODULES = {
    node_diagram: "NodeDiagramModule",
    sequence_diagram: "SequenceDiagramModule",
    logic_diagram: "LogicDiagramModule",
  };

  // Highlight state a module may leave on nodes outside its own container.
  const HIGHLIGHT_CLASSES = [
    "highlighted",
    "port-highlighted",
    "line-highlight",
    "line-hover",
  ];

  const SIDEBAR_MIN_WIDTH = 150;
  const SIDEBAR_MAX_WIDTH = 700;

  // Read by editor_link.js when the viewer has no stored preference.
  window.defaultEditorScheme = editorScheme;

  let currentDiagramType = urlParameter("diagram") || defaultDiagramType;
  let currentMode = urlParameter("mode") || defaultMode;
  let currentDiagramModule = null;

  const modeSelect = document.getElementById("mode-selector");
  const diagramContainer = document.getElementById("diagram-container");
  const loading = document.getElementById("loading");
  const infoPanel = document.getElementById("info-panel");

  // ── URL state ───────────────────────────────────────────────────────────────

  function urlParameter(name) {
    return new URLSearchParams(window.location.search).get(name);
  }

  function updateUrlParams() {
    const url = new URL(window.location.href);
    url.searchParams.set("diagram", currentDiagramType);
    url.searchParams.set("mode", currentMode);
    history.replaceState(null, "", url.toString());
  }

  // ── Diagram lifecycle ───────────────────────────────────────────────────────

  function showLoading(show, text = "Loading...") {
    loading.style.display = show ? "flex" : "none";
    document.getElementById("loading-text").textContent = text;
  }

  function clearStaleHighlights() {
    HIGHLIGHT_CLASSES.forEach((className) => {
      document
        .querySelectorAll(`.${className}`)
        .forEach((el) => el.classList.remove(className));
    });
  }

  async function loadDiagramModule(diagramType, mode) {
    if (currentDiagramModule) {
      currentDiagramModule.destroy();
      currentDiagramModule = null;
    }

    // A module renders asynchronously, so each one gets its own canvas: the
    // switch detaches the previous canvas and a late render lands off-page.
    diagramContainer.innerHTML = "";
    const canvas = document.createElement("div");
    canvas.className = "diagram-canvas";
    diagramContainer.appendChild(canvas);
    clearStaleHighlights();

    const moduleUrl = `${diagramType}.js`;
    await DiagramBase.loadScript(moduleUrl);

    const ModuleClass = window[DIAGRAM_MODULES[diagramType]];
    if (!ModuleClass) {
      throw new Error(
        `Module class not found for diagram type: ${diagramType}. Make sure ${moduleUrl} is properly generated.`,
      );
    }

    currentDiagramModule = new ModuleClass(canvas, {
      mode,
      deployment: deploymentName,
      onInfoUpdate: (data, type) =>
        window.InfoPanel.render(infoPanel, data, type),
    });

    // Only the graph diagrams populate the detail panel.
    infoPanel.style.display =
      diagramType === "node_diagram" || diagramType === "logic_diagram"
        ? "block"
        : "none";
  }

  async function loadDiagram(diagramType, mode) {
    showLoading(true, `Loading ${diagramType.replace("_", " ")}...`);
    try {
      await loadDiagramModule(diagramType, mode);
    } catch (error) {
      console.error("Error loading diagram:", error);
      diagramContainer.innerHTML = "";
      const box = document.createElement("div");
      box.className = "diagram-error";
      box.textContent = `Error loading diagram: ${error.message || "Unknown error occurred"}`;
      diagramContainer.appendChild(box);
    } finally {
      showLoading(false);
    }
  }

  function selectDiagramType(diagramType) {
    currentDiagramType = diagramType;
    updateUrlParams();
    updateDiagramTypeSelection();
    loadDiagram(currentDiagramType, currentMode);
  }

  function updateDiagramTypeSelection() {
    document.querySelectorAll(".diagram-type-item").forEach((item) => {
      item.classList.toggle(
        "active",
        item.getAttribute("data-type") === currentDiagramType,
      );
    });
  }

  // ── Sidebar ─────────────────────────────────────────────────────────────────

  function initializeHeader() {
    document.title = `Autoware System Designer - ${deploymentName}`;

    const backLink = document.querySelector(".home-link");
    if (backLink) backLink.href = systemsIndexPath;

    const launcherLink = document.getElementById("launcher-link");
    if (launcherLink)
      launcherLink.href = `${deploymentName}_launch_commands.html`;

    const headerTitle = document.querySelector("#sidebar-header h1");
    if (headerTitle) headerTitle.textContent = deploymentName;

    const packageInfo = document.querySelector(".package-info");
    if (!packageInfo) return;
    packageInfo.textContent = `Package: ${packageName}`;
    if (systemDefinitionFile) {
      const link = window.createSourceLink(systemDefinitionFile, 1);
      link.classList.add("package-source-link");
      packageInfo.appendChild(link);
    }
  }

  function initializeSelectors() {
    modeSelect.innerHTML = "";
    availableModes.forEach((mode) => {
      const option = document.createElement("option");
      option.value = mode;
      option.textContent = mode;
      modeSelect.appendChild(option);
    });
    modeSelect.value = currentMode;
    modeSelect.addEventListener("change", (e) => {
      currentMode = e.target.value;
      updateUrlParams();
      loadDiagram(currentDiagramType, currentMode);
    });

    const diagramList = document.getElementById("diagram-list");
    diagramList.innerHTML = '<div class="section-title">Diagram Types</div>';
    availableDiagramTypes.forEach((type) => {
      const item = document.createElement("a");
      item.className = "diagram-type-item";
      item.href = "#";
      item.dataset.type = type;
      item.textContent = type
        .replace(/_/g, " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
      item.addEventListener("click", (e) => {
        e.preventDefault();
        selectDiagramType(type);
      });
      diagramList.appendChild(item);
    });
  }

  function initializeSettings() {
    const overlay = document.getElementById("settings-overlay");
    const popup = document.getElementById("settings-popup");
    const setOpen = (open) => {
      popup.classList.toggle("open", open);
      overlay.classList.toggle("open", open);
    };

    document
      .getElementById("settings-btn")
      .addEventListener("click", () => setOpen(true));
    document
      .getElementById("settings-close")
      .addEventListener("click", () => setOpen(false));
    overlay.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") setOpen(false);
    });

    document
      .getElementById("dark-mode-switch")
      .addEventListener("click", () => window.toggleDarkMode());

    const editorSelect = document.getElementById("editor-selector");
    editorSelect.value = localStorage.getItem("editorScheme") || editorScheme;
    editorSelect.addEventListener("change", (e) => {
      localStorage.setItem("editorScheme", e.target.value);
    });
  }

  // Drag the divider to resize the sidebar; the diagram re-fits on release.
  function initializeResizer() {
    const sidebar = document.getElementById("sidebar");
    const resizer = document.getElementById("sidebar-resizer");
    let isResizing = false;

    resizer.addEventListener("mousedown", () => {
      isResizing = true;
      resizer.classList.add("resizing");
      document.body.classList.add("resizing-sidebar");
    });

    document.addEventListener("mousemove", (e) => {
      if (!isResizing) return;
      const width = Math.min(
        Math.max(e.clientX, SIDEBAR_MIN_WIDTH),
        SIDEBAR_MAX_WIDTH,
      );
      sidebar.style.width = `${width}px`;
    });

    document.addEventListener("mouseup", () => {
      if (!isResizing) return;
      isResizing = false;
      resizer.classList.remove("resizing");
      document.body.classList.remove("resizing-sidebar");
      window.dispatchEvent(new Event("resize"));
    });
  }

  // ── Startup ─────────────────────────────────────────────────────────────────

  // theme.js calls this after the viewer flips the theme.
  window.__onThemeChange = function () {
    if (typeof currentDiagramModule?.updateTheme === "function") {
      currentDiagramModule.updateTheme();
    }
  };

  window.addEventListener("load", () => {
    initializeHeader();
    initializeSelectors();
    initializeSettings();
    initializeResizer();
    updateUrlParams();
    window.initializeTheme();

    updateDiagramTypeSelection();
    loadDiagram(currentDiagramType, currentMode);
  });
})();
