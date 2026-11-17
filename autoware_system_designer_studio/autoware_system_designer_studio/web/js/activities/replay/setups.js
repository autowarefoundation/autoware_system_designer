// Replay: the saved setups above the scope selector. A setup is a replay
// saved by name through the daemon, in the replay setups of its config root:
// the system, mode, deploy and bag, the configuration and the run options.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const LIST = "replay.setups";
  const GET = "replay.setup.get";
  const SAVE = "replay.setup.save";
  const DELETE = "replay.setup.delete";
  // Saved setups: a list to load or delete from and
  // a name to save the shown replay as. `capture()` gives the shown setup or
  // { blocker }; `apply(setup)` shows a loaded one and gives what it could not
  // take, as text, or "".
  function setupBar(daemon, capture, apply) {
    const select = document.createElement("select");
    select.className = "replay-setup-select";
    select.setAttribute("aria-label", "Saved replay setup");
    const load = element("button", "panel-button", "Load");
    const remove = element("button", "panel-button", "Delete");
    const name = element("input", "panel-search replay-setup-name");
    name.type = "text";
    name.placeholder = "Setup name";
    name.setAttribute("aria-label", "Setup name");
    const save = element("button", "panel-button", "Save");
    [load, remove, save].forEach((button) => (button.type = "button"));
    load.title =
      "Show the chosen setup: scope, inputs, outputs, bag and run options";
    remove.title = "Delete the chosen setup file";
    save.title =
      "Save the shown scope, configuration, bag and run options under this name";
    const loadRow = element("div", "replay-setup-row");
    loadRow.append(select, load, remove);
    const saveRow = element("div", "replay-setup-row");
    saveRow.append(name, save);
    const note = element("p", "panel-note replay-setup-note");
    const group = SD.widgets.section("Saved setups", loadRow, saveRow, note);

    let setups = [];
    let busy = false;

    const offered = () => !!daemon.health && daemon.offers(LIST);

    function say(text, blocked = false) {
      note.textContent = text;
      note.classList.toggle("blocked", blocked);
    }

    function render() {
      group.hidden = !offered();
      const usable = daemon.connected && !busy;
      const chosen = setups.find((s) => s.name === select.value);
      load.disabled = !usable || !chosen || !!chosen.error;
      remove.disabled = !usable || !chosen;
      const { blocker } = capture();
      save.disabled = !usable || !name.value.trim() || !!blocker;
      save.title =
        blocker ||
        "Save the shown scope, configuration, bag and run options under this name";
    }

    function label(setup) {
      if (setup.error) return `${setup.name} (unreadable)`;
      const where = [
        setup.mode,
        `${setup.scope.length} path${setup.scope.length === 1 ? "" : "s"}`,
      ];
      return `${setup.name} · ${where.filter(Boolean).join(", ")}`;
    }

    function refresh(keep = select.value) {
      if (!offered() || !daemon.connected) {
        render();
        return Promise.resolve();
      }
      return daemon.rpc(LIST).then(
        (result) => {
          setups = result.setups;
          SD.widgets.setOptions(select, [
            ["", setups.length ? "Choose" : "None saved"],
            ...setups.map((setup) => [setup.name, label(setup)]),
          ]);
          select.value = setups.some((s) => s.name === keep) ? keep : "";
          select.title = result.dir;
          render();
        },
        (error) => {
          setups = [];
          SD.widgets.setOptions(select, [["", "None saved"]]);
          say(error.message, true);
          render();
        },
      );
    }

    function run(promise) {
      busy = true;
      render();
      return promise.finally(() => {
        busy = false;
        render();
      });
    }

    load.addEventListener("click", () =>
      run(
        daemon.rpc(GET, { name: select.value }).then(
          (result) => {
            name.value = result.name;
            const missed = apply(result.setup);
            say(
              missed
                ? `Loaded ${result.name}; ${missed}`
                : `Loaded ${result.name}`,
              !!missed,
            );
          },
          (error) => say(error.message, true),
        ),
      ),
    );
    save.addEventListener("click", () => {
      const { setup } = capture();
      const target = name.value.trim();
      if (!setup || !target) return;
      if (
        setups.some((s) => s.name === target) &&
        !window.confirm(`Replace the saved setup ${target}?`)
      )
        return;
      run(
        daemon.rpc(SAVE, { name: target, setup }).then(
          (result) => {
            say(`Saved ${result.name}`);
            return refresh(result.name);
          },
          (error) => say(error.message, true),
        ),
      );
    });
    remove.addEventListener("click", () => {
      const target = select.value;
      if (!target || !window.confirm(`Delete the saved setup ${target}?`))
        return;
      run(
        daemon.rpc(DELETE, { name: target }).then(
          () => {
            say(`Deleted ${target}`);
            return refresh("");
          },
          (error) => say(error.message, true),
        ),
      );
    });
    select.addEventListener("change", () => {
      const chosen = setups.find((s) => s.name === select.value);
      if (chosen) name.value = chosen.name;
      say(chosen?.error || "", !!chosen?.error);
      render();
    });
    name.addEventListener("input", render);
    SD.on("settings.storage", () => {
      say("");
      refresh("");
    });
    SD.on("daemon.health", () => refresh());
    SD.on("daemon.connection", ({ connected }) => {
      if (connected) refresh();
      else render();
    });
    refresh();
    return { element: group, render };
  }

  replay.setups = { setupBar };
})();
