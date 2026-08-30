# Contributing Guide

感谢你愿意改进飞行雪绒。这个项目已经包含桌宠 UI、事件系统、AI、语音、音乐、本地服务和发布脚本，多数问题都跨模块发生。提交时请优先保持边界清晰、改动可验证。

开始前先阅读 `doc/README.md` 和 `doc/维护手册.md`。多人或多 AI 并行工作时，必须同时遵循 `doc/AI协作规范.md`。

## 基本原则

- **小步提交**：一次提交解决一个主题，避免把重构、配置、素材、运行产物混在一起。
- **先看入口**：应用生命周期看 `lib/script/main.py`，事件协议看 `lib/core/event/center.py`，插件发现看 `lib/script/plugin_registry.py`。
- **运行时不入库**：不要提交 `logs/`、`dist/`、`resc/user/`、`resc/models/`、`resc/playwright/`、`resc/GIF/SEanima/`、`py.ini`、`__pycache__/` 等文件。
- **重型资源外置**：`resc.net.txt` 是 Vosk、启动动画、浏览器运行时和 Python 安装器的唯一下载清单，发布包不携带这些资源。
- **用户配置脱敏**：涉及 API Key、登录态、Cookie、storage state 的改动必须确认不会进入发布包。
- **默认与用户值分离**：`config/config_*.py` 只保存默认值；普通用户设置通过 `config.user_settings` 写入稀疏覆盖，禁止运行时改写 Python 配置源码。
- **单一写入源**：状态写入 `user/state`，密钥写入 `user/secrets`，缓存写入 `cache`；旧 `config`、`resc/user` 路径只允许迁移读取。

## 环境准备

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

也可以运行：

```powershell
python install_deps.py
```

安装脚本会准备 Python 包、语音识别模型、本地网页中转服务和可选浏览器离线运行时。

## 代码组织约定

### 生命周期

- 启动、预热、退出清理统一由 `ApplicationState` 编排。
- 新增长生命周期组件时必须提供 `cleanup()`。
- 退出链路中不要直接强退 Qt；优先发布事件或接入已有清理阶段。

### 事件系统

- 跨模块通信优先走 `EventCenter`。
- `subscribe()` 必须有对称 `unsubscribe()`。
- 事件 payload 要保持向后兼容；无法兼容时同步更新 `doc/已注册的事件.txt` 和 `doc/事件系统使用说明.txt`。

### AI 与聊天

- OpenAI 兼容 API 逻辑集中在 `lib/script/chat/api_client_openai.py`。
- Ollama 逻辑集中在 `lib/script/chat/api_client_ollama.py`。
- 图片输入编码走 `lib/script/chat/vision_codec.py`，不要在调用点重复实现 base64/data URL 逻辑。
- 新增厂商兼容分支时要优先做“尝试请求 + 明确错误”策略，避免只靠模型名猜能力。

### 音乐

- 外部调用只使用 `lib.script.music.get_music_service()`。
- `lib/script/cloudmusic/` 是内部播放运行时，不再作为新的外部依赖入口。
- 新增平台 provider 时放入 `lib/script/music/providers/`，并接入 `MusicService` 与搜索路由。

### 本地托管服务

- 本地子进程服务必须在所属服务内封装进程所有权、健康检查和幂等清理。
- 进程启动、健康检查、兜底清理要能重复调用且幂等。
- 浏览器自动化相关运行目录必须写入已忽略路径。

### UI 与配置

新增或修改 AI 控制面板配置时，通常需要同步：

- `lib/script/ui/ai_settings_panel.py`
- `lib/script/ui/ai_settings_storage.py`
- `lib/script/ui/ai_settings_validators.py`
- `config/ollama_config.py`

新增 UI 单例时，也要检查 `lib/script/ui/shutdown.py` 是否需要纳入统一隐藏/清理。

## 提交前检查

至少运行：

```powershell
python -m compileall config lib scripts install_deps.py
python scripts/package_release.py --dry-run
```

如果改了绿色包边界，追加：

```powershell
python scripts/package_green_release.py --dry-run
```

如果改了测试覆盖范围，运行对应 unittest，例如：

```powershell
py -3 -m unittest tests.test_openai_dashscope_multimodal
py -3 -m unittest discover -s tests -p "test_*music*.py"
```

## 文档同步

以下变更必须同步文档：

- 事件协议变化：`doc/事件系统使用说明.txt`、`doc/已注册的事件.txt`
- 调度系统变化：`doc/调度系统使用说明.txt`
- 粒子系统变化：`doc/粒子效果说明.txt`
- 扩展开发方式变化：`doc/Script开发指南.txt`
- 发布包边界变化：`README.md`、`RELEASING.md`
- 版本行为变化：`CHANGELOG.md`

## 提交信息建议

使用简短中文或 Conventional Commit 均可，重点是说明影响范围：

- `fix: 修正普通包浏览器离线包边界`
- `feat: 增加音乐 provider 路由`
- `docs: 重写项目说明文档`
- `refactor: 收敛本地服务生命周期`

## Issue / PR 建议内容

请尽量提供：

- Windows 版本
- Python 版本
- 复现步骤
- 是否启用 AI、GSV、STT、音乐、本地网页中转
- 相关日志、截图或控制台输出

欢迎提交边界清楚、验证充分的小改动。大范围重构请先说明目标、迁移策略和回滚方式。
