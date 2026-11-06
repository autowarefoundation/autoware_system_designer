// Explore activity: the diagram list and entity search beside the diagram
// canvas, with the inspector showing the selected element. Search is offered
// for diagrams that locate entities.
(function () {
  const SD = window.SD;
  const { element, section, list } = SD.widgets;
  const { diagramHost, entitySearch } = SD.activities.explore;

  // Inspector view: the selected element's info cards, or a prompt when nothing is selected.
  function inspectorView(slot) {
    const empty = element(
      "p",
      "panel-empty",
      "Select an element on the diagram.",
    );
    const infoPanel = element("div", "info-panel");
    infoPanel.hidden = true;
    slot.append(empty, infoPanel);
    return {
      reset() {
        infoPanel.hidden = true;
        empty.hidden = false;
      },
      show(data, type) {
        infoPanel.hidden = false;
        empty.hidden = true;
        SD.InfoPanel.render(infoPanel, data, type);
      },
    };
  }

  SD.workbench.registerActivity({
    id: "explore",
    title: "Explore",
    order: 10,
    daemon: false,
    inspector: true,
    icon: "M3 4h6v4H3zM11 12h6v4h-6zM6 8v6h5",
    mount({ side, main, inspector }, { config, store }) {
      const types = config.diagrams.map((d) => d.type);
      store.define("diagram", config.defaultDiagram, (v) => types.includes(v));
      store.syncUrl();

      const host = diagramHost(main, inspectorView(inspector), config, store);
      const diagrams = list(
        config.diagrams.map((d) => [d.type, d.label]),
        (type) => store.set("diagram", type),
      );
      const search = entitySearch(config, store, (id) => host.focus(id));
      side.append(section("Diagrams", diagrams.element), search);

      function showDiagramOptions() {
        const current = store.get("diagram");
        diagrams.mark(current);
        search.hidden = !config.diagrams.find((d) => d.type === current)
          ?.searchable;
      }

      // The canvas lays out only while visible; a change made while hidden reloads on the next show.
      let stale = false;
      const reload = () => {
        if (main.hidden) stale = true;
        else host.load();
      };
      store.onChange("diagram", () => {
        showDiagramOptions();
        reload();
      });
      store.onChange("mode", reload);
      SD.on("themechange", host.updateTheme);

      showDiagramOptions();
      host.load();
      return {
        show() {
          if (!stale) return;
          stale = false;
          host.load();
        },
      };
    },
  });
})();
