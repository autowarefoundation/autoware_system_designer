// Sequence Diagram Module
// This module provides functionality to render sequence diagrams in a given container

class SequenceDiagramModule extends DiagramBase {
  constructor(container, options = {}) {
    super(container, options);
    this.panZoomInstance = null;
    this.init();
  }

  async init() {
    // Load required libraries if not already loaded
    if (typeof mermaid === "undefined") {
      await this.loadScript(
        "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js",
      );
    }
    if (typeof svgPanZoom === "undefined") {
      await this.loadScript(
        "https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js",
      );
    }

    // Initialize Mermaid
    this.initializeMermaid();

    // Load and render the diagram
    await this.loadAndRender();
  }

  initializeMermaid() {
    mermaid.initialize({
      startOnLoad: false,
      maxTextSize: 1000000,
      theme: this.isDarkMode() ? "dark" : "default",
      securityLevel: "loose",
      sequence: {
        showSequenceNumbers: false,
        useMaxWidth: false,
        mirrorActors: true,
        bottomMarginAdj: 10,
        messageAlign: "center",
      },
    });
  }

  async loadAndRender() {
    try {
      // Load data if not already loaded
      if (
        !window.sequenceDiagramData ||
        !window.sequenceDiagramData[this.options.mode]
      ) {
        await this.loadDataScript(this.options.mode, "sequence_diagram");
      }

      if (
        window.sequenceDiagramData &&
        window.sequenceDiagramData[this.options.mode]
      ) {
        // Render sequence diagram
        const data = window.sequenceDiagramData[this.options.mode];
        let mermaidSyntax = "";

        // Check if data is the new object format or legacy string format
        if (typeof data === "object") {
          mermaidSyntax = this.generateMermaidSyntax(data);
        } else {
          mermaidSyntax = data;
        }

        await this.renderSequenceDiagram(mermaidSyntax);
      } else {
        this.showError(`Failed to load data for mode: ${this.options.mode}`);
      }
    } catch (error) {
      console.error("Error loading sequence diagram:", error);
      this.showError(`Error loading sequence diagram: ${error.message}`);
    }
  }

  generateMermaidSyntax(data) {
    const lines = [];
    lines.push("sequenceDiagram");
    lines.push("");
    lines.push("%% Definitions");

    // Build a map of all events for quick lookup
    const eventMap = new Map();
    this.collectAllEvents(data, eventMap);

    if (data.children && Array.isArray(data.children)) {
      data.children.forEach((child) => {
        this.buildLogicGraph(child, lines);
      });
    }

    lines.push("");
    lines.push("%% Connections");

    if (data.children && Array.isArray(data.children)) {
      data.children.forEach((child) => {
        this.buildConnectionGraph(child, lines, eventMap);
      });
    }

    return lines.join("\n");
  }

  collectAllEvents(instance, eventMap) {
    // Collect events from this instance
    if (instance.events && Array.isArray(instance.events)) {
      instance.events.forEach((e) => {
        // If process_event is undefined, assume true for instance events
        if (e.process_event === undefined) e.process_event = true;
        eventMap.set(e.unique_id, e);
      });
    }
    // Collect events from ports
    if (instance.in_ports && Array.isArray(instance.in_ports)) {
      instance.in_ports.forEach((p) => {
        if (p.event) {
          if (p.event.process_event === undefined)
            p.event.process_event = false;
          eventMap.set(p.event.unique_id, p.event);
        }
      });
    }
    if (instance.out_ports && Array.isArray(instance.out_ports)) {
      instance.out_ports.forEach((p) => {
        if (p.event) {
          if (p.event.process_event === undefined)
            p.event.process_event = false;
          eventMap.set(p.event.unique_id, p.event);
        }
      });
    }

    // Recurse
    if (instance.children && Array.isArray(instance.children)) {
      instance.children.forEach((child) =>
        this.collectAllEvents(child, eventMap),
      );
    }
  }

  buildLogicGraph(instance, lines) {
    if (instance.entity_type === "node") {
      if (instance.events && Array.isArray(instance.events)) {
        instance.events.forEach((event) => {
          const nsLabel = instance.namespace
            ? instance.namespace.join("<br/>")
            : "";
          const fullLabel = `${nsLabel}<br/><br/>[${event.name}]`;
          // Using unique_id from data
          lines.push(`participant ${event.unique_id} as ${fullLabel}`);
        });
      }
    } else if (instance.entity_type === "module") {
      if (instance.children && Array.isArray(instance.children)) {
        instance.children.forEach((child) => {
          this.buildLogicGraph(child, lines);
        });
      }
    }
  }

  buildConnectionGraph(instance, lines, eventMap) {
    if (instance.entity_type === "node") {
      if (instance.events && Array.isArray(instance.events)) {
        instance.events.forEach((event) => {
          if (event.process_event === true) {
            if (event.trigger_ids && Array.isArray(event.trigger_ids)) {
              event.trigger_ids.forEach((triggerId) => {
                const trigger = eventMap.get(triggerId);
                if (trigger) {
                  this.eventConnection(
                    instance.name,
                    event,
                    trigger.name,
                    trigger,
                    lines,
                    eventMap,
                  );
                }
              });
            }
          }
        });
      }
    } else if (instance.entity_type === "module") {
      if (instance.children && Array.isArray(instance.children)) {
        instance.children.forEach((child) => {
          this.buildConnectionGraph(child, lines, eventMap);
        });
      }
    }
  }

  eventConnection(
    rootName,
    eventOrigin,
    connectionName,
    eventTarget,
    lines,
    eventMap,
  ) {
    if (
      (eventTarget.type === "on_input" || eventTarget.type === "to_output") &&
      eventTarget.process_event === false
    ) {
      const newConnectionName = eventTarget.name;
      if (eventTarget.trigger_ids && Array.isArray(eventTarget.trigger_ids)) {
        eventTarget.trigger_ids.forEach((triggerId) => {
          const trigger = eventMap.get(triggerId);
          if (trigger) {
            this.eventConnection(
              rootName,
              eventOrigin,
              newConnectionName,
              trigger,
              lines,
              eventMap,
            );
          }
        });
      }
    } else {
      const targetNamespace =
        eventTarget.namespace && eventTarget.namespace.length > 0
          ? eventTarget.namespace[eventTarget.namespace.length - 1]
          : "";
      const label = `${targetNamespace} to ${rootName}_${connectionName}`;
      lines.push(
        `${eventTarget.unique_id}->>${eventOrigin.unique_id}: ${label}`,
      );
    }
  }

  async renderSequenceDiagram(mermaidSyntax) {
    // Clear container
    this.container.innerHTML = "";

    // Create mermaid container that takes full height
    const mermaidContainer = document.createElement("div");
    mermaidContainer.className = "mermaid";
    mermaidContainer.style.width = "1200px"; // Set explicit width for Mermaid to generate larger SVG
    mermaidContainer.style.height = "100%"; // Take full height of parent
    mermaidContainer.style.minHeight = "800px"; // Minimum height fallback
    mermaidContainer.style.overflow = "visible";
    mermaidContainer.style.display = "flex";
    mermaidContainer.style.alignItems = "stretch";

    const bgColor = this.getComputedStyleValue(
      "--bg-secondary",
      this.isDarkMode() ? "#1a1a1a" : "#ffffff",
    );

    mermaidContainer.style.backgroundColor = bgColor;
    mermaidContainer.style.padding = "20px";
    mermaidContainer.style.borderRadius = "8px";
    this.container.appendChild(mermaidContainer);

    try {
      // Render with Mermaid
      const { svg } = await mermaid.render(
        "sequence-diagram-svg-" + Date.now(),
        mermaidSyntax,
      );
      mermaidContainer.innerHTML = svg;

      const svgElement = mermaidContainer.querySelector("svg");
      if (svgElement) {
        // Configure SVG to take full height of container
        svgElement.style.maxWidth = "100%";
        svgElement.style.width = "100%";
        svgElement.style.height = "100%"; // Take full height
        svgElement.style.minHeight = "800px"; // Minimum height fallback

        // Keep the original dimensions for proper aspect ratio
        // but allow it to scale down if needed
        if (
          !svgElement.getAttribute("viewBox") &&
          svgElement.getAttribute("width") &&
          svgElement.getAttribute("height")
        ) {
          const width = parseFloat(svgElement.getAttribute("width"));
          const height = parseFloat(svgElement.getAttribute("height"));
          if (width > 0 && height > 0) {
            svgElement.setAttribute("viewBox", `0 0 ${width} ${height}`);
          }
        }

        // Remove fixed dimensions to allow CSS control
        svgElement.removeAttribute("width");
        svgElement.removeAttribute("height");

        // Keep container at full height
        mermaidContainer.style.width = "100%";
        mermaidContainer.style.height = "100%";

        // Add interaction listeners
        this.addInteractionListeners(svgElement);

        // Initialize pan-zoom
        this.panZoomInstance = svgPanZoom(svgElement, {
          zoomEnabled: true,
          controlIconsEnabled: false,
          fit: true,
          center: true,
          minZoom: 0.5,
          maxZoom: 50,
          zoomScaleSensitivity: 0.4,
          dblClickZoomEnabled: false,
        });

        // Initial fit
        setTimeout(() => {
          if (this.panZoomInstance) {
            this.panZoomInstance.resize();
            this.panZoomInstance.fit();
            this.panZoomInstance.center();
          }
        }, 200);
      }
    } catch (e) {
      console.error("Mermaid rendering failed:", e);
      throw new Error("Error rendering sequence diagram: " + e.message);
    }
  }

  addInteractionListeners(svgElement) {
    let selectedLine = null; // Track currently selected line

    // Helper to check if element is a visible message line
    const isLine = (el) => {
      if (!el || !el.getAttribute) return false;
      const cls = el.getAttribute("class") || "";
      return cls && cls.split(" ").some((c) => c.startsWith("messageLine"));
    };

    // Helper to check if element is message text
    const isText = (el) => {
      if (!el || !el.getAttribute) return false;
      const cls = el.getAttribute("class") || "";
      return cls && cls.split(" ").some((c) => c.startsWith("messageText"));
    };

    // Apply highlight styles to a line
    const applyHighlight = (lineElement) => {
      lineElement.classList.remove("line-hover"); // Remove hover effect first
      lineElement.classList.add("line-highlight");
    };

    // Remove highlight styles from a line
    const removeHighlight = (lineElement) => {
      lineElement.classList.remove("line-highlight");
    };

    // Clear all highlights
    const clearAllHighlights = () => {
      svgElement.querySelectorAll('[class*="messageLine"]').forEach((line) => {
        removeHighlight(line);
        line.classList.remove("line-hover"); // Also remove any hover effects
      });
      selectedLine = null;
    };

    // Add hover effects
    svgElement.addEventListener("mouseover", (e) => {
      const target = e.target;
      if (
        (target.tagName === "path" || target.tagName === "line") &&
        isLine(target)
      ) {
        if (target !== selectedLine) {
          target.classList.add("line-hover");
        }
      }
    });

    svgElement.addEventListener("mouseout", (e) => {
      const target = e.target;
      if (
        (target.tagName === "path" || target.tagName === "line") &&
        isLine(target)
      ) {
        // If this line is not selected, remove hover highlight
        if (target !== selectedLine) {
          target.classList.remove("line-hover");
        }
      }
    });

    // Add click interactions
    svgElement.addEventListener("click", (e) => {
      let target = e.target;
      let lineElement = null;

      // 1. Check if clicked element is the line itself
      if (target.tagName === "path" || target.tagName === "line") {
        if (isLine(target)) {
          lineElement = target;
        }
      }
      // 2. Check if clicked element is text
      else if (target.tagName === "text" || target.tagName === "tspan") {
        // If text is clicked, find the associated message line.
        const textEl = target.closest("text");
        if (textEl && isText(textEl)) {
          let next = textEl.nextElementSibling;
          while (next) {
            // If we find a line, that's our target
            if (isLine(next)) {
              lineElement = next;
              break;
            }
            // If we hit another text, we've likely gone too far
            if (next.tagName === "text" && isText(next)) {
              break;
            }
            next = nextElementSibling;
          }
        }
      }

      if (lineElement) {
        // If clicking the same line that's already selected, deselect it
        if (selectedLine === lineElement) {
          clearAllHighlights();
        } else {
          // Clear previous selection and select new line
          clearAllHighlights();
          selectedLine = lineElement;
          applyHighlight(selectedLine);
        }
        e.stopPropagation(); // Prevent other handlers
      } else {
        // Clicked background, clear all highlights
        clearAllHighlights();
      }
    });
  }

  resetZoom() {
    if (this.panZoomInstance) {
      this.panZoomInstance.reset();
      this.panZoomInstance.center();
    }
  }

  updateTheme() {
    this.initializeMermaid();
    if (
      window.sequenceDiagramData &&
      window.sequenceDiagramData[this.options.mode]
    ) {
      this.renderSequenceDiagram(window.sequenceDiagramData[this.options.mode]);
    }
  }

  destroy() {
    if (this.panZoomInstance) {
      this.panZoomInstance.destroy();
      this.panZoomInstance = null;
    }
    super.destroy();
  }
}

// Export for use in the overview page
window.SequenceDiagramModule = SequenceDiagramModule;
