# Types

## FormatVersion

This is a string type object. Specify three integers separated by dots. The following versions are supported.

## Launch

This is a mapping type object.

| Key         | Type      | Required | Description |
| ----------- | --------- | -------- | ----------- |
| package     | string    | yes      |             |
| plugin      | string    | yes      |             |
| executable  | string    | yes      |             |
| node_output | LogOutput | yes      |             |

## InputInterface

| Key          | Type   | Required | Description |
| ------------ | ------ | -------- | ----------- |
| name         | string | yes      |             |
| message_type | string | yes      |             |
| qos          | QoS    | no       |             |
| global       | string | no       |             |

## OutputInterface

| Key          | Type   | Required | Description |
| ------------ | ------ | -------- | ----------- |
| name         | string | yes      |             |
| message_type | string | yes      |             |
| qos          | QoS    | no       |             |
| global       | string | no       |             |

## ParameterFile

| Key     | Type   | Required | Description |
| ------- | ------ | -------- | ----------- |
| name    | string | yes      |             |
| default | string | no       |             |

## ParameterValue

| Key     | Type   | Required | Description |
| ------- | ------ | -------- | ----------- |
| name    | string | yes      |             |
| type    | string | yes      |             |
| default | string | no       |             |

## Instance

| Key    | Type   | Required | Description |
| ------ | ------ | -------- | ----------- |
| name   | string | yes      |             |
| entity | string | yes      |             |

## ExternalInterfaces

| Key    | Type        | Required | Description |
| ------ | ----------- | -------- | ----------- |
| input  | List of ??? | yes      |             |
| output | List of ??? | yes      |             |

## Connection

| Key  | Type   | Required | Description |
| ---- | ------ | -------- | ----------- |
| from | string | yes      |             |
| to   | string | yes      |             |

## Process

| Key                | Type                     | Required | Description |
| ------------------ | ------------------------ | -------- | ----------- |
| name               | string                   | yes      |             |
| trigger_conditions | List of TriggerCondition | yes      |             |
| outcomes           | List of ???              | yes      |             |

## TriggerCondition

| Key        | Type | Required | Description |
| ---------- | ---- | -------- | ----------- |
| on_trigger |      |          |             |
| on_input   |      |          |             |
| periodic   |      |          |             |
| warn_rate  |      |          |             |
| error_rate |      |          |             |
| timeout    |      |          |             |
| and        |      |          |             |
| or         |      |          |             |
