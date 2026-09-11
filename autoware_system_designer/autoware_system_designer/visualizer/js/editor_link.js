// Editor Link Module
// Builds editor:// URLs for the source-file links shown across the page.

(function () {
  // Editors reachable with the vscode-style scheme; everything else needs its own shape.
  const JETBRAINS_SCHEMES = ["idea", "clion", "pycharm", "webstorm"];

  function currentScheme() {
    return (
      localStorage.getItem("editorScheme") ||
      window.defaultEditorScheme ||
      "vscode"
    );
  }

  function getEditorUrl(filePath, line = 1) {
    const scheme = currentScheme();
    const encoded = encodeURIComponent(filePath);
    if (JETBRAINS_SCHEMES.includes(scheme)) {
      return `${scheme}://open?file=${encoded}&line=${line}`;
    }
    if (scheme === "subl") {
      return `subl://open?url=file://${encoded}&line=${line}`;
    }
    // vscode, vscode-insiders, cursor, windsurf all share the same scheme shape.
    return `${scheme}://file/${encoded}:${line}`;
  }

  // Link to a source location, labelled with the file's basename.
  function createSourceLink(filePath, line = 1) {
    const link = document.createElement("a");
    link.className = "source-link";
    link.href = getEditorUrl(filePath, line);
    link.title = `${filePath}:${line}`;
    link.textContent = filePath.split("/").pop();
    return link;
  }

  window.getEditorUrl = getEditorUrl;
  window.createSourceLink = createSourceLink;
})();
