# Sequence Diagram: Event-Chain Latency View

The sequence diagram of the deployment overview draws one chain of events as a timeline: from a source gate (a clock-driven process) to a sink event, time left to right, every process a block as wide as its run. It answers two questions about a design: which sequential path is the shortest or the most critical, and what the system's end-to-end latency is.

## What is drawn

- **Block**: a process gate executing, as wide as its run. Its colour is the top-level component of the node (the legend lists them); the line above it carries the trigger glyph of the logic diagram (`and`, `or`, clock, `once`), the node, the process and the time the run completes. A dashed glyph marks a gate whose type is not declared.
- **Wait**: the thin segment leading into a block is the time between the trigger arriving and the run starting: the sampling delay of a periodic gate, or the skew an `and` gate waits out.
- **Whisker**: ±1 standard deviation at the end of a block.
- **Hop**: an arrow from the end of one block to the arrival at the next gate, labelled with the topic. Its length is the transport time; a hop between two gates of one node (`to_trigger`) is dashed. A hop that lands after its block has started is drawn dotted: the gate is an `or` and fired from another branch.
- **Track**: one row of blocks. The chain the axis is driven by is the spine on the tinted centre track; every other gate continues the track of the gate it feeds, or takes the nearest free track beside the spine, so the branches that join or leave the spine stack above and below it.
- **Emphasis**: the maximum chain (red, the critical path), the minimum chain (green, the sequential shortest path) and the mean chain (orange, dashed where it leaves the other two). Everything off the three chains is dimmed. Nodes the source reaches but the chain does not pass are counted in the toolbar.
- **Loop edge**: the event graph is cyclic (vehicle → localization → planning → control → vehicle). A depth-first walk from the source cuts every edge that closes on an ancestor; the toolbar counts them.

## States

| State    | x axis                               | Numbers                                                                                                 |
| -------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| logical  | rank (process gates from the source) | none; the default while nothing is measured                                                             |
| declared | milliseconds                         | a periodic gate's sampling delay from its rate, plus any `latency` a process declares                   |
| measured | milliseconds                         | a loaded measurement file; a hop without a sample falls back to its declared value and is drawn hatched |

The axis is driven by one component of every summary: `min`, `mean`, `max`, or `mean + kσ` (k = 1, 2, 3). `time +` / `time −` zoom the millisecond scale.

## Timing model

Every cost and every arrival is a distribution summary `{ min, mean, max, sd, count }`, never a single number.

- **Series**: along a chain, `min`, `mean` and `max` add and _variances_ add (`sd = √Σsd²`).
- **`and` gate**: arrival folds as componentwise `max` over its branches.
- **`or` gate**: arrival folds as componentwise `min`. A gate with no declared type folds as `or` and is reported.
- **`periodic` gate (f Hz)**: needs no measurement; its sampling delay is uniform on `[0, 1/f]`: `min 0`, `mean 1/2f`, `max 1/f`, `sd 1/(f·√12)`.
- **`once` gate**: initialization, excluded from steady-state chains.
- **Three chains from one solve**: each fold records which branch supplied each component, so walking back from the sink yields the minimum, mean and maximum chain. They often differ.

What the marks in the panel mean:

- `≈` after a mean: the value was folded at an `and`/`or` gate and describes one branch, not the set. An `and` gate's mean is a lower bound.
- `?` after a spread: hops without an `sd` were skipped; the chain's `sd` is an estimate, never a bound. `max` remains the bound.
- At an `or` gate the folded number describes only the fastest branch; every branch is listed with its own summary, and **enumerate chains** expands the `or` choices into separate chains (every `and` branch kept), ranked by `max` and capped.

## Declared latency

A process may declare its execution summary in the node design (format 0.5.0):

```yaml
processes:
  - name: detect
    trigger_conditions:
      - on_input: image
    outcomes:
      - to_output: objects
    latency:
      min_ms: 2.0
      mean_ms: 4.5
      max_ms: 12.0
      sd_ms: 1.5
```

`min_ms` and `max_ms` are required. The shape is the one a measurement carries, so when both exist the panel shows the delta (`measured.max − declared.max`) and flags a measured `max` above the declared one, or a spread that grew past twice the declared `sd`. There are no deadlines: a chain reports its minimum, mean and maximum; it asserts nothing.

## Named chains

A system may name the chains its source picker offers:

```yaml
event_chains:
  - name: lidar_to_control
    description: Point cloud in, control command out.
    from: /sensing/lidar/top/lidar:hw_interface
    to: /control/trajectory_follower:output_control_cmd
```

`from` and `to` are `<node path>:<event name>`, where the event is a process name or a port name. Named chains are also the target paths a CARET export would declare.

## Measurement file

A run is loaded from `data/<mode>_latency.json` in the web bundle, or by dropping a file on the canvas. The build copies `latency/<mode>_latency.json` from the directory beside the system definition file when it exists and validates.

```json
{
  "schema": "autoware_system_designer/latency/1",
  "mode": "Runtime",
  "source": "caret",
  "processes": [
    {
      "node_path": "/localization/pose_twist_fusion_filter/ekf_localizer",
      "process": "fuse",
      "count": 1200,
      "min_ms": 1.8,
      "mean_ms": 3.1,
      "max_ms": 11.2,
      "sd_ms": 0.9
    }
  ],
  "links": [
    {
      "topic": "/localization/kinematic_state",
      "publisher": "/localization/pose_twist_fusion_filter/ekf_localizer",
      "subscriber": "/control/trajectory_follower",
      "min_ms": 0.2,
      "mean_ms": 0.4,
      "max_ms": 3.0,
      "sd_ms": 0.3
    }
  ]
}
```

- `processes[]` supplies the execution cost of a process gate, keyed by `node_path` and `process`.
- `links[]` supplies the transport cost of a topic between an output and the input it feeds, keyed by `topic`; `publisher` and `subscriber` narrow the match and may be left out.
- `sd_ms` and `count` are optional. A record without `sd_ms` is drawn without a whisker and excluded from the chain's `sd`, with the skipped hops counted.
- Records are keyed by names, never by `unique_id`: ids are name hashes and change whenever the design is edited.

The toolbar reports how many records of the file matched the design.

## CARET

CARET is the measurement path in both directions: the design already holds what a CARET architecture needs (processes as callbacks, ports and links as communications, named chains as target paths), and CARET's callback and communication latencies map onto `processes[]` and `links[]`. Both directions are reserved as documented stubs (`builder/export/caret_export.py`, `visualizer/latency_source.py`, `js/latency_source.js`) and are not implemented; a CARET run is expected in the file shape above until they are.
