# Release Playbook

本文记录飞行雪绒发布流程，适用于 `LTS1.0.6pre1` 及后续版本。发布目标是：版本号一致、文档一致、普通包轻量、绿色包离线友好，并且不把开发机运行状态打进包里。

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
python scripts/package_release.py --version LTS1.0.6pre1
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.6pre1.zip`
- `dist/FlyingSnowVelvet-LTS1.0.6pre1-manifest.json`

普通包用于联网环境，安装脚本会按 `resc.net.txt` 补齐重型资源。它应排除：

- `resc/models/`
- `resc/playwright/`
- `resc/GIF/SEanima/`
- `resc/chrome-runtime.zip`、`resc/chrome-runtime.z01`、`resc/chrome-runtime.z02`
- `resc/python-3.11.6-amd64.exe`
- `resc/user/`
- `logs/`
- `dist/`
- `.git/`、`.gitignore`、`.github/` 等 Git 相关元文件
- `tests/`、`scripts/`、`.oprate/`、`用户反馈/` 等开发/维护目录
- 本机配置、缓存、临时文件
- `C:\AemeathDeskPet\user`、`cache`、`logs` 中的任何本机数据

### 绿色包

```powershell
python scripts/package_green_release.py --version LTS1.0.6pre1
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.6pre1-green.zip`
- `dist/FlyingSnowVelvet-LTS1.0.6pre1-green-manifest.json`

绿色包需要额外携带安装脚本会联网下载的离线资源归档，优先覆盖以下路径：

- `resc/models/vosk-model-small-cn-0.22.zip`
- `resc/models/vosk-model-small-en-us-0.15.zip`
- `resc/GIF/SEanima.zip`
- `resc/chrome-runtime.z01`
- `resc/chrome-runtime.z02`
- `resc/chrome-runtime.zip`

`scripts/package_green_release.py` 在正式打包时会自动检查这些归档；如果本地缺失，会按 `resc.net.txt` 尝试下载，并在下载阶段与写包阶段显示进度。`--dry-run` 只做检查和清单预览，不会触发下载。

绿色包仍必须排除：

- 已解包的 `resc/playwright/` 运行目录
- 已解包的 `resc/models/vosk-model-small-*/`
- 已解包的 `resc/GIF/SEanima/`
- `resc/user/`
- `logs/`
- `dist/`
- `__pycache__/`
- `.git/`、`.gitignore`、`.github/` 等 Git 相关元文件
- `tests/`、`scripts/`、`.oprate/`、`用户反馈/` 等开发/维护目录
- 登录态、Cookie、storage state、API Key
- `C:\AemeathDeskPet` 下的用户稀疏配置、状态与缓存

## 4. 发布包内容审查

打包后检查 manifest：

```powershell
Get-Content dist\FlyingSnowVelvet-LTS1.0.6pre1-manifest.json | Select-String "playwright|models|SEanima|chrome-runtime|python-3.11|storage_state|__pycache__|\.git|\.github|tests/|scripts/|\.oprate|用户反馈/"
Get-Content dist\FlyingSnowVelvet-LTS1.0.6pre1-green-manifest.json | Select-String "vosk-model-small-cn-0.22.zip|vosk-model-small-en-us-0.15.zip|SEanima.zip|chrome-runtime.z01|chrome-runtime.z02|chrome-runtime.zip"
Get-Content dist\FlyingSnowVelvet-LTS1.0.6pre1-green-manifest.json | Select-String "resc/playwright/|resc/models/vosk-model-small-cn-0.22/|resc/models/vosk-model-small-en-us-0.15/|resc/GIF/SEanima/|python-3.11|storage_state|__pycache__|\.git|\.github|tests/|scripts/|\.oprate|用户反馈/"
```

预期：

- 普通包 manifest 不应出现 `resc/models/`、`resc/GIF/SEanima/`、浏览器分卷、Python 安装器或 `resc/playwright/`。
- 绿色包 manifest 应出现 Vosk 模型 zip、`SEanima.zip` 与浏览器分卷资源，但不应出现它们已解包后的运行目录。
- 两类包都不应出现 Git 元文件、测试目录、打包脚本、运维目录、运行时登录态或用户缓存。

## 5. Git 标签与远端发布

示例：

```powershell
git tag -a LTS1.0.6pre1 -m "LTS 1.0.6 pre1"
git push origin main
git push origin LTS1.0.6pre1
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

