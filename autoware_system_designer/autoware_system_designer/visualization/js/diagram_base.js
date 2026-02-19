// Diagram Base Module
// This module provides common functionality for diagram modules

class DiagramBase {
  constructor(container, options = {}) {
    this.container = container;
    this.options = {
      mode: options.mode || "default",
      deployment: options.deployment || "",
      ...options,
    };
  }

  isDarkMode() {
    return document.documentElement.getAttribute("data-theme") === "dark";
  }

  async loadScript(src) {
    return new Promise((resolve, reject) => {
      // Check if script is already loaded
      const existingScript = document.querySelector(`script[src="${src}"]`);
      if (existingScript) {
        resolve();
        return;
      }

      const script = document.createElement("script");
      script.src = src;
      script.onload = () => resolve();
      script.onerror = (event) => {
        const error = new Error(`Failed to load script: ${src}`);
        error.event = event;
        reject(error);
      };
      document.head.appendChild(script);
    });
  }

  async loadDataScript(mode, type) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `data/${mode}_${type}.js`;
      script.onload = () => resolve();
      script.onerror = (error) => reject(error);
      document.head.appendChild(script);
    });
  }

  showError(message) {
    this.container.innerHTML = `<div style="display: flex; justify-content: center; align-items: center; height: 100%; color: var(--error-color, #dc3545);">${message}</div>`;
  }

  updateInfoPanel(data, type) {
    if (this.options.onInfoUpdate) {
      this.options.onInfoUpdate(data, type);
    }
  }

  adjustColorBrightness(hexColor, factor) {
    // Remove # if present
    hexColor = hexColor.replace("#", "");

    // Parse RGB components
    const r = parseInt(hexColor.substr(0, 2), 16);
    const g = parseInt(hexColor.substr(2, 2), 16);
    const b = parseInt(hexColor.substr(4, 2), 16);

    // Adjust brightness
    const newR = Math.min(255, Math.max(0, Math.round(r + (255 - r) * factor)));
    const newG = Math.min(255, Math.max(0, Math.round(g + (255 - g) * factor)));
    const newB = Math.min(255, Math.max(0, Math.round(b + (255 - b) * factor)));

    // Convert back to hex
    return `#${newR.toString(16).padStart(2, "0")}${newG.toString(16).padStart(2, "0")}${newB.toString(16).padStart(2, "0")}`;
  }

  getComputedStyleValue(prop, fallback) {
    const computedStyle = getComputedStyle(document.documentElement);
    return computedStyle.getPropertyValue(prop).trim() || fallback;
  }

  destroy() {
    this.container.innerHTML = "";
  }
}

// Export for use in the overview page and other modules
window.DiagramBase = DiagramBase;
