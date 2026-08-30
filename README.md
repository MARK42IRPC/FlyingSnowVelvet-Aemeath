# 飞行雪绒

飞行雪绒是面向 Windows 10/11 的桌面宠物项目。当前主线以 `LTS1.0.7beta2` 为版本基线，核心能力包括桌宠展示、事件驱动对象系统、AI 伴聊、语音播报、本地语音识别、多源音乐播放、粒子与小游戏扩展。

- 当前版本：`LTS1.0.7beta2`
- 发布日期：`2026-08-30`
- 主要入口：`lib/core/qt_desktop_pet.py`
- 生命周期编排：`lib/script/main.py`

## 当前定位

这个仓库不是单一 Demo，而是一套可运行的桌面宠物应用源码。项目目前重点放在三件事上：

- **运行稳定**：启动、预热、退出、清理链路集中在主生命周期中，运行时状态尽量不污染源码目录。
- **能力收敛**：聊天、音乐、本地托管服务等模块逐步统一入口，减少历史兼容层和重复单例。
- **离线友好**：普通发布包尽量轻量，绿色包可携带语音模型和启动动画资源，便于无网络环境部署。

## 功能概览

### 桌宠与对象

- 主窗口、动作、移动、渲染、粒子、音频等基础设施位于 `lib/core/`。
- 对象管理器位于 `lib/script/obj-*`，例如音响、雪球、雪豹、沙发、摩托等。
- 粒子脚本位于 `lib/script/practical/`，发现与注册由 `lib/script/plugin_registry.py` 统一处理。

### AI 对话

- 聊天入口位于 `lib/script/chat/`。
- 支持福利 API、手动 OpenAI 兼容 API、本地 Ollama 和规则回复；回复只使用用户选中的来源，不跨来源回退。
- 福利 API 启用时会并发测速 GitHub/Gitee 发布配置源获取密钥与地址，并使用程序内置的固定 Agnes 模型标识，不依赖服务端实现 `/models`；默认使用 Agnes 2.0 Flash，开启“智力提升”后使用 Agnes 2.5 Flash。配置下载固定 10 秒超时，失败后额外重试 3 次。
- 手动 API 的模型框支持直接输入或按当前地址、密钥探测 OpenAI 兼容的 `/models` 列表；未填写协议的公网地址会补全为 `https://`，本机地址补全为 `http://`。
- OpenAI 兼容请求支持流式输出、上下文、人格、图片输入和多种兼容 payload 变体。
- DashScope/Qwen 系模型带图时会先尝试图片请求，接口实际拒绝后再提示用户关闭图片输入或更换模型。

### 语音与识别

- `lib/script/gsvmove/` 负责 ONNX 语音包安装、旧 GSVmove 迁移和本地文本转语音推理，不再依赖端口 9880 的外部服务。
- `lib/script/microphone_stt/` 负责 Vosk 本地语音识别和 Push-to-Talk；识别前可启用轻量 PCM16 自适应降噪，控制面板提供降噪强度和噪声门阈值。
- 用户设置、状态、密钥与缓存统一写入 `C:\AemeathDeskPet\user`、`cache`、`logs` 分层目录。

### 工作台与更新

- 控制面板工作台按页面懒加载，切换页面和主题使用淡入淡出；工作台是普通任务栏窗口，不修改桌宠全局帧率限制。
- 配置保存、语音包解压、麦克风启动和更新探测/下载都通过交互 I/O 或独立后台任务执行，只有保存成功才允许关闭或重启设置面板。
- 更新包完成校验后由独立 helper 在旧进程退出后覆盖安装；随后可选择普通重启进入 `启动程序.bat`，或环境重启进入 `安装依赖.bat`。

### 音乐

- 对外统一入口是 `lib/script/music/service.py`。
- 搜索 provider 位于 `lib/script/music/providers/`，当前包括 QQ / 网易云 / 酷狗。
- `lib/script/cloudmusic/` 已退回内部播放运行时实现，不建议外部直接依赖。
- UI 入口包括音响对象、搜索框、播放列表、进度面板等。

### 系统浏览器登录

- 音乐登录/授权流程使用 Playwright 驱动系统 Microsoft Edge，不下载或内置 Chromium。
- Vosk 模型、启动动画和 Python 安装器均不再内置，缺失时由安装脚本按清单下载。

## 目录速览

| 路径 | 说明 |
| --- | --- |
| `config/` | 只读默认配置、配置模型、用户稀疏覆盖与迁移逻辑 |
| `doc/` | 维护手册、AI 协作规范及事件、调度、粒子等专项协议 |
| `lib/core/` | 事件中心、主窗口、渲染、音频、托盘、日志等基础设施 |
| `lib/script/main.py` | 应用启动、预热、退出和组件清理编排 |
| `lib/script/chat/` | AI 客户端、聊天上下文、流式呈现、自动陪伴 |
| `lib/script/music/` | 音乐服务统一入口、provider、搜索路由 |
| `lib/script/cloudmusic/` | 音乐播放内部运行时 |
| `lib/script/gsvmove/` | ONNX 语音包安装与本地 TTS 推理兼容门面 |
| `lib/script/microphone_stt/` | 本地 STT 与按键说话 |
| `lib/script/ui/` | 控制面板、气泡、命令框、音乐面板、二维码面板等 UI |
| `services/` | 办公 DSH 侧车源码与固定依赖 |
| `resc/` | GIF、字体、音效、模型、绿色包离线资源与用户运行目录 |
| `scripts/` | 普通包、绿色包和其他维护脚本 |
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

- 无可用 Python 时通过 PowerShell 从 Gitee/GitHub 自动下载、校验并安装固定的 Python 3.11；随后选择解释器并写入 `py.ini`
- 安装 `requirements.txt` 中的 Python 包
- 准备 Vosk 语音识别模型
- 在共享语音运行目录创建可选的 DirectML GPU 混合推理 venv
- 按用户选择准备可选的 DeepSeek Harness 办公侧车和固定依赖
- 按 `resc.net.txt` 多源、可续传地下载缺失的 Vosk、启动动画和 Python 资源，并在解压前校验归档
- 校验随程序提供的官方 UnRAR 解压后端；ONNX 语音包由控制面板按需安装
- 启动桌宠主程序

### 开发者

开始修改前先阅读 [`doc/README.md`](doc/README.md)；多人或多 AI 并行时同时遵循 [`doc/AI协作规范.md`](doc/AI协作规范.md)。

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m compileall config lib scripts install_deps.py install_deps
python lib/core/qt_desktop_pet.py
```

## 运行时数据边界

以下内容是运行时产物或旧版本本机状态，默认不进入 Git：

- `logs/`
- `dist/`
- `__pycache__/`
- `resc/user/`
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
python -m compileall config lib scripts install_deps.py install_deps
python scripts/package_release.py --dry-run
python scripts/package_green_release.py --dry-run
```

按改动范围追加运行测试，例如：

```powershell
py -3 -m unittest discover -s tests -p "test_*music*.py"
py -3 -m unittest tests.test_openai_dashscope_multimodal
```

## 发布包边界

- 普通包和绿色包：源码、默认小型资源、文档，不带 `resc/models/`、`resc/GIF/SEanima/` 或 Python 安装包。
- 安装器依据 `resc.net.txt` 在首次运行时补齐缺失的重型资源。
- 两类包都应脱敏 `config/ollama_config.py` 中的密钥、登录态和会话字段。
- 两类包都携带约 548 KiB 的官方 UnRAR 与许可证，用于桌宠内安装七分卷 ONNX 语音包；模型分卷不进入程序发行包。

## 许可证与声明

- 代码许可见 `LICENSE-CODE`。
- 素材许可见 `LICENSE-ASSETS`。
- 第三方服务、音乐平台和网页自动化能力仅用于学习、研究和个人测试；请自行遵守对应平台规则。
