// Logic Diagram Module
// This module provides functionality to render logic diagrams using Viz.js (Graphviz)

class LogicDiagramModule extends DiagramBase {
    constructor(container, options = {}) {
        super(container, options);
        this.panZoomInstance = null;
        this.init();
    }

    async init() {
        try {
            // Load required libraries if not already loaded
            if (!this.isGraphvizLoaded()) {
                console.log('Loading viz.js...');
                // Load the main viz.js library first
                await this.loadScript('https://cdn.jsdelivr.net/npm/viz.js@2.1.2/viz.js');
                // Then load the full render module
                await this.loadScript('https://cdn.jsdelivr.net/npm/viz.js@2.1.2/full.render.js');
                console.log('viz.js loaded, graphviz available:', this.isGraphvizLoaded());
            }

            // Wait for graphviz to be fully ready
            await this.waitForGraphvizReady();

            if (typeof svgPanZoom === 'undefined') {
                console.log('Loading svg-pan-zoom...');
                await this.loadScript('https://cdn.jsdelivr.net/npm/svg-pan-zoom@3.6.1/dist/svg-pan-zoom.min.js');
                console.log('svg-pan-zoom loaded');
            }

            // Load and render the diagram
            await this.loadAndRender();
        } catch (error) {
            console.error('Error in init:', error);
            throw error;
        }
    }

    isGraphvizLoaded() {
        return typeof window !== 'undefined' && typeof window.Viz === 'function' && typeof window.Viz.Module !== 'undefined' && typeof window.Viz.render === 'function';
    }

    async waitForGraphvizReady() {
        return new Promise((resolve) => {
            const checkReady = () => {
                if (this.isGraphvizLoaded()) {
                    resolve();
                } else {
                    setTimeout(checkReady, 50);
                }
            };
            checkReady();
        });
    }

    async loadAndRender() {
        try {
            // Load data if not already loaded
            if (!window.logicDiagramData || !window.logicDiagramData[this.options.mode]) {
                await this.loadDataScript(this.options.mode, 'logic_diagram');
            }

            if (!window.logicDiagramData || !window.logicDiagramData[this.options.mode]) {
                throw new Error(`No logic diagram data available for mode: ${this.options.mode}`);
            }

            // Generate DOT syntax from data and render
            const data = window.logicDiagramData[this.options.mode];
            const dotSyntax = this.generateDotSyntax(data);
            await this.renderLogicDiagram(dotSyntax);

        } catch (error) {
            console.error('Error loading logic diagram:', error);
            this.showError(`Error loading logic diagram: ${error.message}`);
        }
    }

    generateDotSyntax(root) {
        const isDark = this.isDarkMode();
        
        const colors = {
            bg: this.getComputedStyleValue('--bg-secondary', isDark ? "#2d2d2d" : "#dddddd"),
            nodeBg: this.getComputedStyleValue('--bg-primary', isDark ? "#1e1e1e" : "white"),
            edge: this.getComputedStyleValue('--text-muted', isDark ? "#6c757d" : "#000066"),
            text: this.getComputedStyleValue('--text-primary', isDark ? "#e9ecef" : "#333333"),
            input: isDark ? "#004085" : "#e0f7ff",
            output: isDark ? "#856404" : "#fff4e0",
            cloud: this.getComputedStyleValue('--border-hover', isDark ? "#adb5bd" : "gray"),
            cloudEdge: this.getComputedStyleValue('--text-muted', isDark ? "#6c757d" : "#555577")
        };

        let dotLines = [];

        // Graph header
        dotLines.push(`digraph logic_diagram {`);
        dotLines.push(`\tlabel="System Diagram ${root.name}";`);
        dotLines.push(`\tlabelloc=top;`);
        dotLines.push(`\tfontsize=20;`);
        dotLines.push(`\tfontname="Arial";`);
        dotLines.push(`\tfontcolor="${colors.text}";`);
        dotLines.push(`\tbgcolor="${isDark ? '#1a1a1a' : 'white'}";`);
        dotLines.push(`\trankdir=LR;`);
        dotLines.push(`\tnode [shape=box, style=filled, fillcolor="${colors.bg}", fontname="Arial", fontsize=10, fontcolor="${colors.text}"];`);
        dotLines.push(`\tedge [fontname="Arial", fontsize=8, color="${colors.edge}"];`);
        dotLines.push(``);

        // Build instance graphs recursively
        this.buildInstanceGraph(root, dotLines, colors);

        dotLines.push(`}`);
        return dotLines.join('\n');
    }

    buildInstanceGraph(instance, dotLines, colors) {
        if (instance.entity_type === "node") {
            // Node cluster
            dotLines.push(`\tsubgraph cluster_${instance.unique_id} {`);
            dotLines.push(`\t\tlabel="${instance.name}";`);
            dotLines.push(`\t\tfontcolor="${colors.text}";`);
            if (instance.vis_guide) {
                const guide = instance.vis_guide;
                const color = this.isDarkMode() ? (guide.dark_color || guide.color) : guide.color;
                const bgColor = this.isDarkMode() ? (guide.dark_background_color || guide.background_color) : guide.background_color;
                const textColor = this.isDarkMode() ? (guide.dark_text_color || guide.text_color) : guide.text_color;
                dotLines.push(`\t\tstyle="rounded,filled"; color="${color}"; fillcolor="${bgColor}"; fontcolor="${textColor}";`);
            }
            dotLines.push(`\t\tnode [style=filled, fillcolor="${colors.nodeBg}", fontcolor="${colors.text}"];`);

            // Input ports
            if (instance.in_ports && Array.isArray(instance.in_ports)) {
                instance.in_ports.forEach(port => {
                    if (port && port.unique_id && port.name) {
                        const eventType = port.event ? port.event.type : 'unknown';
                        const eventFreq = port.event && port.event.frequency !== null ? port.event.frequency : 'unknown';
                        dotLines.push(`\t\t${port.unique_id} [label="input/${port.name}\\n#${eventType}\\n@${eventFreq}", shape=ellipse, fillcolor="${colors.input}", fontcolor="${colors.text}"];`);
                    }
                });
            }

            // Output ports
            if (instance.out_ports && Array.isArray(instance.out_ports)) {
                instance.out_ports.forEach(port => {
                    if (port && port.unique_id && port.name) {
                        dotLines.push(`\t\t${port.unique_id} [label="output/${port.name}", shape=ellipse, fillcolor="${colors.output}", fontcolor="${colors.text}"];`);
                    }
                });
            }

            // Events
            if (instance.events && Array.isArray(instance.events)) {
                instance.events.forEach(event => {
                    if (event && event.unique_id && event.name && event.type && event.frequency !== undefined) {
                        const freq = event.frequency !== null ? event.frequency : 'unknown';
                        const shape = event.type === "periodic" ? "diamond" : "box";
                        const mediumColor = instance.vis_guide ? (this.isDarkMode() ? (instance.vis_guide.dark_medium_color || instance.vis_guide.medium_color) : instance.vis_guide.medium_color) : (this.isDarkMode() ? "#444444" : "#cccccc");
                        dotLines.push(`\t\t${event.unique_id} [label="${event.name}\\n#${event.type}\\n@${freq}", shape=${shape}, fillcolor="${mediumColor}", fontcolor="${colors.text}"];`);
                    }
                });
            }

            dotLines.push(`\t}`);

            // Global topic clouds for input ports
            if (instance.in_ports && Array.isArray(instance.in_ports)) {
                instance.in_ports.forEach(port => {
                    if (port && port.unique_id && port.is_global) {
                        const topicPath = Array.isArray(port.topic) ? port.topic.join('/') : port.topic || '';
                        dotLines.push(`\t${port.unique_id}_cloud [label="/${topicPath}", shape=hexagon, style=solid, color="${colors.cloud}", fontcolor="${colors.text}"];`);
                        dotLines.push(`\t${port.unique_id}_cloud -> ${port.unique_id} [color="${colors.cloudEdge}"];`);
                    }
                });
            }

            // Event triggers
            if (instance.events && Array.isArray(instance.events)) {
                instance.events.forEach(event => {
                    if (event && event.unique_id && event.trigger_ids && Array.isArray(event.trigger_ids)) {
                        event.trigger_ids.forEach(triggerId => {
                            if (triggerId) {
                                dotLines.push(`\t${triggerId} -> ${event.unique_id} [color="${colors.cloudEdge}"];`);
                            }
                        });
                    }
                });
            }

            // Output port triggers
            if (instance.out_ports && Array.isArray(instance.out_ports)) {
                instance.out_ports.forEach(port => {
                    if (port && port.event && port.event.unique_id && port.event.trigger_ids && Array.isArray(port.event.trigger_ids)) {
                        port.event.trigger_ids.forEach(triggerId => {
                            if (triggerId) {
                                dotLines.push(`\t${triggerId} -> ${port.event.unique_id} [color="${colors.cloudEdge}"];`);
                            }
                        });
                    }
                });
            }

            // Input port triggers
            if (instance.in_ports && Array.isArray(instance.in_ports)) {
                instance.in_ports.forEach(port => {
                    if (port && port.event && port.event.unique_id && port.event.trigger_ids && Array.isArray(port.event.trigger_ids)) {
                        port.event.trigger_ids.forEach(triggerId => {
                            if (triggerId) {
                                dotLines.push(`\t${triggerId} -> ${port.event.unique_id} [color="${colors.cloudEdge}"];`);
                            }
                        });
                    }
                });
            }

        } else if (instance.entity_type === "module" || instance.entity_type === "system") {
            // Recursively process children for modules and systems
            if (instance.children && Array.isArray(instance.children)) {
                instance.children.forEach(child => {
                    if (child) {
                        this.buildInstanceGraph(child, dotLines, colors);
                    }
                });
            }
        }
    }

    async renderLogicDiagram(dotSyntax) {
        console.log('Rendering logic diagram with DOT syntax length:', dotSyntax.length);
        console.log('Graphviz available:', this.isGraphvizLoaded());

        if (!this.isGraphvizLoaded()) {
            throw new Error('viz.js library not loaded');
        }

        try {
            // Clear container
            this.container.innerHTML = '';
            this.container.className = 'logic-diagram-container';

            // Render DOT using viz.js
            console.log('Rendering DOT with viz.js...');

            // Render SVG using viz.js
            const viz = new window.Viz();
            const svgString = await viz.renderString(dotSyntax, { format: 'svg', engine: 'dot' });
            console.log('SVG generated, length:', svgString.length);

            // Validate SVG string
            if (!svgString || svgString.trim().length === 0) {
                throw new Error('Generated SVG is empty');
            }

            // Create a container for the SVG
            const svgContainer = document.createElement('div');
            svgContainer.style.width = '100%';
            svgContainer.style.height = '100%';
            svgContainer.innerHTML = svgString;

            // Get the SVG element
            const svg = svgContainer.querySelector('svg');
            if (svg) {
                // Ensure SVG has proper dimensions
                // Always set to 100% to prevent the SVG's fixed size from affecting the parent layout (e.g. shrinking sidebar)
                svg.setAttribute('width', '100%');
                svg.setAttribute('height', '100%');

                // Add the container to DOM first
                this.container.appendChild(svgContainer);

                // Add interaction handlers
                this.addInteractionHandlers(svg);

                // Initialize pan and zoom on the SVG element after it's in the DOM
                setTimeout(() => {
                    try {
                        this.initPanZoom(svg);
                    } catch (panZoomError) {
                        console.warn('Failed to initialize pan-zoom, continuing without it:', panZoomError);
                    }
                }, 50);
            } else {
                this.container.appendChild(svgContainer);
            }

        } catch (error) {
            console.error('Error rendering logic diagram with viz.js, falling back to text display:', error);

            // Fallback: display DOT syntax as formatted text
            this.container.innerHTML = '';
            this.container.className = 'logic-diagram-container';

            const pre = document.createElement('pre');
            pre.style.cssText = `
                background: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 4px;
                padding: 15px;
                margin: 0;
                font-family: 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.4;
                white-space: pre-wrap;
                word-wrap: break-word;
                max-height: 600px;
                overflow-y: auto;
            `;

            pre.textContent = `// Logic Diagram DOT Syntax\n// Rendering failed: ${error.message}\n\n${dotSyntax}`;
            this.container.appendChild(pre);
        }
    }

    addInteractionHandlers(svgElement) {
        // Add click handlers to nodes and edges
        const nodes = svgElement.querySelectorAll('.node');
        const edges = svgElement.querySelectorAll('.edge');
        const clusters = svgElement.querySelectorAll('.cluster');

        // Handle node clicks
        nodes.forEach(node => {
            node.classList.add('logic-diagram-node');
            node.style.cursor = 'pointer';
            node.addEventListener('click', (e) => {
                e.stopPropagation();
                this.handleElementClick(node, 'Node');
            });
        });

        // Handle edge clicks
        edges.forEach(edge => {
            edge.classList.add('logic-diagram-edge');
            edge.style.cursor = 'pointer';
            edge.addEventListener('click', (e) => {
                e.stopPropagation();
                this.handleElementClick(edge, 'Edge');
            });
        });

        // Handle cluster clicks
        clusters.forEach(cluster => {
            cluster.classList.add('logic-diagram-cluster');
            cluster.style.cursor = 'pointer';
            cluster.addEventListener('click', (e) => {
                e.stopPropagation();
                this.handleElementClick(cluster, 'Cluster');
            });
        });
    }

    handleElementClick(element, type) {
        // Remove previous highlights
        this.container.querySelectorAll('.highlighted').forEach(el => {
            el.classList.remove('highlighted');
        });

        // Highlight clicked element
        element.classList.add('highlighted');

        // Extract element information
        const title = element.querySelector('title');
        const label = element.querySelector('text') || element;
        const elementInfo = {
            type: type,
            label: label ? label.textContent : 'Unknown',
            title: title ? title.textContent : ''
        };

        // Update info panel
        this.updateInfoPanel(elementInfo, type);
    }

    initPanZoom(svg) {
        if (this.panZoomInstance) {
            this.panZoomInstance.destroy();
        }

        // Validate SVG before initializing pan-zoom
        const bbox = svg.getBoundingClientRect();
        if (bbox.width === 0 || bbox.height === 0) {
            console.warn('SVG has zero dimensions, skipping pan-zoom initialization');
            return;
        }

        try {
            // Initialize svg-pan-zoom
            this.panZoomInstance = svgPanZoom(svg, {
                zoomEnabled: true,
                controlIconsEnabled: true,
                fit: false, // We'll handle fitting manually
                center: false, // We'll handle centering manually
                minZoom: 0.01,
                maxZoom: 10,
                zoomScaleSensitivity: 0.2
            });

            // Wait for SVG to be fully rendered, then fit to view
            this.fitDiagramToView(svg);
        } catch (initError) {
            console.warn('Failed to initialize svg-pan-zoom:', initError);
            this.panZoomInstance = null;
        }
    }

    fitDiagramToView(svg) {
        // Use multiple attempts with increasing delays to ensure SVG is fully rendered
        const fitAttempts = [100, 250, 500];

        fitAttempts.forEach((delay, index) => {
            setTimeout(() => {
                if (!this.panZoomInstance) return;

                try {
                    // Update cached dimensions
                    this.panZoomInstance.resize();
                    
                    const sizes = this.panZoomInstance.getSizes();
                    if (sizes.width === 0 || sizes.height === 0) {
                        return;
                    }

                    // Fit and center
                    this.panZoomInstance.fit();
                    this.panZoomInstance.center();

                    // Optional: Add some padding (90%) and cap at 100%
                    let currentZoom = this.panZoomInstance.getZoom();
                    let desiredZoom = currentZoom * 0.9;
                    
                    if (desiredZoom > 1) {
                        desiredZoom = 1;
                    }
                    
                    // Apply zoom and re-center
                    this.panZoomInstance.zoom(desiredZoom);
                    this.panZoomInstance.center();

                    console.log(`Diagram fitted to view (attempt ${index + 1}): zoom=${desiredZoom.toFixed(3)}`);
                } catch (fitError) {
                    console.warn(`Failed to fit diagram (attempt ${index + 1}):`, fitError);
                }
            }, delay);
        });
    }

    updateTheme() {
        if (window.logicDiagramData && window.logicDiagramData[this.options.mode]) {
            const data = window.logicDiagramData[this.options.mode];
            const dotSyntax = this.generateDotSyntax(data);
            this.renderLogicDiagram(dotSyntax);
        }
    }

    destroy() {
        // Clean up pan zoom instance
        if (this.panZoomInstance) {
            this.panZoomInstance.destroy();
            this.panZoomInstance = null;
        }
        super.destroy();
    }
}

// Export for use in the overview page
window.LogicDiagramModule = LogicDiagramModule;
