---
name: fsv-release-validation
description: Use only when the user explicitly requests release validation, manifest checks, or offline installer preparation.
disable-model-invocation: true
user-invocable: true
---

# Release Validation

发行验证必须从当前源码和发行脚本的白名单出发，不把历史归档清单当作当前契约。

- 离线发行版运行 `py -3 -m unittest tests.test_offline_distribution tests.test_windows_zip_extract tests.test_update_installer`，并用 `scripts/build_offline_installer.py` 验证 versioned EXE。
- 确认 `resc/agent/office_system_prompt.txt` 和十个 `SKILL.md` 随包，确认 `services/dsh-office-runtime` 的 manifest、lockfile、profile 和 bridge 源码随包。
- 确认 `node_modules`、随包 Node 目录、用户数据、API key、会话状态、日志、临时文件和开发测试目录不进入发行包。
- 发布物只允许 versioned Offline-Installer.exe 和对应 manifest；不上传 ZIP 或旧 green 资产。
- 检查文件边界、敏感配置清洗、manifest 和版本来源。发行包生成或发布属于用户明确授权的动作。
- 记录实际命令、文件数量/体积摘要、失败项和未执行项；不要只报告“打包成功”。

不要自动提交、上传、推送或覆盖已有发行产物。
