// Explore activity: the diagram list, entity search and depth switch beside
// the diagram canvas, with the inspector showing the selected element. Search
// is offered for diagrams that locate entities, the depth switch for diagrams
// that draw the hierarchy cut at a level. With a daemon, the live state of the
// launched members overlays the canvas.
(function () {
  const SD = window.SD;
  const { element, section, list } = SD.widgets;
  const { diagramHost, entitySearch, depthSelector, liveOverlay } =
    SD.activities.explore;

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
      store.define("depth", "", (v) => /^[1-9]\d*$/.test(v));
      store.syncUrl();

      const host = diagramHost(main, inspectorView(inspector), config, store);
      const diagrams = list(
        config.diagrams.map((d) => [d.type, d.label]),
        (type) => store.set("diagram", type),
      );
      const search = entitySearch(config, store, (id) => host.focus(id));
      const depth = depthSelector(config, store);
      const live = liveOverlay(config, store, host, () => main.hidden);
      side.append(
        section("Diagrams", diagrams.element),
        depth,
        search,
        live.element,
      );

      function showDiagramOptions() {
        const current = store.get("diagram");
        const diagram = config.diagrams.find((d) => d.type === current);
        diagrams.mark(current);
        search.hidden = !diagram?.searchable;
        depth.hidden = !diagram?.layered;
      }

      // The diagram locates the store's entity when it loads and on show; a
      // change while shown comes from this canvas and is already in place.
      let shownKey = "";
      function followEntity() {
        const key = store.get("entity");
        if (!key || key === shownKey) return;
        shownKey = key;
        host.focusKey(key).catch((error) => console.warn("explore:", error));
      }
      function load() {
        shownKey = "";
        host.load().then(followEntity);
      }

      // The canvas lays out only while visible; a change made while hidden reloads on the next show.
      let stale = false;
      const reload = () => {
        if (main.hidden) stale = true;
        else load();
      };
      store.onChange("diagram", () => {
        showDiagramOptions();
        reload();
      });
      store.onChange("mode", reload);
      store.onChange("depth", () => {
        if (
          config.diagrams.find((d) => d.type === store.get("diagram"))?.layered
        )
          reload();
      });
      store.onChange("entity", (key) => {
        if (!main.hidden) shownKey = key;
      });
      SD.on("themechange", host.updateTheme);

      showDiagramOptions();
      load();
      return {
        show() {
          live.show();
          if (stale) {
            stale = false;
            load();
          } else followEntity();
        },
      };
    },
  });
})();
