// Launch activity: the launch part of the top bar, and four views of the
// launched mode as rail items. Control is the state map, with launch and
// shutdown of each compute unit in its section head, the actions on the
// selected member above it and resource marks on its blocks; Logs is the
// member tree beside the selected member's log, read only; Topics is the live
// rate of every topic against the design; Resources is the map sized by CPU,
// memory or GPU use. Escape drops a pending confirmation, else the selection, also on
// the other activities' diagrams through `selection.clear`.
(function () {
  const SD = window.SD;
  const { element, mainView } = SD.widgets;
  const {
    launchControl,
    launchBar,
    stateMonitor,
    stateMap,
    memberActions,
    topicRates,
    resourceFeed,
    resourceMap,
  } = SD.activities.launch;

  // Main views by the store's `view`; Control leads the rail as the Launch group.
  const VIEWS = [
    {
      value: "control",
      label: "Control",
      group: "Launch",
      order: 5,
      icon: "M3 3h14v14H3zM3 9h14M9 9v8",
    },
    {
      value: "logs",
      label: "Logs",
      icon: "M3 4h14v12H3zM6 8l2.5 2L6 12M10.5 12H14",
    },
    {
      value: "topics",
      label: "Topics",
      icon: "M2 10h4l2-5 4 10 2-5h4",
    },
    {
      value: "resources",
      label: "Resources",
      icon: "M3 17V9h4v8M8 17V3h4v14M13 17v-6h4v6",
    },
  ];
  const DEFAULT_VIEW = "control";

  // The page's launch control, shared by the bar and the views.
  let shared = null;
  function controlOf({ config, store, daemon }) {
    shared ||= launchControl(config, store, daemon);
    return shared;
  }

  SD.workbench.registerActivity({
    id: "launch",
    title: "Launch",
    group: "Monitor",
    order: 20,
    daemon: false,
    side: false,
    inspector: false,
    views: () => ({ key: "view", fallback: DEFAULT_VIEW, items: VIEWS }),
    bar(slots, context) {
      launchBar(slots, controlOf(context), context);
    },
    mount({ main }, context) {
      const { config, store } = context;
      const control = controlOf(context);
      const resources = resourceFeed(config, store);

      // -- main: the state map with the unit controls, and the member logs
      const controlView = mainView({ title: "Control" });
      const legend = SD.RunState.legend("control-legend");
      const { rateLabel, BUSY_LOG_BPS } = SD.activities.launch;
      [
        [
          element("span", "log-busy"),
          "busy log",
          `output growing by ${rateLabel(BUSY_LOG_BPS)} or more`,
        ],
        [
          element("span", "restart-glyph", "\u21bbn"),
          "restarts",
          "times the member was restarted or respawned",
        ],
        [
          element("span", "resource-glyph", "C M G"),
          "resources",
          "CPU, memory and GPU memory use of the process over the window, " +
            "one to three bars from 25 % / 50 % / 1 core of CPU and " +
            "2 % / 5 % / 10 % of the device's memory",
        ],
      ].forEach(([glyph, label, meaning]) => {
        const item = element("span", "state-legend-item");
        item.title = meaning;
        item.append(glyph, element("span", "", label));
        legend.appendChild(item);
      });
      controlView.trailing.appendChild(legend);
      const undrawnUnits = element("div", "control-undrawn-units");
      controlView.body.appendChild(undrawnUnits);
      const map = stateMap(controlView.body, config, store, {
        unitControls: control.unitControls,
        resources,
        onOpen: () => store.set("view", "logs"),
        onLoad: () => {
          renderUnits();
          memberBar.render();
        },
      });
      const memberBar = memberActions(config, store, {
        entityOf: (key) => map.entityOf(key),
        containerOf: (entity) => map.containerOf(entity),
      });
      controlView.element.insertBefore(memberBar.element, controlView.body);
      const logsView = mainView({ title: "Logs" });
      const monitorPane = element("div", "split-h");
      logsView.body.appendChild(monitorPane);
      const topicsView = mainView({ title: "Topics" });
      const topicsPane = element("div", "topic-pane");
      topicsView.body.appendChild(topicsPane);
      const resourcesView = mainView({ title: "Resources" });
      const resourcesPane = element("div", "resource-pane");
      resourcesView.body.appendChild(resourcesPane);
      main.append(
        controlView.element,
        logsView.element,
        topicsView.element,
        resourcesView.element,
      );
      const monitor = stateMonitor(monitorPane, config, store);
      const topics = topicRates(topicsPane, config, store);
      const resourceView = resourceMap(resourcesPane, config, store, resources);
      topicsView.head.insertBefore(topics.runSelector, topicsView.trailing);
      logsView.head.insertBefore(monitor.runSelector, logsView.trailing);
      // The activity's slots are visible from mount until another activity takes over.
      let shown = true;

      // Shows the store's view; the other one stops following and laying out.
      function showView() {
        const current = store.get("view");
        controlView.element.hidden = current !== "control";
        logsView.element.hidden = current !== "logs";
        topicsView.element.hidden = current !== "topics";
        resourcesView.element.hidden = current !== "resources";
        if (shown && current === "control") map.show();
        else map.hide();
        if (shown && current === "logs") monitor.show();
        else monitor.hide();
        if (shown && current === "topics") topics.show();
        else topics.hide();
        if (shown && current === "resources") resourceView.show();
        else resourceView.hide();
      }

      // The map's unit controls, and above it the units with a launcher that
      // the map draws no section for.
      function renderUnits() {
        map.renderControls();
        const drawn = map.drawnUnits();
        undrawnUnits.replaceChildren();
        (drawn ? control.rows() : []).forEach((row) => {
          if (drawn.includes(row.unit)) return;
          const controls = control.unitControls(row.unit);
          if (!controls) return;
          const entry = element("div", "control-undrawn-unit");
          entry.append(element("span", "launch-unit", row.unit), controls);
          undrawnUnits.appendChild(entry);
        });
        undrawnUnits.hidden = !undrawnUnits.childElementCount;
      }

      store.onChange("view", showView);
      // Captured ahead of the popups' own Escape, so one that closes a popup does nothing here.
      document.addEventListener(
        "keydown",
        (event) => {
          if (event.key !== "Escape" || !shown || event.defaultPrevented)
            return;
          if (
            document.querySelector(
              ".popup-overlay.open, #settings-overlay.open",
            )
          )
            return;
          if (event.target.closest?.("input, textarea, select")) return;
          if (memberBar.cancel()) return;
          store.set("entity", "");
          SD.emit("selection.clear");
        },
        true,
      );
      SD.on("launch.control", renderUnits);
      SD.on("daemon.health", () => memberBar.render());
      renderUnits();
      showView();
      return {
        show() {
          shown = true;
          showView();
        },
        hide() {
          shown = false;
          showView();
        },
      };
    },
  });
})();
