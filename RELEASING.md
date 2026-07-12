# Release Playbook

本文记录飞行雪绒发布流程，适用于 `LTS1.0.6beta8` 及后续版本。发布目标是：版本号一致、文档一致、普通包轻量、绿色包离线友好，并且不把开发机运行状态打进包里。

## 1. 发布前版本同步

至少同步以下文件：

- `config/version_info.py`
- `README.md`
- `CHANGELOG.md`
- `AA更新日志.txt`

`config/version_info.py` 是打包脚本读取版本号的来源。更新版本时优先改这里，再同步其它展示文档。

## 2. 发布前检查

### 文档门户

```powershell
python scripts/generate_doc_portal.py
```

检查：

- `AA使用必读.html` 可正常生成
- 门户版本号与当前版本一致
- 文档卡片、贡献名单、赞助名单路径正确

### 静态检查

```powershell
python -m compileall config lib scripts install_deps.py
python scripts/package_release.py --dry-run
python scripts/package_green_release.py --dry-run
```

### 运行冒烟

至少手动确认：

- 桌宠能启动、显示、正常退出
- 控制面板能打开、保存、重新加载配置
- AI 回复与流式输出可用
- 图片输入路径不会因模型名误判提前失败
- GSV / STT 开关不阻塞启动或退出
- 音乐搜索、播放、暂停、下一首不回归
- 本地网页中转登录流程可以打开浏览器并关闭运行时浏览器

## 3. 发布包类型

### 普通包

```powershell
python scripts/package_release.py --version LTS1.0.6beta8
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.6beta8.zip`
- `dist/FlyingSnowVelvet-LTS1.0.6beta8-manifest.json`

普通包用于联网环境，安装脚本会按 `resc.net.txt` 补齐重型资源。它应排除：

- `resc/models/`
- `resc/playwright/`
- `resc/GIF/SEanima/`
- `resc/chrome-runtime.zip`、`resc/chrome-runtime.z01`、`resc/chrome-runtime.z02`
- `resc/python-3.11.6-amd64.exe`
- `resc/user/`
- `logs/`
- `dist/`
- 本机配置、缓存、临时文件
- `C:\AemeathDeskPet\user`、`cache`、`logs` 中的任何本机数据

### 绿色包

```powershell
python scripts/package_green_release.py --version LTS1.0.6beta8
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.6beta8-green.zip`
- `dist/FlyingSnowVelvet-LTS1.0.6beta8-green-manifest.json`

绿色包与普通包保持相同的重型资源边界；离线资源应通过发布渠道单独提供，或在安装前预置到对应 `resc/` 路径。

绿色包仍必须排除：

- 已解包的 `resc/playwright/` 运行目录
- `resc/user/`
- `logs/`
- `dist/`
- `__pycache__/`
- 登录态、Cookie、storage state、API Key
- `C:\AemeathDeskPet` 下的用户稀疏配置、状态与缓存

## 4. 发布包内容审查

打包后检查 manifest：

```powershell
Get-Content dist\FlyingSnowVelvet-LTS1.0.6beta8-manifest.json | Select-String "playwright|models|SEanima|chrome-runtime|python-3.11|storage_state|__pycache__"
Get-Content dist\FlyingSnowVelvet-LTS1.0.6beta8-green-manifest.json | Select-String "playwright|models|SEanima|chrome-runtime|python-3.11|storage_state|__pycache__"
```

预期：

- 普通包和绿色包 manifest 都不应出现 `resc/models/`、`resc/GIF/SEanima/`、浏览器分卷、Python 安装器或 `resc/playwright/`。
- 两类包都不应出现运行时登录态或用户缓存。

## 5. Git 标签与远端发布

示例：

```powershell
git tag -a LTS1.0.6beta8 -m "LTS 1.0.6 beta8"
git push origin main
git push origin LTS1.0.6beta8
```

Release 建议上传：

- 普通包 zip 与 manifest
- 绿色包 zip 与 manifest（如本次提供）
- `AA使用必读.html`

Release Notes 以 `CHANGELOG.md` 当前版本段为主，必要时补充迁移说明。

## 6. 发布后检查

- 确认 GitHub Release 附件完整
- 确认普通包与绿色包体积符合预期
- 抽样下载解压，检查启动脚本和安装依赖脚本可运行
- 如修改了配置目录、缓存目录、发布包边界，在用户公告中单独说明

发布不是复制开发目录，而是冻结一个可复现、可解释、无本机隐私状态的运行快照。

