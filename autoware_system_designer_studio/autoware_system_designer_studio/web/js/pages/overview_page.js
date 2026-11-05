// Workbench page entry: starts the shell and keeps the canvas showing the
// diagram module of the selected diagram type and mode.

(function () {
  if (typeof deploymentConfig === "undefined") {
    console.error(
      "deploymentConfig is not defined. Make sure config.js is loaded.",
    );
    return;
  }

  const config = deploymentConfig;
  const {
    deploymentName,
    diagramScripts,
    dataBase,
    latencyModes,
    editorScheme = "vscode",
  } = config;

  // Highlight state a module may leave on nodes outside its own container.
  const HIGHLIGHT_CLASSES = ["highlighted", "port-highlighted"];

  SD.editor.setDefaultScheme(editorScheme);

  // Diagram that draws every entity; a focus request the current diagram cannot serve opens it.
  const FOCUS_DIAGRAM = "node_diagram";

  let currentDiagramModule = null;
  let pendingFocusId = null;

  const diagramContainer = document.getElementById("diagram-container");
  const loading = document.getElementById("loading");
  const infoPanel = document.getElementById("info-panel");
  const inspectorEmpty = document.getElementById("inspector-empty");

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

    const moduleUrl = diagramScripts[diagramType];
    await DiagramBase.loadScript(moduleUrl);

    const ModuleClass = SD.diagrams.get(diagramType);
    if (!ModuleClass) {
      throw new Error(
        `Module class not found for diagram type: ${diagramType}. Make sure ${moduleUrl} is properly generated.`,
      );
    }

    currentDiagramModule = new ModuleClass(canvas, {
      mode,
      deployment: deploymentName,
      dataBase,
      latencyModes,
      onInfoUpdate: (data, type) => {
        infoPanel.style.display = "block";
        inspectorEmpty.hidden = true;
        SD.InfoPanel.render(infoPanel, data, type);
      },
    });
  }

  async function loadDiagram() {
    const diagramType = SD.store.get("diagram");
    const mode = SD.store.get("mode");
    showLoading(true, `Loading ${diagramType.replace("_", " ")}...`);
    try {
      await loadDiagramModule(diagramType, mode);
      if (pendingFocusId) {
        const id = pendingFocusId;
        pendingFocusId = null;
        focusEntity(id);
      }
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

  async function focusEntity(id) {
    SD.workbench.showMainTab("diagram");
    const module = currentDiagramModule;
    if (typeof module?.focusEntity === "function") {
      await module.ready;
      if (module === currentDiagramModule && module.focusEntity(id)) return;
    }
    if (SD.store.get("diagram") !== FOCUS_DIAGRAM) {
      pendingFocusId = id;
      SD.store.set("diagram", FOCUS_DIAGRAM);
    }
  }

  SD.on("focusentity", ({ id }) => focusEntity(id));

  SD.on("themechange", () => {
    if (typeof currentDiagramModule?.updateTheme === "function") {
      currentDiagramModule.updateTheme();
    }
  });

  window.addEventListener("load", () => {
    SD.theme.init();
    SD.workbench.init(config);
    SD.store.onChange("diagram", loadDiagram);
    SD.store.onChange("mode", loadDiagram);
    loadDiagram();
  });
})();
