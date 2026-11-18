// Replay: the popup of the replay configuration file the choices give and
// the commands that run it, with the button that opens it.
(function () {
  const SD = window.SD;
  const { element, popup } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const { model, lists } = replay;

  // `shown()` gives { configuration, args } of the replay shown: the
  // arguments of model.configYaml and model.command.
  function commandsPopup(shown) {
    const commands = popup("Configuration and command");
    const configCode = lists.codeBlock(model.configFile());
    const checkCode = lists.codeBlock("Pre-flight");
    const runCode = lists.codeBlock("Replay");
    commands.body.append(
      configCode.element,
      checkCode.element,
      runCode.element,
    );
    const button = element(
      "button",
      "panel-button",
      "Configuration and command",
    );
    button.type = "button";
    button.title = "The replay configuration file and the commands that run it";
    button.setAttribute("aria-haspopup", "dialog");
    button.addEventListener("click", () => {
      const { configuration, args } = shown();
      const yaml = model.configYaml(configuration);
      const download = element("a", "copy-btn", "Download");
      download.href = `data:text/yaml;charset=utf-8,${encodeURIComponent(yaml)}`;
      download.download = model.configFile();
      configCode.set(yaml, download);
      checkCode.set(model.command({ ...args, check: true }));
      runCode.set(model.command(args));
      commands.open();
    });
    return { button, close: commands.close };
  }

  replay.commandsPopup = commandsPopup;
})();
