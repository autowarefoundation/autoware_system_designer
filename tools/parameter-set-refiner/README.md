# parameter-set-refiner

A tool to remove `autoware_launch` config file dependencies from
`*.parameter_set.yaml` files by inlining only the parameter values that
actually differ from each package's own defaults.

## Background

`*.parameter_set.yaml` files in the autoware system designer framework can
reference config files from `autoware_launch` via `param_files` entries:

```yaml
- node: /perception/object_recognition/detection/centerpoint/validation
  param_files:
    - obstacle_pointcloud_based_validator_param_path: >-
        $(find-pkg-share autoware_launch)/config/perception/.../obstacle_pointcloud_based_validator.param.yaml
  param_values: []
```

This creates a hard dependency on `autoware_launch` at design time. The
refiner resolves each such reference, compares the `autoware_launch` override
against the package's own default config, and rewrites the entry so only the
differing values are kept as inline `param_values`:

```yaml
- node: /perception/object_recognition/detection/centerpoint/validation
  param_files: []
  param_values:
    - name: using_2d_validator
      value: true
```

If the two files are identical, the reference is removed with no `param_values`
added.

## Usage

```bash
python3 inline_autoware_launch_params.py <parameter_set.yaml> [OPTIONS]
```

### Options

| Option                | Description                                                                        |
| --------------------- | ---------------------------------------------------------------------------------- |
| `--workspace-src DIR` | Root of the colcon `src/` tree. Auto-detected from the input file path if omitted. |
| `--dry-run`           | Print what would change without writing the file.                                  |
| `--output FILE`       | Write the result to `FILE` instead of modifying in place.                          |

### Example

```bash
python3 inline_autoware_launch_params.py /path/to/autoware/src/launcher/autoware_launch/autoware_sample_designs/design/parameter_set/sample_system_perception.parameter_set.yaml --workspace-src /path/to/autoware/src
```

## How it works

For each `param_files` entry referencing `$(find-pkg-share autoware_launch)/...`:

1. **Resolve the autoware_launch config** — maps the `$(find-pkg-share ...)` substitution to an actual file path in the workspace.

2. **Find the matching `*.node.yaml`** — searches the workspace for node definitions that declare the same `param_file` key. When multiple candidates exist (e.g. the generic `param_path` key appears in many nodes), the best match is selected using this priority:
   - Node whose declared default filename matches the autoware_launch config filename (exact match).
   - Node whose package contains `config/<autoware_launch_config_filename>`.
   - Node whose package name best matches the directory path of the autoware_launch config (path-segment scoring).

3. **Resolve the package default config** — locates the baseline config file to compare against. Prefers `<pkg>/config/<autoware_launch_config_filename>` (exact filename match) over the filename declared in `node.yaml`, to handle cases where a node.yaml's declared default points to a sibling config for a different mode.

4. **Diff recursively** — compares the autoware_launch config against the package default using a recursive, leaf-level diff. Only the parameters that actually differ are recorded, using dotted-path names (e.g. `association.can_assign.unknown`) so that identical sibling fields are not written.

5. **Rewrite the entry** — appends the differing values to `param_values` (skipping any already set), then removes the `autoware_launch` reference from `param_files`. Non-autoware_launch `param_files` entries are preserved unchanged.

### Output format

- Scalars: `value: true` / `value: 0.5`
- Arrays: inline flow style `value: [0.098, 0.147, 0.078]`
- Nested dicts: block mapping under the dotted-path name, e.g.:

  ```yaml
  - name: association.can_assign.unknown
    value:
      [polygon_tracker, multi_vehicle_tracker, pedestrian_and_bicycle_tracker]
  ```

## Requirements

```text
ruamel.yaml
```

Install with:

```bash
pip install ruamel.yaml
```
