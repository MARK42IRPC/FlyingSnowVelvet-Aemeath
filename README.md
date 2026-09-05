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
- **离线友好**：正式发布由单 EXE 安装器提供完整隔离运行环境、CPU 推理依赖、Vosk
  模型和启动/退出动画资源，便于无网络环境部署。

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
- 更新器下载并校验同一个离线安装器 EXE；用户确认后退出桌宠，由原生安装器完成目录切换，
  不执行本机 Python 或外部 Node。

### 音乐

- 对外统一入口是 `lib/script/music/service.py`。
- 搜索 provider 位于 `lib/script/music/providers/`，当前包括 QQ / 网易云 / 酷狗。
- `lib/script/cloudmusic/` 已退回内部播放运行时实现，不建议外部直接依赖。
- UI 入口包括音响对象、搜索框、播放列表、进度面板等。

### 系统浏览器登录

- 音乐登录/授权流程使用 Playwright 驱动系统 Microsoft Edge，不下载或内置 Chromium。
- 开发源码可按 `resc.net.txt` 补全 Vosk/动画资源；正式离线安装器会把这些资源直接放入
  payload，且只保留 `resc/GIF/SEanima/` 文件夹，不携带 `SEanima.zip`。

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
| `services/` | 办公 DSH 侧车源码及固定依赖 |
| `resc/` | GIF、字体、音效、模型、离线构建资源与用户运行目录 |
| `scripts/` | 离线发行版构建、依赖和其他维护脚本 |
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
- 在共享语音运行目录创建 DirectML GPU 混合推理 venv；NVIDIA CUDA 环境改由控制面板按需安装
- 准备 DSH 办公后端及固定依赖
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

`resc.net.txt` 是开发环境补全资源的下载清单。正式发行只上传一个带完整隔离
Python/Node/CPU ONNX/DirectML overlay 的
`FlyingSnowVelvet-<version>-Offline-Installer.exe`；安装器内置
`resc/GIF/SEanima/` 文件夹和 Vosk 模型，不依赖用户机器上的 Python、Node 或 CUDA。
构建入口是 `scripts/build_offline_distribution.py` 与
`scripts/build_offline_installer.py`，发布工作流使用当前仓库的临时
`build/offline-release` 目录。

## 常用检查

```powershell
python -m compileall config lib scripts install_deps.py install_deps
python -m unittest discover -s tests -p "test_*.py" -q
```

按改动范围追加运行测试，例如：

```powershell
py -3 -m unittest discover -s tests -p "test_*music*.py"
py -3 -m unittest tests.test_openai_dashscope_multimodal
```

## 发布包边界

- 离线安装器只从当前仓库构建，payload 包含固定 Python 3.11、Node 24.13.0、DSH
  production `node_modules`、CPU `onnx`/`onnxruntime`、`genie-tts` 双语前端、
  `jieba`/`jieba-fast`、Vosk 模型和 `SEanima` 文件夹。
- CUDA、Torch、NVIDIA、TensorRT 不进入基础发行版；DirectML 以独立 overlay 随包提供，
  由设置面板按需启用。
- 安装器使用原生 ZIP 解压和尾部 SHA-256 校验，安装过程显示当前文件、百分比、文件/字节
  进度和预计剩余时间；安装结束后由用户点击“退出安装并启动飞行雪绒”。
- 用户数据、密钥、登录态、日志和缓存保存在 `C:\AemeathDeskPet`，不进入 payload。

## 许可证与声明

- 代码许可见 `LICENSE-CODE`。
- 素材许可见 `LICENSE-ASSETS`。
- 第三方服务、音乐平台和网页自动化能力仅用于学习、研究和个人测试；请自行遵守对应平台规则。
