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

    function append(lines) {
      if (!lines.length) return;
      log.querySelector(".terminal-placeholder")?.remove();
      const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
      lines.forEach((line) =>
        log.appendChild(document.createTextNode(`${line}\n`)),
      );
      lineCount += lines.length;
      while (lineCount > maxLines) {
        log.firstChild.remove();
        lineCount -= 1;
      }
      if (atBottom) log.scrollTop = log.scrollHeight;
    }

    clear();
    return { element: log, append, clear };
  }

  SD.widgets.terminal = terminal;
})();
