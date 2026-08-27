---
name: fsv-windows-powershell
description: Use for Windows paths, PowerShell commands, launchers, subprocesses, encoding, dependency scripts, or DSH runtime work in Flying Snow Velvet.
---

# Windows And PowerShell

桌宠运行在 Windows 用户目录中，路径可能包含中文、空格、`&`、`!`、`%` 或单引号。

- PowerShell 命令使用明确的 `-LiteralPath`，路径和参数不要依赖未经验证的 glob、环境变量或字符串拼接。
- 搜索优先使用 `rg` 或 `rg --files`。读取文件可使用 `Get-Content -Raw`；不要用 shell 重定向伪造源码写入。
- 不把路径直接拼入 `cmd /c`，不启用 delayed expansion；启动器和重启 helper 遵循 `ProcessStartInfo` 与项目现有 launcher。
- 隐藏的后台进程使用 `CREATE_NO_WINDOW` 或项目已有封装。只结束当前组件明确拥有的进程树，不扫描并终止未知进程。
- 注意 UTF-8、CRLF、PowerShell 的 `$` 展开和反引号转义；手工编辑优先使用 `apply_patch`。
- Node/DSH 必须遵循固定版本契约。开发环境可按现有运行时逻辑复用精确版本的系统 Node，但发行安装仍由根目录安装器准备随包运行时。
- 依赖扫描和安装要排除 uv 管理的基础解释器及虚拟环境。不要直接向带锁的 uv 环境写入桌宠依赖。

执行可能改变磁盘、进程、网络或权限状态的命令前，先确认目标绝对路径、所有权和审批边界。
