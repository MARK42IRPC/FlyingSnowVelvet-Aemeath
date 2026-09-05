---
name: fsv-dependency-maintenance
description: Use only when the user explicitly requests dependency installation, dependency diagnosis, or changes to Python, Node, npm, DSH, or voice runtime setup.
disable-model-invocation: true
user-invocable: true
---

# Dependency Maintenance

用户侧依赖安装统一归根目录 `安装依赖.bat` 和 `install_deps.py`，办公任务不能自行维护用户环境。

- 先检查 Python、pip、uv 标记、Node/npm、DSH source bundle 和已有安装状态，再选择安装器阶段。
- 排除 uv 管理的基础解释器和 uv 创建的虚拟环境；不要向带锁环境写入桌宠依赖。
- 主 Python 依赖、DirectML venv、语音包运行时和 DSH Node/npm 依照现有固定版本与镜像校验契约处理。
- DSH 使用固定 Node/npm 和 `services/dsh-office-runtime/package-lock.json`，用户侧安装执行 `npm ci --omit=dev --ignore-scripts --no-audit --no-fund`，不得用任意 npm install 改写锁文件。
- 单个依赖失败时保留后续模块和最终摘要；进度必须由真实 pip 阶段或模块完成驱动，不使用按时间虚增的百分比。
- 不在应用运行时联网自修复、扫描用户目录寻找替代环境或覆盖用户 API key、登录态和运行日志。
- 修改安装器后运行安装器专项测试，并检查统一离线发行包 dry-run。

Skill 只提供安装边界；真正执行安装必须由用户明确触发并经过桌宠现有流程。
