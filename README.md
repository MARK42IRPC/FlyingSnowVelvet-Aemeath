# 飞行雪绒 LTS 1.0.5 pre1

跨平台桌面宠物（主打 Windows 10/11）与音乐 / AI 伴聊一体化体验，基于 PyQt5、事件总线与动态插件体系构建。当前版本聚焦于稳定性和运行时瘦身，兼容本地 Ollama、大模型 OpenAI 兼容 API 以及规则回复降级。

> TL;DR：`install_deps.py` 会自动发现可用 Python、安装依赖、下载 Vosk 模型并启动宠物；`lib/core/qt_desktop_pet.py` 是桌宠入口，`lib/script/main.py` 承担 orchestration。

---

## 功能速览

- **AI 伴聊与工具调度**：`lib/script/chat` 统一了本地 Ollama、OpenAI 兼容 API、YuanBao-Free-API 本地中转服务以及规则回复模式，并通过 `lib/script/tool_dispatcher` 将模型输出中的 `###指令###` 转译为宠物命令（音乐、音量、闹钟、雪豹/沙发/摩托等对象生成、清理、瞬移、回忆等）。
- **语音 / 语音识别**：`lib/script/voice` 为声音请求抽象层，`lib/script/gsvmove` 桥接 GSVmove TTS，`lib/script/microphone_stt` 负责本地 Vosk 识别（含 Push-to-Talk 管理器、ASCII 目录镜像、静音检测）。`config/config_voice.py` 定义运行参数。
- **音乐系统**：`lib/script/cloudmusic` orchestrator 对接 `NetEase / QQ / Kugou`，由 `lib/script/music/providers/*` 提供统一 adapter；窗口 UI（播放队列 / 搜索 / 控制按钮）位于 `lib/script/ui`。事件在 `EventType.MUSIC_*` 命名空间中解耦。
- **动态插件世界**：管理器放在 `lib/script/obj-*`（雪豹、雪堆、沙发、摩托、闹钟、音响），粒子脚本在 `lib/script/practical/*_particle.py`。`lib/core/plugin_registry.py` 承担发现、注册、初始化、清理。
- **高频命令**：`#雪豹 [数量]`、`#雪堆 [数量]`、`#沙发 [数量]`、`#沙发重力`、`#摩托 [数量]`、`#闹钟 [秒]`、`#闹钟重力`、`#音响 [数量]`、`#音响重力`、`#退出音乐登录`、`#清理`。`/` 前缀执行 shell，普通文本触发聊天。
- **UI 与托盘**：`lib/core/pet_window.py` 渲染宠物精灵、承载粒子覆盖层 `lib/core/qt_particle_system.py`，`lib/core/tray_icon.py` 控制托盘（含“清理历史”动作）。UI 组件拆分在 `lib/script/ui/*`。
- **文档门户**：`doc/` 下包含事件/调度/粒子/Script 开发指南（中文），`scripts/generate_doc_portal.py` 可一键生成 `AA使用必读.html`（贡献名单 + 文档 + 赞助墙）。

---

## 架构快照

| 目录 / 文件 | 说明 |
| --- | --- |
| `install_deps.py` | 自动扫描 Python、补齐 pip、测延迟安装依赖、下载 Vosk 模型、写 `py.ini` 并启动主程序。 |
| `config/` | 运行时配置 facades（UI、动画、音乐、语音、AI、timeout、shared storage 等）。 |
| `lib/core/` | 基础设施：事件中心、粒子系统、定时器、日志、cmd registry、physics、托盘等。 |
| `lib/script/main.py` | 应用 orchestrator：订阅生命周期事件、加载 GIF、初始化管理器、UI、托盘、清理资源。 |
| `lib/script/app/` | 单实例锁、桌面快捷方式、Qt runtime、硬件探测。 |
| `lib/script/chat/` | AI handler、persona、自动陪伴、流式呈现、Ollama/OpenAI 客户端。 |
| `lib/script/gsvmove/` / `microphone_stt/` | GSVmove TTS + 本地语音识别服务。 |
| `lib/script/cloudmusic/` & `lib/script/music/providers/` | 音乐 orchestrator 与 provider。 |
| `lib/script/ui/` | 命令框、气泡、AI 设置面板、音乐控制、语音指示器、提示面板等。 |
| `doc/` | 迁移清单、事件/调度/粒子说明、Script 开发指南、贡献列表。 |
| `resc/` | GIF / 音频 / 字体 / 模型等资源（`resc/models` 由 install 脚本下载）。 |

事件、命令、插件均经过 `lib/core/event/center.py` 与 `lib/core/plugin_registry.py` 解耦处理，可参考 `doc/事件系统使用说明.txt` 与 `doc/Script开发指南.txt`。

---

## 快速开始

### 1. 面向玩家

1. 安装 Python 3.7~3.13（推荐 3.10+，可直接用系统 `py` 启动器）。
2. 克隆/下载仓库后运行 `安装依赖.bat`（或 `python install_deps.py`）。脚本会：
   - 发现可用 Python 并写入 `py.ini`（路径采用 ASCII 安全形式）；
   - 自动安装 `requirements.txt` 中的依赖，并优先解压仓库内置的 `services/bundles/yuanbao-free-api-main.zip`；
   - 下载 Vosk 中/英文模型到 `resc/models/`（若 `sounddevice+vosk` 就绪）；
   - 启动 `lib/core/qt_desktop_pet.py` 背景进程。
3. 右键宠物打开命令框：`/cmd` 执行 shell、`#命令` 操作管理器、普通文本聊天。

### 2. 面向开发者 / 调试

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m compileall config lib install_deps.py
python lib/core/qt_desktop_pet.py
```

- `scripts/generate_doc_portal.py` 生成带样式的资料舱 HTML。
- `scripts/package_release.py --version LTS1.0.5pre1` 会在 `dist/` 产出瘦身 zip（排除日志、用户数据、Vosk 模型等），详见下文。
- `logs/` 自动滚动保留最近 5 份，`logs/app_*.log` 便于排查。

---

## 配置要点

### AI / 大模型

- `config/ollama_config.py` 负责选择 **OpenAI 兼容 API** 与本地 **Ollama**。默认会尝试从以下环境变量加载密钥：`FLYINGSNOWVELVET_API_KEY`、`FLYINGSNOW_API_KEY`、`OPENAI_API_KEY`。仓库默认不内置任何密钥；若没有设置，`API_KEY` 保持为空，可在 AI 设置面板或直接编辑本地配置文件。
- 若启用 `YuanBao-Free-API` 且接口地址指向 `http://127.0.0.1:8000/v1`，桌宠会在启动时自动解压并拉起仓库内置的本地中转服务；该服务使用 `API_KEY` 作为 Bearer 访问密钥，并在首次启动时生成二维码图片供扫码登录。
- `FORCE_REPLY_MODE` 支持：`''` 自动、`'0'` 强制外部 API、`'2'` 本地 Ollama、`'3'` 规则回复、`'4'` 优先走 YuanBao-Web。
- 人格脚本默认 `resc/persona.txt`，可通过 `PERSONA_FILE` 指定自定义文件。

### 语音与识别

- `install_deps.py` 会在检测到 `sounddevice` 和 `vosk` 安装后，自动下载 `vosk-model-small-cn-0.22` / `vosk-model-small-en-us-0.15`。也可手动放入 `resc/models/`。
- `install_deps.py` 会优先检查 `resc/playwright/` 与 `resc/bundles/` 下的 Chromium 离线资源；只有本地资源不存在时，才回退到在线执行 `playwright install chromium`。
- `lib/script/gsvmove/service.py` 会在 `APP_PRE_START` 背景启动本地 GSVmove（默认从共享目录的 `start_gsvmove.bat`），并监听 `EventType.AI_VOICE_REQUEST`。
- Push-to-Talk 逻辑位于 `lib/script/microphone_stt/push_to_talk.py`，可在 `config/config_voice.py` 自定义快捷键、输入增益等。

### 音乐与对象

- `config/config_music.py`、`config/config_entities.py` 定义音响/播放队列/对象的 UI、物理、登陆参数。
- `EventType.MUSIC_*` 事件串联 UI 与 `CloudMusicManager`，命令框 `#音响`、`#音响重力` 等均在管理器内实现。

---

## 常用命令 / 输入链路

- `/something`：交给 `CmdCenter` 执行 shell。
- `#command`：下发到哈希命令注册表，例如：
  - `#雪豹 3`：生成 3 只雪豹；
  - `#摩托 1`、`#闹钟 00:01:30`、`#清理` 等；
  - `#退出音乐登录`：注销当前音乐账号。
- 普通文本：交给 `ChatHandler`，若开启多模态关键词会触发 `lib/script/chat/vision_capture.py` 截图；流式 chunk 推送到 UI 气泡与 ToolDispatcher。

---

## 脚本与工具

- `scripts/generate_doc_portal.py`：读取 `doc/*.txt` 与贡献/赞助清单，渲染 `AA使用必读.html`。用于 GitHub Release 附件或 wiki。
- `services/bundles/yuanbao-free-api-main.zip`：内置的 `chenwr727/yuanbao-free-api` 源码压缩包；`install_deps.py` 与桌宠启动时会优先使用它准备本地中转服务。
- `scripts/package_release.py`：
  - `python scripts/package_release.py --version LTS1.0.5pre1`：产出 `dist/FlyingSnowVelvet-LTS1.0.5pre1.zip`；
  - `--dry-run`：仅打印将被打包的文件，CI 用于校验；
  - 自动排除 `logs/`、`resc/models/`、`resc/user/`、`__pycache__/`、`.git/`、`.github/` 等运行时或仓库文件，并写入 `.keep` 占位符确保必要目录存在。
- `scripts/package_green_release.py`：
  - `python scripts/package_green_release.py --version LTS1.0.5pre1`：产出包含 `resc/models/` 与 `resc/playwright/` 资源的 `dist/FlyingSnowVelvet-LTS1.0.5pre1-green.zip`，适合 QQ / 网盘分发。
- `scripts/fetch_playwright_chromium_resource.py`：
  - `python scripts/fetch_playwright_chromium_resource.py`：将本机已安装的 Playwright Chromium 运行时重打包到 `resc/playwright/`，供 green 包离线使用。

---

## 文档与迁移笔记

- `doc/README.txt`：文档索引、状态速览（启动入口、架构、命令列表）。
- `doc/Script开发指南.txt`：插件/粒子/管理器开发规范与模板。
- `doc/事件系统使用说明.txt`、`doc/调度系统使用说明.txt`、`doc/粒子效果说明.txt`：事件协议、定时任务、粒子触发。
- `doc/迁移清单任务.txt`：当前 Phase 8 迁移计划（84% 完成），列出拆分、瘦身、已收敛的修改记录。
- `doc/贡献名单和主播的狗盆/`：贡献者名单、赞助墙素材；发布前运行脚本刷新 HTML。

---

## 发布流程概览

详见 [RELEASING.md](RELEASING.md)，核心步骤：

1. 更新版本号 & `CHANGELOG.md`；
2. 运行 `python -m compileall config lib install_deps.py scripts`；
3. 运行 `python scripts/generate_doc_portal.py`；
4. 执行 `python scripts/package_release.py --version <tag>`（产出 zip + `manifest.json`）；
5. 创建 Git 标签与 GitHub Release，上传 zip + `AA使用必读.html`。

GitHub Actions (`.github/workflows/ci.yml`) 会在 Windows 环境安装依赖并执行 `compileall` + 打包脚本干跑，阻止语法与打包回归。

---

## 贡献

- 请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，遵循 `doc/` 内部规范，并在合并前补齐文档/迁移清单。
- 提交 PR 前至少运行：
  - `python -m compileall config lib install_deps.py`;
  - `python scripts/package_release.py --dry-run`（确保清单无误）;
  - `python scripts/generate_doc_portal.py`（若文档有更新）。
- 新增/修改管理器、粒子、配置后，请同步更新 `doc/*.txt` 以及 `CHANGELOG.md`。

---

## 版本与路线图

当前版本：`LTS1.0.5pre1`（2026-03-18）。主要变更请查阅 [CHANGELOG.md](CHANGELOG.md)。迁移计划仍在 Phase 8，优先清理旧结构、瘦身 orchestrator、拆分 `install_deps.py`。

---

## 许可证

- **源代码**（`config/`, `install_deps.py`, `lib/`, `scripts/`, 文本文档等）采用 [Apache License 2.0](LICENSE-CODE)。提交贡献即表示你同意以该许可证授权，并授予相应的专利许可。
- **非代码资源**（`resc/` 下的字体/音频/GIF/图片、贡献墙素材等）遵循 [Flying Snow Velvet Assets License](LICENSE-ASSETS)。这些文件仅可用于运行和展示桌宠，不得单独分发或商用，除非获得作者书面授权。
