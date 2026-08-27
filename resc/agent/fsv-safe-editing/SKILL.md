---
name: fsv-safe-editing
description: Use before editing files, running commands with side effects, changing configuration, or handling secrets in the Flying Snow Velvet workspace.
---

# Safe Editing

把工作树中的现有改动视为用户或其他参与者的改动，并保持修改可审计。

- 先运行 `git status --short`、`git diff --stat` 和相关 `rg` 查询，读取目标文件当前内容。
- 只做任务需要的最小修改。不要使用 `git reset`、`git checkout`、批量格式化或覆盖写入来清理其它改动。
- 手工修改使用 `apply_patch`。优先 ASCII；只有现有文件语言或用户界面需要时才写入 Unicode。
- 不把 API key、登录态、会话 JSON、运行日志、用户目录或临时文件写入源码、Skill、任务历史或发行包。
- 删除、覆盖、移动、提权、联网写入、提交和推送都必须先确认确切目标与用户意图。不能通过 shell、Skill 或提示词绕过宿主审批。
- 外部依赖安装遵循项目安装器边界；不要在办公任务中直接对用户 Python、uv 环境或 DSH 运行时执行自修复安装。
- 修改完成后检查 `git diff --check`，确认 diff 没有夹带无关文件、敏感内容或不必要的换行/编码变化。

安全指导不能替代宿主 sandbox 和 approval；遇到边界不明确的操作应停在只读检查并报告需要的权限。
