// Popup over the workbench: a titled box on a dimmed overlay, closed by its
// button, a click on the overlay or Escape.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;

  function popup(title) {
    const overlay = element("div", "popup-overlay");
    const box = element("div", "popup");
    const head = element("div", "popup-head");
    const close = element("button", "popup-close", "✕");
    close.type = "button";
    close.setAttribute("aria-label", "Close");
    head.append(element("span", "popup-title", title), close);
    const body = element("div", "popup-body");
    box.append(head, body);
    overlay.appendChild(box);
    box.addEventListener("click", (event) => event.stopPropagation());

    function setOpen(open) {
      if (open && !overlay.isConnected) document.body.appendChild(overlay);
      overlay.classList.toggle("open", open);
    }
    close.addEventListener("click", () => setOpen(false));
    overlay.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });

    return {
      element: overlay,
      body,
      open: () => setOpen(true),
      close: () => setOpen(false),
      isOpen: () => overlay.classList.contains("open"),
    };
  }

  SD.widgets.popup = popup;
})();
