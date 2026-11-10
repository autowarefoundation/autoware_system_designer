// Launch: the treemap of the mode, coloured and marked by the live states of
// the system's launched members, their busy logs and restart counts, with
// the caller's controls of each compute unit in its section head. A click on
// a block selects its node or module as the store's `entity`, shared with
// the log view and Explore, and a unit's head clears it; the map marks the
// store's entity, none once it is cleared. A double click on a node opens it
// there through `onOpen`. The map lays out only while shown: a mode change made while
// hidden loads on the next show.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const launch = (SD.activities.launch ||= {});

  // Member events arrive in bursts at launch; one apply per burst.
  const APPLY_DELAY_MS = 80;

  function stateMap(container, config, store, hooks = {}) {
    const { unitControls, onOpen, onLoad } = hooks;
    const host = element("div", "diagram-container");
    container.appendChild(host);
    let module = null;
    // Entities of the loaded mode, composables nested under their container.
    let entities = [];
    let entitiesMode = null;
    let hidden = true;
    let stale = true;
    let loadSeq = 0;
    let settled = false;
    let timer = null;

    // Live states of the mode's nodes, each with the growth of its own output
    // (`rate`) and, from the busy threshold up, a note of it (`busy`); a
    // composable node's output is its container's.
    function liveStates() {
      const { logRate } = launch.scope(config.systemId, () => entitiesMode);
      const states = launch.memberStates(
        entities,
        config.systemId,
        entitiesMode,
      );
      entities.forEach((entity) => {
        const state = states.get(entity.member_path);
        if (!state || entity.container_path) return;
        const rate = logRate(entity.member_path);
        state.rate = rate;
        state.busy = launch.busyLog(rate)
          ? `log ${launch.rateLabel(rate)}`
          : "";
      });
      return states;
    }

    function applyStates() {
      timer = null;
      module?.setLiveStates(liveStates());
    }

    function schedule() {
      if (timer || hidden) return;
      timer = setTimeout(applyStates, APPLY_DELAY_MS);
    }

    // Marks the store's entity on the map.
    function followEntity() {
      const key = store.get("entity");
      const entity = key && entities.find((e) => e.key === key);
      if (entity) module?.focusEntity(String(entity.unique_id));
      else module?.clearSelection();
    }

    async function load() {
      const seq = ++loadSeq;
      stale = false;
      settled = false;
      module?.destroy();
      module = null;
      const canvas = element("div", "diagram-canvas");
      host.replaceChildren(canvas);
      try {
        const mode = store.get("mode");
        const data = await SD.NamespaceTree.loadStructure(
          config.dataBase,
          mode,
        );
        if (seq !== loadSeq) return;
        entities = launch.nestComposables(SD.NamespaceTree.entities(data));
        entitiesMode = mode;
        const created = new (SD.diagrams.get("treemap"))(canvas, {
          mode,
          dataBase: config.dataBase,
          unitControls,
          onInfoUpdate(_data, _type, entity) {
            store.set(
              "entity",
              entity?.path ? SD.NamespaceTree.entityKey(entity) : "",
            );
          },
        });
        await created.ready;
        if (seq !== loadSeq) {
          created.destroy();
          return;
        }
        module = created;
        applyStates();
        followEntity();
      } catch (error) {
        console.error("launch map:", error);
        host.replaceChildren(
          element(
            "div",
            "diagram-error",
            `Error loading the map: ${error.message}`,
          ),
        );
      }
      if (seq !== loadSeq) return;
      settled = true;
      onLoad?.();
    }

    host.addEventListener("dblclick", (event) => {
      if (event.target.closest?.('.tm-block[data-kind="node"]')) onOpen?.();
    });
    SD.on("launch.member", schedule);
    SD.on("launch.sessions", schedule);
    SD.on("launch.activity", schedule);
    store.onChange("entity", () => {
      if (!hidden) followEntity();
    });
    store.onChange("mode", () => {
      if (hidden) stale = true;
      else load();
    });

    return {
      show() {
        hidden = false;
        if (stale) load();
        else {
          applyStates();
          followEntity();
        }
      },
      hide() {
        hidden = true;
      },
      // Refills the unit controls on the map.
      renderControls() {
        module?.renderUnitControls();
      },
      // The loaded entity of a selection key, or null.
      entityOf(key) {
        return entities.find((e) => e.key === key) || null;
      },
      // The container entity of a composable node, or null.
      containerOf(entity) {
        return (
          entities.find(
            (e) =>
              e.entity_type === "node" &&
              !e.container_path &&
              e.member_path === entity.container_path,
          ) || null
        );
      },
      // Names of the units the map draws a section for; null until a load settled.
      drawnUnits() {
        if (!settled) return null;
        return module ? module.unitNames() : [];
      },
    };
  }

  launch.stateMap = stateMap;
})();
