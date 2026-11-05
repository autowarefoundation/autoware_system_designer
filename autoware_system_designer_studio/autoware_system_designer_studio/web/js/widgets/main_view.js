// Main view frame: a head with a title and trailing controls over the body,
// or an empty message in the body's place.
(function () {
  const SD = window.SD;
  const { element } = SD.widgets;

  function mainView({ title = "", empty = "" } = {}) {
    const root = element("div", "pane");
    const head = element("div", "main-view-head");
    const titleNode = element("span", "main-view-title", title);
    const trailing = element("div", "main-view-trailing");
    head.append(titleNode, trailing);
    const emptyNode = element("p", "main-empty");
    const body = element("div", "pane");
    root.append(head, emptyNode, body);

    // A non-empty `text` replaces the body with the message.
    function setEmpty(text) {
      emptyNode.textContent = text || "";
      emptyNode.hidden = !text;
      body.hidden = !!text;
    }

    function setTitle(text) {
      titleNode.textContent = text;
    }

    setEmpty(empty);
    return { element: root, head, trailing, body, setTitle, setEmpty };
  }

  SD.widgets.mainView = mainView;
})();
