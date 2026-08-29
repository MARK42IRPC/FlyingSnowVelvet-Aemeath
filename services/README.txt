本目录用于存放桌宠随发行包提供的本地辅助服务源码。

- `dsh-office-runtime/`：办公模式使用的 DSH 侧车源码，包含 package manifest、lockfile、profile、bridge 和入口脚本。
- DSH 的 Node 运行目录与 `node_modules` 由根目录 `install_deps.py` 按固定版本准备，不直接提交到仓库或发行包。
- 其他历史服务目录已退役；请勿在此恢复自动下载、解压或启动旧的第三方本地中转服务。
