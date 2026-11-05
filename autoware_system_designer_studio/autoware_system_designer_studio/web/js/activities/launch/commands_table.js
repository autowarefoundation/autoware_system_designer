// Launch: the launch commands of the selected mode and deploy, one row per
// compute unit, with the unit's reach on the daemon when known.
(function () {
  const SD = window.SD;
  const { element, copyButton } = SD.widgets;
  const launch = (SD.activities.launch ||= {});

  function commandsTable(container) {
    const table = element("table", "launch-table");
    const head = element("thead");
    const headRow = element("tr");
    ["Compute unit", "Status", "Command", ""].forEach((label) =>
      headRow.appendChild(element("th", "", label)),
    );
    head.appendChild(headRow);
    const body = element("tbody");
    table.append(head, body);
    container.appendChild(table);

    // `method` names the command column of each row; `unitStatus` maps unit -> reach.
    function render(rows, method, unitStatus) {
      table.classList.toggle("no-status", !unitStatus.size);
      body.replaceChildren(
        ...rows.map((row) => {
          const tr = element("tr");
          const status = unitStatus.get(row.unit) || "";
          const statusCell = element("td", "launch-status-cell");
          if (status)
            statusCell.appendChild(
              element("span", `unit-status ${status}`, status.replace(/_/g, " ")),
            );
          const command = row[method];
          const commandCell = element("td", "command-cell");
          const copyCell = element("td", "copy-cell");
          if (command) {
            commandCell.appendChild(element("code", "", command));
            copyCell.appendChild(copyButton(command));
          } else {
            commandCell.appendChild(
              element("span", "command-missing", "Not available for this method"),
            );
          }
          tr.append(element("td", "launch-unit", row.unit), statusCell, commandCell, copyCell);
          return tr;
        }),
      );
    }
    return { render };
  }

  launch.commandsTable = commandsTable;
})();
