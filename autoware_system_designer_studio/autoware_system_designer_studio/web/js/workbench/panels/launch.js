// Launch panel (launchpad): one card per compute unit of the selected mode and
// deploy variant. A daemon offering launch methods runs the unit from its card;
// each card also carries the unit's launch command for a terminal.
(function () {
  const SD = window.SD;
  const METHOD_KEY = "workbench.launchMethod";
  const METHODS = [
    ["native", "Runtime"],
    ["xml", "XML"],
  ];

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function copyButton(text) {
    const button = element("button", "copy-btn", "Copy");
    button.type = "button";
    button.title = text;
    button.addEventListener("click", () => {
      navigator.clipboard?.writeText(text).then(() => {
        button.textContent = "Copied";
        button.classList.add("copied");
        setTimeout(() => {
          button.textContent = "Copy";
          button.classList.remove("copied");
        }, 1500);
      });
    });
    return button;
  }

  SD.workbench.registerPanel({
    id: "launch",
    title: "Launch",
    order: 20,
    daemon: false,
    icon: "M6 4l10 6-10 6z",
    mount(body, { config, store, daemon }) {
      const records = config.launchCommands;
      const hasNative = records.some((record) => record.native);
      let method = SD.storage.get(METHOD_KEY);
      if (method !== "xml" && !(method === "native" && hasNative)) method = hasNative ? "native" : "xml";
      let unitStatus = new Map();

      const methodSwitch = element("div", "segmented");
      METHODS.forEach(([value, label]) => {
        if (value === "native" && !hasNative) return;
        const button = element("button", "", label);
        button.type = "button";
        button.dataset.method = value;
        button.addEventListener("click", () => {
          method = value;
          SD.storage.set(METHOD_KEY, value);
          render();
        });
        methodSwitch.appendChild(button);
      });
      const methodRow = element("div", "panel-row");
      methodRow.append(element("span", "panel-row-label", "Command"), methodSwitch);
      body.appendChild(methodRow);

      const list = element("div", "launch-list");
      body.appendChild(list);

      const pageLink = element("a", "panel-footer-link", "All commands as a table");
      pageLink.href = config.launchCommandsPath;
      body.appendChild(pageLink);

      function render() {
        methodSwitch.querySelectorAll("button").forEach((button) => {
          button.classList.toggle("active", button.dataset.method === method);
        });
        const mode = store.get("mode");
        const unit = store.get("unit");
        const deploy = store.get("deploy");
        const rows = records.filter((r) => r.mode === mode && (!unit || r.unit === unit) && r.deploy === deploy);
        const canLaunch = daemon.offers("launch.start");
        list.replaceChildren();
        if (!rows.length) {
          list.appendChild(
            element("p", "panel-empty", unit ? `No launcher for ${unit} in mode ${mode}.` : `No launchers for mode ${mode}.`),
          );
          return;
        }
        rows.forEach((row) => {
          const card = element("div", "launch-card");
          const head = element("div", "launch-card-head");
          head.appendChild(element("span", "launch-unit", row.unit));
          const status = unitStatus.get(row.unit);
          if (status) head.appendChild(element("span", `unit-status ${status}`, status.replace(/_/g, " ")));
          card.appendChild(head);

          if (canLaunch) {
            const launch = element("button", "panel-button primary", "Launch");
            launch.type = "button";
            launch.disabled = status !== "available";
            launch.addEventListener("click", () => {
              const params = { system: config.systemId, mode, compute_unit: row.unit };
              if (row.deploy) params.deploy = row.deploy;
              launch.disabled = true;
              daemon
                .rpc("launch.start", params)
                .then(() => store.set("panel", "monitor"))
                .catch((error) => {
                  launch.disabled = false;
                  launch.title = error.message;
                });
            });
            card.appendChild(launch);
          }

          const command = row[method];
          const line = element("div", "launch-command");
          // Marks keep the command's punctuation in place under the right-to-left ellipsis.
          const code = element("code", "", command ? `\u200E${command}\u200E` : "Not available for this method");
          if (command) {
            code.title = command;
            line.append(code, copyButton(command));
          } else {
            code.classList.add("command-missing");
            line.appendChild(code);
          }
          card.appendChild(line);
          list.appendChild(card);
        });
      }

      async function loadUnitStatus() {
        try {
          const result = await daemon.rpc("systems.list");
          const system = result.systems.find((s) => s.id === config.systemId);
          unitStatus = new Map((system?.compute_units || []).map((u) => [u.name, u.status]));
        } catch (error) {
          console.warn("launch panel:", error);
        }
        render();
      }

      ["mode", "unit", "deploy"].forEach((key) => store.onChange(key, render));
      SD.on("daemon.health", loadUnitStatus);
      if (daemon.health) loadUnitStatus();
      render();
    },
  });
})();
