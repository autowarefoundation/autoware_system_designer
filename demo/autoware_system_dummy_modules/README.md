# autoware_system_dummy_modules

Dummy nodes of the example systems. Each node design under `design/node` is built into a node
that does what its design declares, with generated messages:

- every subscriber and publisher exists with the design's message type and quality of service;
- every process publishes its outputs when its trigger conditions fire: at the `periodic` rate,
  on each `on_input` message, on the first message of `once: <input>`, at start for `once`;
- `to_trigger` and `on_trigger` run the triggered process;
- each port logs one message every five seconds (`said <port> #<count>`, `heard <port> #<count>`).

The message types are `std_msgs` (text, numbers, arrays, `Header`, `ColorRGBA`), `tf2_msgs` for
`/tf` and `std_srvs` for services, so a system of these nodes runs on a ROS base installation.

| Message field `data` | Published value                  |
| -------------------- | -------------------------------- |
| string               | `<node> <port> #<count>`         |
| number               | the count (integers modulo 100)  |
| boolean              | alternating                      |
| array                | four numbers following the count |

`scripts/generate_dummy_nodes.py` generates one `DummyNode` subclass per design at configure time
and registers it as the component and executable named in the design's `launch`. In an `and`
condition the periodic conditions fire, or the input conditions when there is none, once every
other condition has received a message.
