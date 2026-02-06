# Manifest file

実装のために build/autoware_system_designer/resource` 以下に自動生成されるファイル。たぶんament_indexを上手く使えば生成しなくても良いはず。

## _package_map.yaml

| Item        | Type           | Descrption                                                                  |
| ----------- | -------------- | --------------------------------------------------------------------------- |
| package_map | dict[str, str] | パッケージ名とインストール先の対応関係。 |

## &lt;package_name&gt;.yaml

| Item                | Type       | Descrption                                         |
| ------------------- | ---------- | -------------------------------------------------- |
| package_name        | str        | パッケージの名前。                                 |
| deploy_config_files | list[dict] | パッケージが持つ仕様ファイルのパスとタイプの一覧。 |
