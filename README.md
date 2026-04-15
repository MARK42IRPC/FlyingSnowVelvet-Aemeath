# 飞行雪绒 LTS 1.0.5 pre3

飞行雪绒是一个以 Windows 10/11 为主要目标平台的桌面宠物项目，围绕 **桌宠展示、AI 伴聊、语音播报、语音识别、音乐播放、可生成场景对象** 这几条主线持续迭代。当前 `pre3` 已作为新一轮开发基线启用，先完成版本号、文档入口与发布脚本基线同步，后续功能与修复将在这一轮迭代中继续累计。

> 当前版本：`LTS1.0.5pre3`  
> 发布日期：`2026-04-15`

---

## 当前状态

- **桌宠主循环稳定可运行**：入口为 `lib/core/qt_desktop_pet.py`，应用编排集中在 `lib/script/main.py`
- **AI 对话链路完整**：支持 OpenAI 兼容 API、本地 Ollama、YuanBao-Free-API 本地中转，以及规则回复兜底
- **语音能力已成体系**：
  - `lib/script/gsvmove/` 负责 GSVmove 文本转语音桥接
  - `lib/script/microphone_stt/` 负责本地 Vosk 识别与 Push-to-Talk
  - 控制面板可直接控制 GSV 是否启用、GSV 语音缓存上限，并可打开缓存目录
- **音乐模块支持多源搜索与路由**：QQ / 网易云 / 酷狗三套 provider 已统一到 `lib/script/music/`
- **对象与粒子仍是项目特色**：雪豹、雪堆、沙发、摩托、闹钟、音响、雪球，以及 16 个已注册粒子
- **运行时数据已和源码分离**：`resc/user/`、`logs/`、`config/music/volume.json`、`config/user_scale.json` 等运行时内容默认不进入 Git

---

## 功能概览

### 1. AI 与工具调度

- `lib/script/chat/` 统一处理输入、人格、上下文、流式输出、自动陪伴
- `lib/script/tool_dispatcher/dispatcher.py` 负责把模型输出中的 `###指令###` 解析为桌宠动作
- 当前常见输入路径：
  - `/命令`：交给 shell
  - `#命令`：交给哈希命令注册表
  - 普通文本：交给聊天系统

### 2. 语音

- `lib/script/gsvmove/service.py` 会按配置决定是否预拉起 GSV 服务
- 控制面板中的 **自动启用 GSV 语音模块** 现在是强开关：
  - 开启：允许预热，也允许 AI 文本转语音
  - 关闭：既不预热，也不处理 AI 文本语音请求
- 新生成的 GSV 音频会保存到 `resc/user/temp/gsv_voice/`
- 控制面板可设置 **GSV 缓存上限**（`1~128`，默认 `20`），超出后会自动清理旧文件

### 3. 语音识别

- `lib/script/microphone_stt/` 提供本地 Vosk 识别、热键按住说话、状态同步
- `install_deps.py` 会在依赖满足时准备语音识别运行环境

### 4. 音乐

- `config/config_music.py` 当前默认平台是 `qq`
- `lib/script/music/service.py` 作为统一入口，内部接三套 provider 和搜索路由
- `lib/script/cloudmusic/` 保留现有桌宠 UI 与播放编排
- 本地缓存默认写入 `resc/user/temp/`

### 5. 对象与粒子

- 对象管理器位于 `lib/script/obj-*`
- 粒子脚本位于 `lib/script/practical/*_particle.py`
- 发现与初始化由 `lib/core/plugin_registry.py` 统一完成

---

## 目录速览

| 路径 | 说明 |
| --- | --- |
| `config/` | 运行配置、共享配置镜像、版本信息 |
| `lib/core/` | 事件中心、窗口、粒子系统、物理、日志、托盘等基础设施 |
| `lib/script/main.py` | 应用生命周期编排、组件初始化、退出清理 |
| `lib/script/chat/` | 聊天、人格、流式呈现、AI 客户端 |
| `lib/script/gsvmove/` | GSVmove 文本转语音桥接 |
| `lib/script/microphone_stt/` | 本地语音识别、Push-to-Talk |
| `lib/script/music/` | 多源音乐服务抽象、provider 与搜索路由 |
| `lib/script/cloudmusic/` | 音乐 UI 与历史播放逻辑 |
| `lib/script/ui/` | 控制面板、气泡、命令框、音乐搜索框等 UI |
| `doc/` | 中文说明文档、开发贡献、赞助名单 |
| `scripts/` | 文档门户生成、发布打包、资源整理脚本 |
| `services/` | YuanBao-Free-API 服务源码与离线 bundle |
| `resc/` | GIF、字体、音效、离线资源和运行时用户目录 |

---

## 快速开始

### 面向使用者

1. 安装 Python 3.7~3.13（推荐 3.10+）
2. 在项目根目录运行：

```powershell
python install_deps.py
```

或直接双击：

- `安装依赖.bat`
- `启动程序.bat`

安装脚本会尝试完成：

- 发现可用 Python 并写入 `py.ini`
- 安装 `requirements.txt`
- 准备本地语音识别模型与部分运行资源
- 启动桌宠主程序

### 面向开发者

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m compileall config lib scripts install_deps.py
python lib/core/qt_desktop_pet.py
```

---

## 配置与运行时数据

### 共享配置

- 项目会优先使用 `C:\AemeathDeskPet\config` 中的共享配置
- 共享配置缺键时会按当前项目模板自动补齐
- AI 面板保存时会同步写回共享配置

### 运行时数据目录

以下内容默认视为运行时产物，不应提交到 Git：

- `logs/`
- `resc/user/`
- `dist/`
- `config/music/volume.json`
- `config/user_scale.json`
- `services/storage_state.json`

### GSV 语音缓存

- 路径：`resc/user/temp/gsv_voice/`
- 默认保留：`20` 条
- 控制入口：控制面板 → AI 设置 → GSV 缓存上限 / 打开缓存文件夹

---

## 文档入口

仓库内主要文档如下：

- `README.md`：仓库总览
- `CHANGELOG.md`：版本变更
- `CONTRIBUTING.md`：协作与提交约定
- `RELEASING.md`：打包与发版步骤
- `doc/README.txt`：中文文档索引
- `doc/Script开发指南.txt`：扩展开发说明
- `doc/事件系统使用说明.txt`：事件总线说明
- `doc/调度系统使用说明.txt`：定时器与任务调度说明
- `doc/粒子效果说明.txt`：已注册粒子说明
- `doc/音乐多源优化方案计划.txt`：音乐多源能力现状与后续计划
- `doc/贡献名单和主播的狗盆/开发贡献.txt`：项目贡献与外部依赖说明
- `doc/贡献名单和主播的狗盆/感谢大佬投喂名单.txt`：赞助名单

文档门户可通过以下命令生成：

```powershell
python scripts/generate_doc_portal.py
```

生成结果：

- `AA使用必读.html`

---

## 打包与发布

普通发布包：

```powershell
python scripts/package_release.py --version LTS1.0.5pre3
```

绿色资源包：

```powershell
python scripts/package_green_release.py --version LTS1.0.5pre3
```

两者默认都会排除运行时用户数据；绿色包会额外保留模型与浏览器资源，适合离线分发。

更完整的发布步骤请看 `RELEASING.md`。

---

## 版本说明

`pre3` 当前作为新一轮开发基线，先完成以下准备：

- 项目版本号切换到 `LTS1.0.5pre3`
- 资料舱门户、打包命令与仓库入口文档同步到 `pre3`
- 后续功能、修复与兼容性调整将继续累计到 `CHANGELOG.md`
- `pre2` 已发布内容保留在历史变更记录中

详见 `CHANGELOG.md` 与 `AA更新日志.txt`。

---

## 许可证

- 源代码遵循 `LICENSE-CODE`
- 非代码资源遵循 `LICENSE-ASSETS`

请勿将运行时用户数据、登录态、缓存音频或个人密钥打包再分发。
