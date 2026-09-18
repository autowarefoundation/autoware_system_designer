# Sequence Diagram: Event-Chain Latency View

The sequence diagram of the deployment overview draws every event chain of the system as a timeline: time left to right, every process a block as wide as its run, one group per chain stacked down the page. It answers two questions about a design: which sequential path is the shortest or the most critical, and what the system's end-to-end latency is.

The chains are the design's: the trigger relation the node designs declare, node by node, port by port. A measurement file supplies the time along them, at node unit: the run of every gate from the exec of the output it feeds, the wait of every input before that run from the node's measured in→out response, and the transport of every link from the take it was matched with.

## What is drawn

- **Block**: a process gate executing, as wide as its run. Its colour is the top-level component of the node (the legend lists them); the line above it carries the trigger glyph of the logic diagram (`and`, `or`, clock, `once`), the node, the process and the time the run completes. A dashed glyph marks a gate whose type is not declared.
- **Wait**: the thin segment leading into a block is the time between the trigger arriving and the run starting: the sampling delay of a periodic gate, or the skew an `and` gate waits out.
- **Whisker**: ±1 standard deviation at the end of a block.
- **Hop**: an arrow from the end of one block to the arrival at the next gate, labelled with the topic. Its length is the transport time; a hop between two gates of one node (`to_trigger`) is dashed. A hop that lands after its block has started is drawn dotted: the gate is an `or` and fired from another branch. In the measured state a long-dashed hop is _sampled_: its length includes the measured wait between the input's arrival and the run that answered it (the node's in→out response less the run).
- **Group**: one chain, from a source gate (a clock-driven process) to a sink event, under a title line with its rate, gate count and total. There is one group per clock root, deepest first, analyzed from the event graph: no chain is declared in the design; each starts at its own source firing and all share the axis. Clocks whose streams one gate merges share a group: when a gate takes two or more inputs of one message type, the clocks at the head of each input's stream (the walk upstream that stays on that message type, through links and the gates that publish them) are _peers_ and are solved together, so their chains meet at that gate and continue as one. Three lidar decoders feeding a concatenation are one group titled `/sensing/lidar/{left,right,top}/lidar:decoder_0`; the IMU and twist the distortion correctors also take are inputs of another type, side inputs of the stream, and keep their own groups. Two inputs that carry the same clocks (a fork that rejoins, such as the raw and the segmented cloud into an occupancy grid) pair nothing. Clocks whose chain never leaves their own node are hidden behind the `single-node chains` toggle. Clicking a title, block or hop makes its group the active one; `enumerate chains` acts on it.
- **Track**: one row of blocks within a group. The chain the axis is driven by is the spine on the tinted centre track; every other gate continues the track of the gate it feeds, or takes the nearest free track beside the spine, so the branches that join or leave the spine stack above and below it.
- **Emphasis**: the maximum chain (red, the critical path), the minimum chain (green, the sequential shortest path) and the mean chain (orange, dashed where it leaves the other two). Everything off the three chains is dimmed. Nodes the source reaches but the chain does not pass are counted in the toolbar.
- **Loop edge**: the event graph is cyclic (vehicle → localization → planning → control → vehicle). Events are ranked by their distance from the group's sources and edges are kept in that order; an edge that does not lead further from the sources is kept only while it closes no cycle, so a loop is cut where the long way round rejoins a chain, never on a source's own short way to a merging gate. The toolbar counts the cut edges.
- **Chain end**: how a group's chain stops is drawn at its last gate and named in the title. A _loop end_ is a return wire: out of the end of the gate, under the track and back along it, up into the start of the gate its message rejoins, labelled `↺` with that gate's name; the same wire leaves any gate on the chain whose message closes a loop further up. An _open end_ is a short stub with an end stop after the last gate, labelled under the block with the message nobody takes: an output no node subscribes to, an input that triggers no process, or a gate that publishes nothing. A chain the hop limit cuts trails off without a stop. Clicking either opens a panel naming the two events of the loop or the reason the chain is open.
- **Rate**: the design's declared rate, propagated from the clocks, labels every gate in the logical state. In the measured state the group title, the gate tooltip and the panels put the rate the run observed beside it: a port's from its take or publish count, a periodic gate's from the timer nearest its declared period, any other gate's from the first output it feeds. The design propagates the fastest trigger's rate through `and` and `or` gates alike, so a concatenation downstream of a distortion corrector that also takes a 50 Hz IMU is declared at 50 Hz while the run shows the lidar rate; the panel flags a difference of more than a fifth.

## States

The vertices are the process and port events the node designs declare; clock roots are the `periodic` gates. Every chain is analyzed from the design graph in every state; what changes is the time on it.

| State    | x axis                               | Numbers                                                                                                                                                                                                                                                                   |
| -------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| logical  | rank (process gates from the source) | none; the default while nothing is measured                                                                                                                                                                                                                               |
| rates    | milliseconds                         | a periodic gate's sampling delay from its rate; every process run is an unmeasured placeholder of zero width                                                                                                                                                              |
| measured | milliseconds                         | a loaded measurement file; a run without a sample stays the placeholder and is drawn faint; a gate the record says never ran is dead, drawn dashed, and delivers nothing downstream; a gate the run observed waits nothing of its own, its inputs carry the measured wait |

The axis is driven by one component of every summary: `min`, `mean`, `max`, or `mean + kσ` (k = 1, 2, 3).

## Navigation

The page reads like a document at screen scale. The scroll wheel moves down and up the page, shift+scroll (or a sideways swipe) moves along the axis, and dragging pans. Ctrl+scroll, a pinch, or `time +` / `time −` zoom the millisecond scale, about the pointer or the centre of the view: blocks and hops stretch along the axis while tracks, glyphs and labels keep their size, and the ruler stays pinned to the top of the view. The scale opens with the longest sink chain fitted to the width of the view; `fit` returns to it.

## Timing model

Every cost and every arrival is a distribution summary `{ min, mean, max, sd, count }`, never a single number.

- **Series**: along a chain, `min`, `mean` and `max` add and _variances_ add (`sd = √Σsd²`).
- **`and` gate**: arrival folds as componentwise `max` over its branches.
- **`or` gate**: arrival folds as componentwise `min`. A gate with no declared type folds as `or` and is reported.
- **`periodic` gate (f Hz)**: needs no measurement; its sampling delay is uniform on `[0, 1/f]`: `min 0`, `mean 1/2f`, `max 1/f`, `sd 1/(f·√12)`.
- **Execution, wait and transport**: measured quantities only. The design declares no latency; a process run or a link without a sample costs zero and is marked `unmeasured`, so a chain total is a lower bound until the measurement covers it. At an `or` gate a branch that is fully measured takes precedence over one summing placeholders, whose zero width would otherwise always arrive first. A gate whose node was never observed, whose process exited inside the window or never got past construction, or whose every declared output was never published is **dead**: its run is marked `dead` with the reason, nothing arrives downstream of it, an `or` gate folds the live branches only and an `and` gate waiting on it is dead itself. The gate panel names the reason. A group opens on the deepest chain the run completed; a dead chain stays in view as a side branch where it feeds the live one, and the title reads `never ran` only when no sink of the group is live. An input's wait before a measured run is the node's in→out response less the run, taken so that wait + run reproduces the response in min, mean and max; a gate the run observed has no sampling delay of its own, since that wait sits on its inputs and a timer's phase is not part of a measured chain.
- **`once` gate**: initialization, excluded from steady-state chains.
- **Three chains from one solve**: each fold records which branch supplied each component, so walking back from the sink yields the minimum, mean and maximum chain. They often differ.

What the marks in the panel mean:

- `≈` after a mean: the value was folded at an `and`/`or` gate and describes one branch, not the set. An `and` gate's mean is a lower bound.
- `?` after a spread: hops without an `sd` were skipped; the chain's `sd` is an estimate, never a bound. `max` remains the bound.
- At an `or` gate the folded number describes only the fastest branch; every branch is listed with its own summary, and **enumerate chains** expands the `or` choices into separate chains (every `and` branch kept), ranked by `max` and capped.

## Measurement file

A run is loaded from `data/<mode>_latency.js` in the web bundle when the file exists, or by dropping a file (`.js` or `.json`) on the canvas. The file is one object; in the bundle it is written as the script `window.latencyData["<mode>"] = {...};`, which a page opened from `file://` can load where it cannot fetch JSON. The build copies `latency/<mode>_latency.js` (or `.json`) from the directory beside the system definition file when it exists and validates, and serves it as the script.

The runtime writes the file (schema `autoware_system_designer/latency/2`) from a traced run of the system, by default straight into the export's `visualization/web/data/`; see the [runtime README](../../autoware_system_designer_runtime/README.md#latency-measurement) for the command. It is keyed by node path and topic, never by process name or `unique_id`: ids are name hashes and change whenever the design is edited.

```json
{
  "schema": "autoware_system_designer/latency/2",
  "mode": "Runtime",
  "run": {
    "window_s": 60.0,
    "window_wall_s": 120.0,
    "clock": { "base": "ros", "rate": 0.5, "samples": 6000 },
    "probe": true,
    "tracer": "0.1.0",
    "processes": 55,
    "dropped_records": 0
  },
  "summary": {
    "design_nodes": 102,
    "observed_nodes": 100,
    "unobserved_nodes": 2,
    "nodes_exited": 1,
    "nodes_not_initialized": 1,
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
      "process": {
        "pids": [4242],
        "state": "running",
        "last_record": "2026-09-18T02:36:53+00:00"
      },
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
      ],
      "notes": {
        "declared_input_never_subscribed": ["<topic>"],
        "declared_trigger_never_taken": ["<topic>"],
        "declared_output_never_advertised": ["<topic>"]
      }
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

- `run.clock` is the time base of every duration and rate: `ros` when the system ran on `/clock` (`use_sim_time`), with its rate against wall time and the number of `/clock` samples, else `wall`. `window_s` is the window in that base, `window_wall_s` the same window in wall time; measured rates and the design's declared rates then speak the same time.
- `nodes[].inputs[].rate_hz`, `nodes[].timers[].rate_hz` and `nodes[].outputs[].rate_hz` are the rates the run observed; the measured state shows them beside the design's declared rates in the group title, the gate tooltip and the panels.
- `nodes[].outputs[].response[]` supplies the wait of an input→gate edge: the row whose `from` is the input's topic, under the output the gate feeds, is the node's in→out time from that input's arrival to the publish; less the output's `exec` it is the wait the message spent before the run.
- `nodes[].outputs[].exec` supplies the execution cost of a process gate: the gate takes the record of the output topic it feeds (ports are trusted, process names are not). `trigger` is the dominant detected trigger of that output and `response` the age each input has when the output leaves. The gate panel names the detected trigger; the hop panel shows the link, the in→out response, the run and the wait left between them, and a group's title puts the composed total beside the record's own end-to-end chain (`chains[]`).
- `links[]` supplies the transport cost of a topic between an output and the input it feeds, keyed by `topic`; `publisher` and `subscriber` narrow the match and may be left out. A link marked `intra_process` crossed no DDS hop: it is drawn as a zero-length hop and its time is part of the downstream node's `exec`.
- `chains[]` is the measured end-to-end time from a detected timer to a publish, following the message flow. A group's title shows the measured record of its clock-root node to its sink beside the total composed from `exec` and `links`; the two are measured separately and never derived from one another.
- `declared_diff` compares each output's declared trigger (from the node design's process events) with the observed one; the rows appear in a gate's info panel and in the Node panel.
- `process` says what became of the node's process over the window: `state` is `running`, `exited` (the runtime saw the process end inside the window; `exit` carries the wall time, the exit code and the actor) or `not_initialized` (the process traced only the endpoints every node creates — `/rosout`, `/parameter_events`, `/clock` — so the node never got past construction; not judged for a node whose design declares no topic port). `last_record` is the wall time of the process's last trace record. Exits are known to the runtime only; an offline re-analysis of the trace directory reports `running` for a process it cannot see end.
- `notes` separates a declared trigger topic the node never subscribed to (`declared_input_never_subscribed`, the node never asked for it) from one it subscribed to and never received (`declared_trigger_never_taken`), and lists declared outputs it never advertised (`declared_output_never_advertised`), inputs it took that the design does not name and inputs that feed nothing declared.
- `sd_ms` and `count` are optional. A record without `sd_ms` is drawn without a whisker and excluded from the chain's `sd`, with the skipped hops counted.
- `summary` gives the run-level counts (design nodes observed, exited or never initialized, traced nodes outside the design, declared outputs per `declared_diff` status) so a consumer can judge a run without walking the records; `unobserved_nodes` lists the design nodes that produced no record and `unmatched_nodes` the traced nodes the design does not contain. An observed node is one with any trace record, so `observed_nodes` counts an exited or uninitialized node too; the state counts say how many of them were alive.

The first shape, `autoware_system_designer/latency/1` (`processes[]` keyed by node path and process name, `links[]`), stays readable.

The toolbar reports how many records of the file matched the design. There are no deadlines: a chain reports its minimum, mean and maximum; it asserts nothing.
