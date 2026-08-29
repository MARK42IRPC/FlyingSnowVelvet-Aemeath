# Release Playbook

本文记录飞行雪绒发布流程，适用于 `LTS1.0.7beta1` 及后续版本。发布目标是：版本号一致、文档一致、普通包轻量、绿色包离线友好，并且不把开发机运行状态打进包里。

## 1. 发布前版本同步

至少同步以下文件：

- `config/version_info.py`
- `README.md`
- `CHANGELOG.md`

`config/version_info.py` 是打包脚本读取版本号的来源。更新版本时优先改这里，再同步其它展示文档。

应用版本仍使用 `LTS...` 格式，但桌宠自动更新不再扫描全部版本标签。每次发布后必须同步维护以下两个固定程序发布槽，并让它们指向同一份已验收程序快照：

- GitHub：`MARK42IRPC/FlyingSnowVelvet-Aemeath` 的 `PACK` release。
- Gitee：`Mark42IRPC/Aemeath-AIdeskpet` 的“最新包” release。

GitHub/Gitee 没有真实打包 ZIP 附件时，更新器使用固定 tag 自动生成的源码 ZIP，供测试版更新；后续上传真实打包版后优先使用附件。更新器按 release 时间选择较新的镜像，时间相同时选择响应更快的镜像；只有 revision 相同才允许下载失败后跨镜像回落。发布时应更新 release 时间/revision，确保客户端能识别固定槽内容变化。`RESC` 只存放 Python、Vosk、启动动画、公告和福利 API 配置等安装资源，不得复用为上述程序发布槽；七分卷 ONNX 模型使用独立的“语音包”发布槽。

## 2. 发布前检查

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
- 陪伴/办公切换只路由普通文本，`/`、`#` 命令不变；办公页可新建、恢复、取消任务并显示实时状态
- 办公权限窗的允许、任务内始终允许和拒绝可用；退出后没有残留 DSH/Node 侧车
- 图片输入路径不会因模型名误判提前失败
- ONNX 语音 / STT 开关不阻塞启动或退出
- 缺失或旧版语音包时安装提示位于控制面板顶部，磁盘选择、取消、下载与解压进度可用
- 音乐搜索、播放、暂停、下一首不回归
- 音乐登录流程可以打开系统 Edge 并关闭浏览器

## 3. 发布包类型

### 普通包

```powershell
python scripts/package_release.py --version LTS1.0.7beta1
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.7beta1.zip`
- `dist/FlyingSnowVelvet-LTS1.0.7beta1-manifest.json`

普通包用于联网环境，安装脚本会按 `resc.net.txt` 补齐重型资源。它应排除：

- `resc/models/`
- `resc/GIF/SEanima/`
- `resc/python-3.11.6-amd64.exe`
- `resc/node-24.13.0-win-x64/`
- `services/dsh-office-runtime/node_modules/`
- `resc/user/`
- `logs/`
- `dist/`
- `.git/`、`.gitignore`、`.github/` 等 Git 相关元文件
- `tests/`、`scripts/`、`.oprate/`、`用户反馈/` 等开发/维护目录
- 本机配置、缓存、临时文件
- `C:\AemeathDeskPet\user`、`cache`、`logs` 中的任何本机数据

普通包必须保留 `lib/script/gsvmove/bin/UnRAR.exe` 与 `LICENSE-UnRAR.txt`，并保留 `services/dsh-office-runtime/` 下的 package manifest、lockfile、profile、bridge 源码以及 `resc/agent/` 下的办公系统提示词和十个 `SKILL.md`；语音模型七个分卷、Node 运行目录和已安装 npm 依赖不进入程序包。

### 绿色包

```powershell
python scripts/package_green_release.py --version LTS1.0.7beta1
```

输出示例：

- `dist/FlyingSnowVelvet-LTS1.0.7beta1-green.zip`
- `dist/FlyingSnowVelvet-LTS1.0.7beta1-green-manifest.json`

绿色包需要额外携带安装脚本会联网下载的离线资源归档，优先覆盖以下路径：

- `resc/models/vosk-model-small-cn-0.22.zip`
- `resc/models/vosk-model-small-en-us-0.15.zip`
- `resc/GIF/SEanima.zip`

`scripts/package_green_release.py` 在正式打包时会自动检查这些归档；如果本地缺失，会按 `resc.net.txt` 尝试下载，并在下载阶段与写包阶段显示进度。`--dry-run` 只做检查和清单预览，不会触发下载。

绿色包仍必须排除：

- 已解包的 `resc/node-24.13.0-win-x64/` 与 `services/dsh-office-runtime/node_modules/`
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

绿色包同样只携带固定 UnRAR 后端，不携带 ONNX 语音模型分卷。

## 4. 发布包内容审查

打包后检查 manifest：

```powershell
Get-Content dist\FlyingSnowVelvet-LTS1.0.7beta1-manifest.json | Select-String "models|SEanima|python-3.11|storage_state|__pycache__|\.git|\.github|tests/|scripts/|\.oprate|用户反馈/"
Get-Content dist\FlyingSnowVelvet-LTS1.0.7beta1-green-manifest.json | Select-String "vosk-model-small-cn-0.22.zip|vosk-model-small-en-us-0.15.zip|SEanima.zip"
Get-Content dist\FlyingSnowVelvet-LTS1.0.7beta1-green-manifest.json | Select-String "resc/models/vosk-model-small-cn-0.22/|python-3.11|storage_state|__pycache__|\.git|\.github|tests/|scripts/|\.oprate|用户反馈/"
Get-Content dist\FlyingSnowVelvet-LTS1.0.7beta1-manifest.json, dist\FlyingSnowVelvet-LTS1.0.7beta1-green-manifest.json | Select-String "lib/script/gsvmove/bin/UnRAR.exe|lib/script/gsvmove/bin/LICENSE-UnRAR.txt"
```

预期：

- 普通包 manifest 不应出现 `resc/models/`、`resc/GIF/SEanima/` 或 Python 安装器。
- 绿色包 manifest 应出现 Vosk 模型 zip、`SEanima.zip`，但不应出现它们已解包后的运行目录。
- 两类包都不应出现 Git 元文件、测试目录、打包脚本、运维目录、运行时登录态或用户缓存。
- 两类包都应同时出现固定的 `UnRAR.exe` 与其许可证，且不应出现 `ONNX_aimisiV2语音包.part*.rar`。

## 5. ONNX 语音包发布

语音包独立于程序 ZIP 发布。Gitee 与 GitHub 的“语音包”槽必须各自上传完整七卷，文件名严格为 `ONNX_aimisiV2语音包.part01.rar` 至 `part07.rar`；客户端不会跨镜像拼接分卷。

上传前按 `doc/语音包协议.md` 校验包根目录、manifest、中英文前端、参考素材和 `SHA256SUMS.txt`。上传后从每个镜像至少下载第一卷并确认 RAR5 可读，随后用完整七卷执行一次安装校验和中英文 CPU 推理。程序发行包只提供经哈希锁定的官方 UnRAR，不允许在用户安装阶段联网下载解压器。

## 6. Git 标签与远端发布

示例：

```powershell
git tag -a LTS1.0.7beta1 -m "LTS 1.0.7 beta1"
git push origin main
git push origin LTS1.0.7beta1
```

Release 建议上传：

- 普通包 zip 与 manifest
- 绿色包 zip 与 manifest（如本次提供）

GitHub `PACK` 的普通包附件由 `.github/workflows/publish-pack.yml` 发布。先将本地
验收通过的 ZIP 与 manifest 上传到 Gitee `最新包`，再把两个固定 tag 推送到同一提交；
Windows runner 会从 Gitee 回读附件、校验 ZIP 与 manifest 条目一致，并使用仓库内置
`GITHUB_TOKEN` 覆盖上传到现有 `PACK` release。开发机无需保存 GitHub API 令牌，且
两个程序镜像保持字节级一致。发布完成后仍需分别从公开附件地址回读校验。

Release Notes 以 `CHANGELOG.md` 当前版本段为主，必要时补充迁移说明。

## 7. 发布后检查

- 确认 GitHub Release 附件完整
- 确认 GitHub `PACK` 与 Gitee“最新包”固定发布槽均指向本次程序快照，且 ZIP 可下载
- 确认普通包与绿色包体积符合预期
- 抽样下载解压，检查启动脚本和安装依赖脚本可运行
- 如修改了配置目录、缓存目录、发布包边界，在用户公告中单独说明

发布不是复制开发目录，而是冻结一个可复现、可解释、无本机隐私状态的运行快照。
