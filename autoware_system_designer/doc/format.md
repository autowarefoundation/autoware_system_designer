# Autoware System Design Format

## 共通部分

### autoware_system_design_format

| Item                          | Type                                      | Required | Descrption |
| ----------------------------- | ----------------------------------------- | -------- | ---------- |
| autoware_system_design_format | [FormatVersion](./types.md#formatversion) |          |            |
| name                          | ModuleNameDefinition                      |          |            |
| base                          |                                           |          |            |
| override                      |                                           |          |            |
| remove                        |                                           |          |            |

## 外部仕様

| Item            | Type | Required | Descrption |
| --------------- | ---- | -------- | ---------- |
| launch          |      |          |            |
| inputs          |      |          |            |
| outputs         |      |          |            |
| parameter_files |      |          |            |
| parameters      |      |          |            |

## 内部構造

| Item                | Type               | Required | Descrption |
| ------------------- | ------------------ | -------- | ---------- |
| instances           | List of Instance   |          |            |
| external_interfaces | ExternalInterfaces |          |            |
| connections         | List of Connection |          |            |

## 解析仕様

| Item      | Type | Required | Descrption |
| --------- | ---- | -------- | ---------- |
| processes |      |          |            |
