// Border interface: the launch-file interface of a border as one sheet. The
// command launching it, its launch arguments with their defaults, the
// interface ports (subscribers, publishers, clients, servers) with the topic
// or service name and the launch argument each is reached by, the launch
// files included ahead of the instance, and the instance. A port row reports the boundary port of the mode's structure
// as the selected element.
(function () {
  const SD = window.SD;

  function el(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = text;
    return item;
  }

  // Monospace text that may wrap after each slash.
  function code(text) {
    const item = el("code");
    String(text ?? "")
      .split(/(?<=\/)/)
      .forEach((part, index) => {
        if (index) item.appendChild(document.createElement("wbr"));
        item.appendChild(document.createTextNode(part));
      });
    return item;
  }

  // Table of `columns` ({ title, cell(row) -> node | string }) over `rows`.
  function table(columns, rows, onSelect) {
    const tableEl = el("table", "border-table");
    const head = tableEl.createTHead().insertRow();
    columns.forEach((column) => head.appendChild(el("th", "", column.title)));
    const body = tableEl.createTBody();
    rows.forEach((row) => {
      const tr = body.insertRow();
      columns.forEach((column) => {
        const content = column.cell(row);
        const td = tr.insertCell();
        if (content instanceof Node) td.appendChild(content);
        else td.textContent = content ?? "";
      });
      if (!onSelect) return;
      tr.classList.add("selectable");
      tr.addEventListener("click", () => {
        tableEl
          .closest(".border-sheet")
          .querySelectorAll("tr.selected")
          .forEach((other) => other.classList.remove("selected"));
        tr.classList.add("selected");
        onSelect(row);
      });
    });
    return tableEl;
  }

  function section(title, count, content, className = "") {
    const box = el("section", `border-section ${className}`.trim());
    const heading = el("h3", "border-section-title", title);
    if (count !== null) heading.appendChild(el("span", "border-count", count));
    box.append(heading, content);
    return box;
  }

  const empty = (text) => el("p", "border-empty", text);

  // Nodes within an instance of the structure.
  function countNodes(instance) {
    if (instance.entity_type === "node") return 1;
    return (instance.children || []).reduce((n, c) => n + countNodes(c), 0);
  }

  // Declared interface ports of one direction (`input` or `output`), each
  // with the structure's boundary port of its name and the paths of the
  // instance ports linked to it.
  function portRows(prefix, declared, boundary, links) {
    const byName = new Map(boundary.map((port) => [port.name, port]));
    const peers = new Map();
    links.forEach((link) => {
      const [own, other] =
        prefix === "input" ? [link.from_port, link.to_port] : [link.to_port, link.from_port];
      if (!own?.unique_id || !other?.port_path) return;
      const id = String(own.unique_id);
      if (!peers.has(id)) peers.set(id, []);
      peers.get(id).push(other.port_path);
    });
    return declared.map((port) => {
      const data = byName.get(port.name) || null;
      return {
        ...port,
        data,
        msgType: data?.msg_type || "",
        peers: data ? peers.get(String(data.unique_id)) || [] : [],
      };
    });
  }

  class BorderInterfaceModule extends DiagramBase {
    constructor(container, options = {}) {
      super(container, options);
      this.ready = this.init();
    }

    async init() {
      try {
        this.render(await this.loadModeData(this.options.mode));
      } catch (error) {
        console.error("Error loading border interface:", error);
        this.showError(`Error loading border interface: ${error.message}`);
      }
    }

    render(root) {
      const border = this.options.border || {};
      const sheet = el("div", "border-sheet");
      sheet.append(
        this.header(border),
        section(
          "Arguments",
          (border.arguments || []).length,
          this.argumentTable(border.arguments || []),
        ),
      );
      sheet.append(
        this.portSection(
          "Subscribers",
          "input",
          border.subscribers || [],
          root.in_ports || [],
          root,
        ),
        this.portSection(
          "Publishers",
          "output",
          border.publishers || [],
          root.out_ports || [],
          root,
        ),
      );
      // Service sections appear when the border declares such ports.
      if ((border.clients || []).length)
        sheet.appendChild(
          this.portSection(
            "Clients",
            "input",
            border.clients,
            root.in_ports || [],
            root,
            "Service",
          ),
        );
      if ((border.servers || []).length)
        sheet.appendChild(
          this.portSection(
            "Servers",
            "output",
            border.servers,
            root.out_ports || [],
            root,
            "Service",
          ),
        );
      sheet.append(
        section(
          "System launches",
          (border.system_launches || []).length,
          this.systemLaunchTable(border.system_launches || []),
        ),
        section("Instance", (root.children || []).length, this.instanceTable(root.children || [])),
      );
      this.container.replaceChildren(sheet);
    }

    header(border) {
      const box = el("header", "border-header");
      const title = el("div", "border-title");
      title.append(
        el("span", "kind-tag", "border"),
        el("span", "border-name", this.options.deployment),
      );
      box.appendChild(title);
      if (border.launch_command) {
        const command = el("div", "border-command");
        command.append(code(border.launch_command), SD.widgets.copyButton(border.launch_command));
        box.appendChild(command);
      }
      if (border.launch_file)
        box.appendChild(
          el("div", "border-launch-file", `Generated launch file: ${border.launch_file}`),
        );
      return box;
    }

    argumentTable(args) {
      if (!args.length) return empty("No launch arguments.");
      return table(
        [
          { title: "Argument", cell: (a) => code(a.name) },
          {
            title: "Default",
            cell: (a) =>
              a.default === null || a.default === undefined
                ? el("span", "border-required", "required")
                : code(a.default),
          },
          { title: "Description", cell: (a) => a.description },
        ],
        args,
      );
    }

    // Interface ports of one kind; `prefix` names the launch argument that
    // rebinds the topic or service name.
    portSection(title, prefix, declared, boundary, root, nameTitle = "Topic") {
      const rows = portRows(prefix, declared, boundary, root.links || []);
      const content = rows.length
        ? table(
            [
              { title: "Argument", cell: (r) => code(`${prefix}/${r.name}`) },
              { title: nameTitle, cell: (r) => code(r.topic) },
              { title: "Type", cell: (r) => code(r.msgType) },
              {
                title: "Instance port",
                cell: (r) => {
                  const list = el("div", "border-peers");
                  r.peers.forEach((path) => list.appendChild(code(path)));
                  return list;
                },
              },
            ],
            rows,
            (row) => {
              if (row.data) this.updateInfoPanel(row.data, "Port");
            },
          )
        : empty(`No ${title.toLowerCase()}.`);
      return section(title, rows.length, content, `border-${prefix}`);
    }

    systemLaunchTable(launches) {
      if (!launches.length) return empty("No system launches.");
      return table(
        [
          { title: "Package", cell: (l) => code(l.package) },
          { title: "File", cell: (l) => code(l.file) },
          { title: "Scoped", cell: (l) => (l.scoped ? "yes" : "no") },
          {
            title: "Arguments",
            cell: (l) => {
              const list = el("div", "border-peers");
              (l.arguments || []).forEach((a) => list.appendChild(code(`${a.name}:=${a.value}`)));
              return list;
            },
          },
        ],
        launches,
      );
    }

    instanceTable(instances) {
      if (!instances.length) return empty("No instance.");
      return table(
        [
          { title: "Instance", cell: (c) => c.name },
          { title: "Path", cell: (c) => code(c.path || "") },
          { title: "Kind", cell: (c) => c.entity_type || "" },
          { title: "Nodes", cell: (c) => String(countNodes(c)) },
          {
            title: "Design",
            cell: (c) => (c.source_file ? SD.editor.sourceLink(c.source_file, 1) : ""),
          },
        ],
        instances,
        (instance) => this.updateInfoPanel(instance, "Node", instance),
      );
    }

    clearSelection() {
      this.container
        .querySelectorAll("tr.selected")
        .forEach((row) => row.classList.remove("selected"));
    }
  }

  BorderInterfaceModule.portRows = portRows;

  SD.diagrams.register("border_interface", BorderInterfaceModule);
})();
