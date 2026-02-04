# 処理内容メモ


## 全体の流れ

生成はcmakeからscript/deployment_process.pyを呼ぶことで実行される。

```
deploy_config = DeploymentConfig.from_env()
deploy_config.deployment_file = deployment_file
deploy_config.manifest_dir = manifest_dir
deploy_config.output_root_dir = output_root_dir

deployment = Deployment(deploy_config)
deployment.generate_parameter_set_template()
deployment.visualize()
deployment.generate_launcher()
deployment.generate_system_monitor()
deployment.generate_build_scripts()
```

### Deployment.__init__
