// Terminal view of a log: keeps the last `maxLines` lines and follows new
// output while scrolled to the end.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;

  function terminal(placeholder, maxLines = 5000) {
    const log = element("pre", "terminal-log");
    let lineCount = 0;

    function clear(text = placeholder) {
      log.replaceChildren(element("span", "terminal-placeholder", text));
      lineCount = 0;
    }

    function push(nodes) {
      if (!nodes.length) return;
      log.querySelector(".terminal-placeholder")?.remove();
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
      nodes.forEach((node) => log.appendChild(node));
      lineCount += nodes.length;
      while (lineCount > maxLines) {
        log.firstChild.remove();
        lineCount -= 1;
      }
      if (atBottom) log.scrollTop = log.scrollHeight;
    }

    function append(lines) {
      push(lines.map((line) => document.createTextNode(`${line}\n`)));
    }

    // A line set apart from the output, such as where the process ended.
    function note(text) {
      push([element("span", "terminal-note", `${text}\n`)]);
    }

    clear();
    return { element: log, append, note, clear };
  }

  SD.widgets.terminal = terminal;
})();
