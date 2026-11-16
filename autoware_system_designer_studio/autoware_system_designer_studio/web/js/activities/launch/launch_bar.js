// Launch: the launch part of the top bar, shown on every activity. The bar
// holds the drawer toggle, launch of every unit of the shown mode and deploy,
// shutdown of every launched unit, the launch state of the daemon with the
// system and mode launched over one mark per compute unit, and the last
// control errors. Its
// drawer holds the deploy, the launch options, the RViz start on the daemon
// and one row per compute unit with the unit's state and controls; the launch
// commands of the units open from it in a popup, by launch method.
(function () {
  const SD = window.SD;
  const { element, segmented, copyButton, popup, field, boundSelect } =
    SD.widgets;
  const launch = (SD.activities.launch ||= {});

  const METHOD_NOTES = {
    native:
      "Runs the system structure under the designer runtime supervisor, one supervised process per node.",
    xml: "Runs the exported XML launcher through ros2 launch.",
  };
  const OPTIONS = [
    [
      "measure",
      "Measure from launch",
      "Open the latency measurement window once every member started; " +
        "without it a window opens on Start measure.",
    ],
    ["respawn", "Respawn exited processes", ""],
  ];
  const DRAWER_ICON = "M4 7l6 6 6-6";
  const COMMANDS_ICON =
    "M2 4a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V4zm2 1v10h12V5H4zm2.3 2.3L9 10l-2.7 2.7-1-1L7 10 5.3 8.3l1-1zM10 12h4v1.5h-4V12z";
  const RVIZ_ICON =
    "M10 4C5.5 4 2 10 2 10s3.5 6 8 6 8-6 8-6-3.5-6-8-6zm0 9.5a3.5 3.5 0 1 1 0-7 3.5 3.5 0 0 1 0 7zM10 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4z";
  const RVIZ_KIND = "rviz";

  function button(label, className, onClick) {
    const node = element("button", `panel-button ${className}`.trim(), label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function table(className, labels) {
    const root = element("table", className);
    const headRow = element("tr");
    labels.forEach((label) => headRow.appendChild(element("th", "", label)));
    const head = element("thead");
    head.appendChild(headRow);
    const body = element("tbody");
    root.append(head, body);
    return { element: root, body };
  }

  function launchBar(slots, control, { config, store, daemon }) {
    // -- bar: the drawer toggle, launch and shutdown of every unit, the state
    const root = element("div", "launch-bar");
    const toggle = element("button", "topbar-icon-btn launch-bar-toggle");
    toggle.type = "button";
    toggle.title = "Launch options and compute units";
    toggle.setAttribute("aria-label", toggle.title);
    toggle.setAttribute("aria-haspopup", "true");
    toggle.setAttribute("aria-expanded", "false");
    toggle.appendChild(SD.workbench.svgIcon(DRAWER_ICON));
    const state = element("span", "launch-bar-state");
    const launchAll = button("Launch", "", () =>
      control.launchable().forEach(control.launchUnit),
    );
    const shutdownAll = button("Shutdown", "", () =>
      control.stoppableLaunched().forEach(control.shutdownUnit),
    );
    const controls = element("div", "unit-controls");
    controls.append(launchAll, shutdownAll);
    const error = element("span", "launch-error launch-bar-error");
    root.append(toggle, controls, state, error);
    slots.bar.appendChild(root);

    // -- commands popup: the launch method, then one command per unit
    const commands = popup("Launch commands");
    const methodSwitch = segmented(control.methods, control.setMethod);
    const methodRow = element("div", "panel-row");
    methodRow.append(
      element("span", "panel-row-label", "Command"),
      methodSwitch.element,
    );
    const methodNote = element("p", "panel-note");
    const commandTable = table("launch-table", ["Compute unit", "Command", ""]);
    const commandsEmpty = element("p", "main-empty");
    commands.body.append(
      methodRow,
      methodNote,
      commandTable.element,
      commandsEmpty,
    );

    // -- drawer: deploy, options, the commands popup and RViz, then the units
    const drawer = element("div", "launch-drawer");
    const optionRow = element("div", "launch-drawer-row");
    const checks = OPTIONS.map(([name, label, title]) => {
      const wrapper = element("label", "panel-check");
      wrapper.title = title;
      const input = element("input");
      input.type = "checkbox";
      input.checked = control.option(name);
      input.addEventListener("change", () =>
        control.setOption(name, input.checked),
      );
      wrapper.append(input, element("span", "", label));
      return wrapper;
    });
    const rvizError = element("span", "launch-error");
    rvizError.hidden = true;
    const rvizButton = button("Launch RViz", "panel-icon-button", () => {
      rvizError.hidden = true;
      rvizButton.disabled = true;
      daemon
        .rpc("rviz.start")
        .then((job) => daemon.upsertJob(job))
        .catch((error) => {
          rvizError.textContent = error.message;
          rvizError.hidden = false;
        })
        .then(renderRviz);
    });
    rvizButton.prepend(SD.workbench.svgIcon(RVIZ_ICON));
    const commandsButton = button(
      "Launch commands",
      "panel-icon-button",
      () => {
        popover.close();
        renderCommands();
        commands.open();
      },
    );
    commandsButton.prepend(SD.workbench.svgIcon(COMMANDS_ICON));
    const tools = element("div", "launch-drawer-tools");
    tools.append(rvizError, commandsButton, rvizButton);
    if (config.deployNames.length)
      optionRow.appendChild(
        field(
          "Deploy",
          boundSelect(
            "deploy",
            config.deployNames.map((name) => [name, name]),
          ),
        ),
      );
    optionRow.append(...checks, tools);
    const unitTable = table("launch-table", [
      "Compute unit",
      "Status",
      "Control",
    ]);
    const unitsEmpty = element("p", "main-empty");
    drawer.append(optionRow, unitTable.element, unitsEmpty);
    slots.drawer.appendChild(drawer);
    const popover = SD.popover(toggle, slots.drawer, {
      onOpen() {
        placeDrawer();
        renderDrawer();
      },
    });

    // The drawer hangs below the bar, over the bar's width.
    function placeDrawer() {
      const box = root.getBoundingClientRect();
      slots.drawer.style.left = `${box.left}px`;
      slots.drawer.style.width = `${box.width}px`;
    }
    const replace = () => {
      if (popover.isOpen()) placeDrawer();
    };
    window.addEventListener("resize", replace);
    new ResizeObserver(replace).observe(root);

    function emptyNote(rows) {
      if (rows.length) return "";
      const deploy = store.get("deploy");
      return `No launchers for mode ${store.get("mode")}${deploy ? ` and deploy ${deploy}` : ""}.`;
    }

    // What the bar says while launch is not offered: the probe awaited, a
    // daemon without launch, or none.
    function unofferedNote() {
      if (!daemon.probed)
        return ["Checking for daemon…", "Waiting for the daemon probe."];
      if (daemon.health)
        return [
          "Launch not offered",
          "This daemon does not offer launch; the drawer lists the launch commands.",
        ];
      return [
        "Static export",
        "No daemon launches from this page; the drawer lists the launch commands.",
      ];
    }

    function renderBar() {
      const rows = control.rows();
      const offered = control.offered();
      const { system, mode, token, label } = control.summary();
      const [note, noteTitle] = offered ? ["", ""] : unofferedNote();
      const line = element("div", "launch-bar-line");
      line.appendChild(SD.RunState.mark(token));
      if (offered && system) {
        const page = config.systems.find((s) => s.id === system.id)?.page;
        const name = element(
          page && !system.shown ? "a" : "span",
          "launch-bar-system",
          system.name,
        );
        if (!system.shown) {
          name.classList.add("other");
          name.title = `${system.id} is launched; ${config.deploymentName} launches once it has shut down.`;
          if (page)
            name.href = `${config.siteRoot}${page}?activity=${encodeURIComponent(store.get("activity"))}`;
        } else name.title = system.id;
        line.appendChild(name);
      }
      if (offered && mode)
        line.appendChild(element("span", "launch-bar-mode", mode));
      line.appendChild(
        element(
          "span",
          "launch-bar-summary",
          offered || !rows.length ? label : note,
        ),
      );
      state.replaceChildren(line);
      state.title = noteTitle;
      const summaries = offered ? control.unitSummaries() : [];
      if (summaries.length > 1) {
        const marks = element("div", "launch-bar-units");
        summaries.forEach((unit) => {
          const item = element("span", "launch-bar-unit");
          item.title = `${unit.unit}: ${unit.label}`;
          item.append(
            SD.RunState.mark(unit.token),
            element("span", "launch-bar-unit-name", unit.unit),
          );
          marks.appendChild(item);
        });
        state.appendChild(marks);
      }

      const canLaunch = control.launchable().length > 0;
      const canShutdown = control.stoppableLaunched().length > 0;
      controls.hidden = !offered;
      launchAll.textContent = rows.length > 1 ? "Launch all" : "Launch";
      launchAll.disabled = !canLaunch;
      launchAll.classList.toggle("primary", canLaunch);
      shutdownAll.textContent = rows.length > 1 ? "Shutdown all" : "Shutdown";
      shutdownAll.disabled = !canShutdown;
      shutdownAll.classList.toggle("danger", canShutdown);

      // The last control errors, each named by its unit among several.
      const errors = offered
        ? control
            .unitErrors()
            .map(([unit, message]) =>
              rows.length > 1 ? `${unit}: ${message}` : message,
            )
        : [];
      error.textContent = errors.join("\n");
      error.hidden = !errors.length;
    }

    // RViz is one job per daemon; the button waits while it runs.
    function renderRviz() {
      const offered = daemon.offers("rviz.start");
      rvizButton.hidden = !offered;
      if (!offered) {
        rvizError.hidden = true;
        return;
      }
      const running = daemon
        .jobs()
        .some((job) => job.kind === RVIZ_KIND && job.state === "running");
      rvizButton.disabled = running;
      const rvizConfig = daemon.health?.launch?.rviz_config;
      rvizButton.title = running
        ? "RViz is running"
        : rvizConfig
          ? `rviz2 -d ${rvizConfig}`
          : "rviz2";
    }

    function renderDrawer() {
      const rows = control.rows();
      const offered = control.offered();
      checks.forEach((check) => (check.hidden = !offered));
      renderRviz();
      unitTable.element.hidden = !rows.length || !offered;
      unitTable.body.replaceChildren(
        ...rows.map((row) => {
          const tr = element("tr");
          const statusCell = element("td", "launch-status-cell");
          const controlCell = element("td");
          const parts = control.unitParts(row.unit);
          if (parts) {
            const status = element("div", "launch-unit-state");
            status.append(SD.RunState.mark(parts.token), ...parts.state);
            statusCell.appendChild(status);
            const controls = element("div", "unit-controls");
            controls.append(...parts.actions, ...parts.notes);
            controlCell.appendChild(controls);
          }
          tr.append(
            element("td", "launch-unit", row.unit),
            statusCell,
            controlCell,
          );
          return tr;
        }),
      );
      unitsEmpty.textContent = emptyNote(rows);
      unitsEmpty.hidden = !!rows.length;
    }

    function renderCommands() {
      const rows = control.rows();
      const method = control.method();
      methodSwitch.mark(method);
      methodNote.textContent = METHOD_NOTES[method];
      commandTable.element.hidden = !rows.length;
      commandTable.body.replaceChildren(
        ...rows.map((row) => {
          const tr = element("tr");
          const text = row[method];
          const commandCell = element("td", "command-cell");
          const copyCell = element("td", "copy-cell");
          if (text) {
            commandCell.appendChild(element("code", "", text));
            copyCell.appendChild(copyButton(text));
          } else {
            commandCell.appendChild(
              element(
                "span",
                "command-missing",
                "Not available for this method",
              ),
            );
          }
          tr.append(
            element("td", "launch-unit", row.unit),
            commandCell,
            copyCell,
          );
          return tr;
        }),
      );
      commandsEmpty.textContent = emptyNote(rows);
      commandsEmpty.hidden = !!rows.length;
    }

    function render() {
      renderBar();
      if (popover.isOpen()) renderDrawer();
      if (commands.isOpen()) renderCommands();
    }

    SD.on("launch.control", render);
    SD.on("daemon.jobs", () => {
      if (popover.isOpen()) renderRviz();
    });
    render();
  }

  launch.launchBar = launchBar;
})();
