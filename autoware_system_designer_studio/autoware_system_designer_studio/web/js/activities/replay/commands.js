// Replay: the popup of the replay configuration file the choices give and
// the commands that run it, with the button that opens it.
(function () {
  const SD = window.SD;
  const { element, popup } = SD.widgets;
  const replay = (SD.activities.replay ||= {});
  const { model, lists } = replay;

  function commandsPopup() {
    const commands = popup("Configuration and command");
    const configCode = lists.codeBlock(model.CONFIG_FILE);
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
    button.addEventListener("click", commands.open);
    return {
      button,
      close: commands.close,
      // `configuration` is the argument of model.configYaml, `args` of model.command.
      set(configuration, args) {
        const yaml = model.configYaml(configuration);
        const download = element("a", "copy-btn", "Download");
        download.href = `data:text/yaml;charset=utf-8,${encodeURIComponent(yaml)}`;
        download.download = model.CONFIG_FILE;
        configCode.set(yaml, download);
        checkCode.set(model.command({ ...args, check: true }));
        runCode.set(model.command(args));
      },
    };
  }

  replay.commandsPopup = commandsPopup;
})();
