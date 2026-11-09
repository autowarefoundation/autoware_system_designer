// Explore: depth switch for the diagrams that draw the design hierarchy cut
// at a level. Level 1 holds the top-level components, the deepest level of
// the mode's structure the nodes. The store's `depth` names the level; empty
// is the deepest, so the full view carries over to modes of another depth.
(function () {
  const SD = window.SD;
  const { element, section, segmented } = SD.widgets;
  const explore = (SD.activities.explore ||= {});

  // Number of levels below `instance`.
  function structureDepth(instance, depth = 0) {
    if (!instance?.children?.length) return depth;
    return Math.max(
      ...instance.children.map((child) => structureDepth(child, depth + 1)),
    );
  }

  // Level the store's `value` names in a structure `levels` deep.
  function effectiveDepth(value, levels) {
    const chosen = parseInt(value, 10);
    return chosen > 0 ? Math.min(chosen, levels) : levels;
  }

  function depthSelector(config, store) {
    const control = element("div", "depth-levels");
    const group = section("Depth", control);
    let levels = 0;
    let switcher = null;

    function mark() {
      switcher?.mark(String(effectiveDepth(store.get("depth"), levels)));
    }

    function render() {
      const options = Array.from({ length: levels }, (_, i) => {
        const level = String(i + 1);
        return [level, level];
      });
      switcher = segmented(options, (value) =>
        store.set("depth", Number(value) >= levels ? "" : value),
      );
      control.replaceChildren(switcher.element);
      mark();
    }

    async function loadMode() {
      const data = await SD.NamespaceTree.loadStructure(
        config.dataBase,
        store.get("mode"),
      );
      levels = Math.max(1, structureDepth(data));
      render();
    }

    const reload = () =>
      loadMode().catch((error) => console.warn("explore depth:", error));
    store.onChange("mode", reload);
    store.onChange("depth", mark);
    reload();
    return group;
  }

  depthSelector.structureDepth = structureDepth;
  depthSelector.effectiveDepth = effectiveDepth;
  explore.depthSelector = depthSelector;
})();
