// Explore: depth switch for the diagrams that draw the design hierarchy cut
// at a level. Level 1 holds the top-level components, the deepest level of
// the mode's structure the nodes. The switch offers up to five levels; a
// deeper structure ends with N, the deepest. The store's `depth` names the
// level; empty is the deepest, so the full view carries over to modes of
// another depth.
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

  const ALL = "";
  const MAX_OPTIONS = 5;

  // Switch value for the store's `value`: a level above the deepest, or
  // `ALL` for the deepest.
  function switchValue(value, levels) {
    const chosen = parseInt(value, 10);
    return chosen > 0 && chosen < levels ? String(chosen) : ALL;
  }

  // [value, label] pairs of a structure `levels` deep: the levels above the
  // last option, then the deepest as its own number or N when the structure
  // is deeper than the switch.
  function switchOptions(levels) {
    const count = Math.min(levels, MAX_OPTIONS);
    const options = Array.from({ length: count - 1 }, (_, i) => {
      const level = String(i + 1);
      return [level, level];
    });
    options.push([ALL, levels > MAX_OPTIONS ? "N" : String(levels)]);
    return options;
  }

  function depthSelector(config, store) {
    const control = element("div", "depth-levels");
    const group = section("Depth", control);
    let levels = 0;
    let switcher = null;

    function mark() {
      switcher?.mark(switchValue(store.get("depth"), levels));
    }

    function render() {
      switcher = segmented(switchOptions(levels), (value) =>
        store.set("depth", value),
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
  depthSelector.switchValue = switchValue;
  depthSelector.switchOptions = switchOptions;
  explore.depthSelector = depthSelector;
})();
