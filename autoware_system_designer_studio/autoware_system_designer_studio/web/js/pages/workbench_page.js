// Workbench page entry: applies the theme and starts the shell with this
// system's config.
(function () {
  if (typeof deploymentConfig === "undefined") {
    console.error(
      "deploymentConfig is not defined. Make sure config.js is loaded.",
    );
    return;
  }
  SD.editor.setDefaultScheme(deploymentConfig.editorScheme || "vscode");

  window.addEventListener("load", () => {
    SD.theme.init();
    SD.workbench.init(deploymentConfig);
  });
})();
