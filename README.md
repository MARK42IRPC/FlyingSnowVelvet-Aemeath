# 飞行雪绒

飞行雪绒是面向 Windows 10/11 的桌面宠物项目。当前主线以 `LTS1.0.6beta9` 为版本基线，核心能力包括桌宠展示、事件驱动对象系统、AI 伴聊、语音播报、本地语音识别、多源音乐播放、粒子与小游戏扩展。

- 当前版本：`LTS1.0.6beta9`
- 发布日期：`2026-07-15`
- 主要入口：`lib/core/qt_desktop_pet.py`
- 生命周期编排：`lib/script/main.py`

## 当前定位

这个仓库不是单一 Demo，而是一套可运行的桌面宠物应用源码。项目目前重点放在三件事上：

- **运行稳定**：启动、预热、退出、清理链路集中在主生命周期中，运行时状态尽量不污染源码目录。
- **能力收敛**：聊天、音乐、本地托管服务等模块逐步统一入口，减少历史兼容层和重复单例。
- **离线友好**：普通发布包尽量轻量，绿色包可携带模型与浏览器离线包，便于无网络环境部署。

## 功能概览

### 桌宠与对象

- 主窗口、动作、移动、渲染、粒子、音频等基础设施位于 `lib/core/`。
- 对象管理器位于 `lib/script/obj-*`，例如音响、雪球、雪豹、沙发、摩托等。
- 粒子脚本位于 `lib/script/practical/`，发现与注册由 `lib/core/plugin_registry.py` 统一处理。

### AI 对话

- 聊天入口位于 `lib/script/chat/`。
- 支持 OpenAI 兼容 API、本地 Ollama、规则兜底和本地网页中转服务。
- OpenAI 兼容请求支持流式输出、上下文、人格、图片输入和多种兼容 payload 变体。
- DashScope/Qwen 系模型带图时会先尝试图片请求，接口实际拒绝后再提示用户关闭图片输入或更换模型。

### 语音与识别

- `lib/script/gsvmove/` 负责 GSVmove 文本转语音桥接。
- `lib/script/microphone_stt/` 负责 Vosk 本地语音识别和 Push-to-Talk。
- 用户设置、状态、密钥与缓存统一写入 `C:\AemeathDeskPet\user`、`cache`、`logs` 分层目录。

### 音乐

- 对外统一入口是 `lib/script/music/service.py`。
- 搜索 provider 位于 `lib/script/music/providers/`，当前包括 QQ / 网易云 / 酷狗。
- `lib/script/cloudmusic/` 已退回内部播放运行时实现，不建议外部直接依赖。
- UI 入口包括音响对象、搜索框、播放列表、进度面板等。

### 本地网页中转与浏览器运行时

- `services/yuanbao-free-api/` 保存本地网页中转服务源码。
- 登录/授权流程依赖 Playwright 驱动系统浏览器或安装脚本从 `resc.net.txt` 下载的离线 Chromium 分卷资源。
- 浏览器运行时、Vosk 模型、启动动画和 Python 安装器均不再内置，缺失时由安装脚本按清单下载。
- 实际浏览器运行目录 `resc/playwright/` 是运行时产物，始终不应进入 Git 或普通发布包。

## 目录速览

| 路径 | 说明 |
| --- | --- |
| `config/` | 只读默认配置、配置模型、用户稀疏覆盖与迁移逻辑 |
| `doc/` | 中文说明、事件系统、调度系统、粒子与开发指南 |
| `lib/core/` | 事件中心、主窗口、渲染、音频、托盘、日志等基础设施 |
| `lib/script/main.py` | 应用启动、预热、退出和组件清理编排 |
| `lib/script/chat/` | AI 客户端、聊天上下文、流式呈现、自动陪伴 |
| `lib/script/music/` | 音乐服务统一入口、provider、搜索路由 |
| `lib/script/cloudmusic/` | 音乐播放内部运行时 |
| `lib/script/gsvmove/` | 本地 TTS 托管服务桥接 |
| `lib/script/microphone_stt/` | 本地 STT 与按键说话 |
| `lib/script/ui/` | 控制面板、气泡、命令框、音乐面板、二维码面板等 UI |
| `services/` | 本地网页中转服务源码与可选离线 bundle |
| `resc/` | GIF、字体、音效、模型、绿色包离线资源与用户运行目录 |
| `scripts/` | 文档门户、普通包、绿色包打包脚本 |
| `tests/` | unittest 回归测试 |

## 快速开始

### 使用者

推荐使用 Windows + Python 3.10 及以上版本。

```powershell
python install_deps.py
```

或者双击：

- `安装依赖.bat`
- `启动程序.bat`

安装脚本会尝试完成：

- 选择可用 Python 并写入 `py.ini`
- 安装 `requirements.txt` 中的 Python 包
- 准备 Vosk 语音识别模型
- 准备本地网页中转服务源码或内置服务包
- 按 `resc.net.txt` 下载缺失的 Vosk、启动动画、Python 和浏览器资源
- 启动桌宠主程序

### 开发者

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m compileall config lib scripts install_deps.py
python lib/core/qt_desktop_pet.py
```

## 运行时数据边界

以下内容是运行时产物或旧版本本机状态，默认不进入 Git：

- `logs/`
- `dist/`
- `__pycache__/`
- `resc/user/`
- `resc/playwright/`
- `config/user_scale.json`
- `config/music/volume.json`
- `services/storage_state.json`
- `py.ini`

正式用户数据默认位于 `C:\AemeathDeskPet`：

- `user/settings.json`：仅保存偏离默认值的普通设置；
- `user/secrets/`：API Key、Cookie、登录态；
- `user/state/`：聊天记忆、音乐历史、游戏统计；
- `cache/`：音乐、语音等可再生成缓存；
- `logs/`：外部服务日志。

可使用 `py -3.11 scripts/config_tool.py check|compact|migrate|effective` 检查、压缩、迁移或查看最终生效配置。测试和便携环境可通过 `AEMEATH_DESK_PET_HOME` 覆盖根目录。

`resc.net.txt` 是重型资源的唯一下载清单，发布包不携带清单中对应的资源文件。

## 常用检查

```powershell
python -m compileall config lib scripts install_deps.py
python scripts/package_release.py --dry-run
python scripts/package_green_release.py --dry-run
```

按改动范围追加运行测试，例如：

```powershell
py -3 -m unittest discover -s tests -p "test_*music*.py"
py -3 -m unittest tests.test_openai_dashscope_multimodal
```

## 发布包边界

- 普通包和绿色包：源码、默认小型资源、文档，不带 `resc/models/`、`resc/playwright/`、`resc/GIF/SEanima/` 或 Python/浏览器安装包。
- 安装器依据 `resc.net.txt` 在首次运行时补齐缺失的重型资源。
- 两类包都应脱敏 `config/ollama_config.py` 中的密钥、登录态和会话字段。

## 许可证与声明

- 代码许可见 `LICENSE-CODE`。
- 素材许可见 `LICENSE-ASSETS`。
- 第三方服务、音乐平台和网页自动化能力仅用于学习、研究和个人测试；请自行遵守对应平台规则。

