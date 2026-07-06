# 飞行雪绒

飞行雪绒是面向 Windows 10/11 的桌面宠物项目。当前主线以 `LTS1.0.6beta7` 为版本基线，核心能力包括桌宠展示、事件驱动对象系统、AI 伴聊、语音播报、本地语音识别、多源音乐播放、粒子与小游戏扩展。

- 当前版本：`LTS1.0.6beta7`
- 发布日期：`2026-07-07`
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
- 语音缓存、用户录音缓存等运行数据写入 `resc/user/` 下的忽略目录。

### 音乐

- 对外统一入口是 `lib/script/music/service.py`。
- 搜索 provider 位于 `lib/script/music/providers/`，当前包括 QQ / 网易云 / 酷狗。
- `lib/script/cloudmusic/` 已退回内部播放运行时实现，不建议外部直接依赖。
- UI 入口包括音响对象、搜索框、播放列表、进度面板等。

### 本地网页中转与浏览器运行时

- `services/yuanbao-free-api/` 保存本地网页中转服务源码。
- 登录/授权流程依赖 Playwright 驱动系统浏览器或绿色包提供的离线 Chromium 压缩包。
- 普通发布包不携带浏览器运行时；绿色包可携带 `resc/chrome-win64.zip` 供离线安装脚本解包。
- 实际浏览器运行目录 `resc/playwright/` 是运行时产物，始终不应进入 Git 或普通发布包。

## 目录速览

| 路径 | 说明 |
| --- | --- |
| `config/` | 默认配置、版本信息、共享配置模板 |
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
- 如存在 `resc/chrome-win64.zip`，解包为 Playwright 可识别的离线浏览器运行时
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

以下内容是运行时产物或本机状态，默认不进入 Git：

- `logs/`
- `dist/`
- `__pycache__/`
- `resc/user/`
- `resc/playwright/`
- `config/user_scale.json`
- `config/music/volume.json`
- `services/storage_state.json`
- `py.ini`

绿色包可以携带 `resc/chrome-win64.zip` 作为离线资源；普通包应排除该文件。

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

- 普通包：源码、默认资源、文档，不带 `resc/playwright/`，不带 `resc/chrome-win64.zip`。
- 绿色包：在普通包基础上保留离线模型和 `resc/chrome-win64.zip`，仍不带已解包的 `resc/playwright/` 运行目录。
- 两类包都应脱敏 `config/ollama_config.py` 中的密钥、登录态和会话字段。

## 许可证与声明

- 代码许可见 `LICENSE-CODE`。
- 素材许可见 `LICENSE-ASSETS`。
- 第三方服务、音乐平台和网页自动化能力仅用于学习、研究和个人测试；请自行遵守对应平台规则。

