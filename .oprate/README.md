# 仓库同步工具

此目录仅供本地使用，已被 `.gitignore` 忽略，不会提交到仓库。

## 检查同步状态

在项目根目录运行：

```powershell
oprate\sync_github_gitee.cmd
```

脚本会检查当前分支、工作区、GitHub `origin` 和 Gitee `gitee` 的提交差异，默认不会推送。

## 首次配置 Gitee

如果本地没有 `gitee` 远端，可在首次运行时传入仓库地址：

```powershell
oprate\sync_github_gitee.cmd -GiteeUrl git@gitee.com:你的用户名/你的仓库.git
```

脚本只会添加远端，不会自动提交改动。

## 执行同步

确认检查结果正常后：

```powershell
oprate\sync_github_gitee.cmd -Push
```

远端领先或发生分叉时，脚本会阻止推送。只有确认需要覆盖远端历史时才使用：

```powershell
oprate\sync_github_gitee.cmd -Push -Force
```

`-Force` 使用 `--force-with-lease`，比普通强制推送更安全。
