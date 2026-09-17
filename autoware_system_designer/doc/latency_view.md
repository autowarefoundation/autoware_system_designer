# Sequence Diagram: Event-Chain Latency View

The sequence diagram of the deployment overview draws every event chain of the system as a timeline: time left to right, every process a block as wide as its run, one group per chain stacked down the page. It answers two questions about a design: which sequential path is the shortest or the most critical, and what the system's end-to-end latency is.

The chains come from one of two graphs. The **design graph** is the trigger relation the node designs declare. The **recorded graph** is what a measured run showed, at node unit: it exists once a measurement file is loaded and is the graph the view opens on, since declared events are a claim and the record is the observation.

## What is drawn

- **Block**: a process gate executing, as wide as its run. Its colour is the top-level component of the node (the legend lists them); the line above it carries the trigger glyph of the logic diagram (`and`, `or`, clock, `once`), the node, the process and the time the run completes. A dashed glyph marks a gate whose type is not declared.
- **Wait**: the thin segment leading into a block is the time between the trigger arriving and the run starting: the sampling delay of a periodic gate, or the skew an `and` gate waits out.
- **Whisker**: ±1 standard deviation at the end of a block.
- **Hop**: an arrow from the end of one block to the arrival at the next gate, labelled with the topic. Its length is the transport time; a hop between two gates of one node (`to_trigger`) is dashed. A hop that lands after its block has started is drawn dotted: the gate is an `or` and fired from another branch. In the recorded graph a long-dashed hop is _sampled_: the message did not fire its gate and waited in the subscription for the gate's next run, and the hop's length includes that wait.
- **Group**: one chain, from a source gate (a clock-driven process) to a sink event, under a title line with its rate, gate count and total. There is one group per clock root, deepest first, analyzed from the event graph: no chain is declared in the design; each starts at its own source firing and all share the axis. Clocks whose chain never leaves their own node are hidden behind the `single-node chains` toggle. Clicking a title, block or hop makes its group the active one; `enumerate chains` acts on it.
- **Track**: one row of blocks within a group. The chain the axis is driven by is the spine on the tinted centre track; every other gate continues the track of the gate it feeds, or takes the nearest free track beside the spine, so the branches that join or leave the spine stack above and below it.
- **Emphasis**: the maximum chain (red, the critical path), the minimum chain (green, the sequential shortest path) and the mean chain (orange, dashed where it leaves the other two). Everything off the three chains is dimmed. Nodes the source reaches but the chain does not pass are counted in the toolbar.
- **Loop edge**: the event graph is cyclic (vehicle → localization → planning → control → vehicle). A depth-first walk from the source cuts every edge that closes on an ancestor; the toolbar counts them.

## Graphs and states

| Graph    | Vertices                                                                                                                                                                                                   | States                   |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------ |
| design   | the process and port events the node designs declare; clock roots are the `periodic` gates                                                                                                                 | logical, rates, measured |
| recorded | one clock root per timer a node ran, one process gate per output it published, one port per topic taken or published; built from a `latency/2` file, with the design's instances behind the nodes it names | logical, measured        |

In the recorded graph a gate is fed by the trigger the run detected for its output (its timer, or the input that fired it) and by every input its `response` table names. The trigger edge costs nothing; a response edge is _sampled_ and carries the sampling delay of the gate, uniform over the gate's own measured period (its timer's under a timer, its output rate otherwise). A link joins a publish to the takes matched with it; a record with no publisher attaches to the topic's only recorded publisher. Nodes the design does not place are appended under the root without a guide. Every run and every link in the graph is measured; nothing is declared, so there is no rates state.

| State    | x axis                               | Numbers                                                                                                      |
| -------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------ |
| logical  | rank (process gates from the source) | none; the default while nothing is measured                                                                  |
| rates    | milliseconds                         | a periodic gate's sampling delay from its rate; every process run is an unmeasured placeholder of zero width |
| measured | milliseconds                         | a loaded measurement file; a run without a sample stays the placeholder and is drawn faint                   |

The axis is driven by one component of every summary: `min`, `mean`, `max`, or `mean + kσ` (k = 1, 2, 3). `time +` / `time −` zoom the millisecond scale.

## Timing model

Every cost and every arrival is a distribution summary `{ min, mean, max, sd, count }`, never a single number.

- **Series**: along a chain, `min`, `mean` and `max` add and _variances_ add (`sd = √Σsd²`).
- **`and` gate**: arrival folds as componentwise `max` over its branches.
- **`or` gate**: arrival folds as componentwise `min`. A gate with no declared type folds as `or` and is reported.
- **`periodic` gate (f Hz)**: needs no measurement; its sampling delay is uniform on `[0, 1/f]`: `min 0`, `mean 1/2f`, `max 1/f`, `sd 1/(f·√12)`.
- **Execution and transport**: measured quantities only. The design declares no latency; a process run or a link without a sample costs zero and is marked `unmeasured`, so a chain total is a lower bound until the measurement covers it.
- **`once` gate**: initialization, excluded from steady-state chains.
- **Three chains from one solve**: each fold records which branch supplied each component, so walking back from the sink yields the minimum, mean and maximum chain. They often differ.

What the marks in the panel mean:

- `≈` after a mean: the value was folded at an `and`/`or` gate and describes one branch, not the set. An `and` gate's mean is a lower bound.
- `?` after a spread: hops without an `sd` were skipped; the chain's `sd` is an estimate, never a bound. `max` remains the bound.
- At an `or` gate the folded number describes only the fastest branch; every branch is listed with its own summary, and **enumerate chains** expands the `or` choices into separate chains (every `and` branch kept), ranked by `max` and capped.

## Measurement file

A run is loaded from `data/<mode>_latency.json` in the web bundle, or by dropping a file on the canvas. The build copies `latency/<mode>_latency.json` from the directory beside the system definition file when it exists and validates.

The runtime writes the file (schema `autoware_system_designer/latency/2`) from a traced run of the system, by default straight into the export's `visualization/web/data/`; see the [runtime README](../../autoware_system_designer_runtime/README.md#latency-measurement) for the command. It is keyed by node path and topic, never by process name or `unique_id`: ids are name hashes and change whenever the design is edited.

```json
{
  "schema": "autoware_system_designer/latency/2",
  "mode": "Runtime",
  "run": {
    "window_s": 60.0,
    "probe": true,
    "tracer": "0.1.0",
    "processes": 55,
    "dropped_records": 0
  },
  "summary": {
    "design_nodes": 102,
    "observed_nodes": 100,
    "unobserved_nodes": 2,
    "nodes_not_in_design": 12,
    "outputs_by_status": {
      "match": 28,
      "mismatch": 40,
      "rate_mismatch": 37,
      "unobserved": 105,
      "undeclared": 169,
      "unknown": 5
    }
  },
  "nodes": [
    {
      "node_path": "/localization/pose_twist_fusion_filter/ekf_localizer",
      "inputs": [
        {
          "topic": "/localization/pose_estimator/pose_with_covariance",
          "rate_hz": 10.0
        }
      ],
      "timers": [{ "period_ms": 20.0, "rate_hz": 49.9 }],
      "outputs": [
        {
          "topic": "/localization/kinematic_state",
          "rate_hz": 49.9,
          "trigger": { "kind": "timer", "period_ms": 20.0, "share": 0.998 },
          "exec": {
            "count": 2990,
            "min_ms": 0.9,
            "mean_ms": 1.4,
            "max_ms": 6.2,
            "sd_ms": 0.4
          },
          "response": [
            {
              "from": "/localization/pose_estimator/pose_with_covariance",
              "mean_ms": 51.2,
              "min_ms": 1.0,
              "max_ms": 104.0,
              "sd_ms": 29.0
            }
          ]
        }
      ],
      "declared_diff": [
        {
          "output": "/localization/kinematic_state",
          "declared": "periodic 50 Hz",
          "observed": "timer 20 ms @ 49.9 Hz",
          "status": "match"
        }
      ]
    }
  ],
  "links": [
    {
      "topic": "...",
      "publisher": "...",
      "subscriber": "...",
      "count": 600,
      "min_ms": 0.2,
      "mean_ms": 0.4,
      "max_ms": 3.0,
      "sd_ms": 0.3
    },
    {
      "topic": "...",
      "publisher": "...",
      "subscriber": "...",
      "intra_process": true
    }
  ],
  "chains": [
    {
      "from": "<node_path>:timer:<period_ms>",
      "to": "<node_path>:<topic>",
      "hops": 4,
      "terminal": true,
      "count": 580,
      "min_ms": 12.0,
      "mean_ms": 18.5,
      "max_ms": 41.0,
      "sd_ms": 4.2
    }
  ],
  "unobserved_nodes": ["<node_path>"],
  "unmatched_nodes": [
    { "node": "<fqn>", "pids": [1234], "publishes": 600, "takes": 0 }
  ]
}
```

- `nodes[].outputs[].exec` supplies the execution cost of a process gate: the gate takes the record of the output topic it feeds (ports are trusted, process names are not). `trigger` is the dominant detected trigger of that output and `response` the age each input has when the output leaves. In the recorded graph the output _is_ the gate, `trigger` and `response` are its incoming edges, and `timers[]` are the clock roots; the hop panel shows the link, the sampling delay and the measured response side by side, the composed sum beside the direct measurement.
- `links[]` supplies the transport cost of a topic between an output and the input it feeds, keyed by `topic`; `publisher` and `subscriber` narrow the match and may be left out. A link marked `intra_process` crossed no DDS hop: it is drawn as a zero-length hop and its time is part of the downstream node's `exec`.
- `chains[]` is the measured end-to-end time from a detected timer to a publish, following the message flow. A group's title shows the measured record of its clock-root node to its sink beside the total composed from `exec` and `links`; the two are measured separately and never derived from one another.
- `declared_diff` compares each output's declared trigger (from the node design's process events) with the observed one; the rows appear in a gate's info panel and in the Node panel.
- `sd_ms` and `count` are optional. A record without `sd_ms` is drawn without a whisker and excluded from the chain's `sd`, with the skipped hops counted.
- `summary` gives the run-level counts (design nodes observed, traced nodes outside the design, declared outputs per `declared_diff` status) so a consumer can judge a run without walking the records; `unobserved_nodes` lists the design nodes that produced no record and `unmatched_nodes` the traced nodes the design does not contain.

The first shape, `autoware_system_designer/latency/1` (`processes[]` keyed by node path and process name, `links[]`), stays readable.

The toolbar reports how many records of the file matched the design. There are no deadlines: a chain reports its minimum, mean and maximum; it asserts nothing.
