// Top bar popover: a panel below the bar opened by its button, closed by the
// button, a press outside the two, or Escape.
(function () {
  const SD = window.SD;

  // `onOpen` runs before the panel shows.
  function popover(button, panel, { onOpen } = {}) {
    function isOpen() {
      return !panel.hidden;
    }

    function setOpen(open) {
      if (open === isOpen()) return;
      if (open) onOpen?.();
      panel.hidden = !open;
      button.setAttribute("aria-expanded", String(open));
    }

    button.addEventListener("click", () => setOpen(!isOpen()));
    document.addEventListener("mousedown", (event) => {
      if (panel.contains(event.target) || button.contains(event.target)) return;
      setOpen(false);
    });
    // Captured and consumed, so the Escape closing the panel reaches no view under it.
    document.addEventListener(
      "keydown",
      (event) => {
        if (event.key !== "Escape" || !isOpen()) return;
        event.preventDefault();
        setOpen(false);
      },
      true,
    );

    return {
      isOpen,
      open: () => setOpen(true),
      close: () => setOpen(false),
    };
  }

  SD.popover = popover;
})();
