# colcon-autoware-system-designer

A colcon extension for the Autoware System Designer build.

At the start of `colcon build`, it writes every package that colcon discovered, selected or not, to
`<build-base>/.autoware_system_designer_packages.json`:

```json
{
  "format_version": 1,
  "packages": {
    "autoware_system_designer_demo_nodes": "/path/to/src/.../autoware_system_designer_demo_nodes"
  }
}
```

The manifest collector of each deploying package (`autoware_system_designer_build_deploy`) reads this
map to locate the design files of other packages. The map follows colcon's own discovery
(`--base-paths`, `COLCON_IGNORE`), so the result does not depend on the directory colcon runs from
or on where the sources live.

Without this extension, the collector derives the scan scope from the workspace layout.

## Install

```bash
pip install ./colcon-autoware-system-designer
```

Install it into the Python environment that runs `colcon`.

## Override

Set the CMake cache variable `AUTOWARE_SYSTEM_DESIGNER_PACKAGE_MAP` to read a map from another path,
for example one written by a separate `colcon` invocation.
