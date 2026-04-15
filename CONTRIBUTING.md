# Contributing Guide

感谢你为 **飞行雪绒 LTS** 提交改动。当前仓库处于 `LTS1.0.5pre3` 阶段，核心要求不是“堆功能”，而是 **保证运行稳定、运行时数据边界清晰、文档同步更新**。

## 1. 基础原则

- 目标平台以 **Windows 10/11** 为主
- 推荐 Python 版本：**3.10+**
- 提交前请确认：
  - 没有把个人密钥、Cookie、登录态写进仓库
  - 没有把 `logs/`、`resc/user/`、`dist/`、本地缓存等运行产物带进 Git
  - 新增功能带有对应清理逻辑和文档更新

特别注意以下文件是运行时配置，不应重新纳入 Git 跟踪：

- `config/music/volume.json`
- `config/user_scale.json`

## 2. 本地开发

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m compileall config lib scripts install_deps.py
```

常用命令：

- 启动桌宠：`python lib/core/qt_desktop_pet.py`
- 生成资料舱：`python scripts/generate_doc_portal.py`
- 检查打包清单：`python scripts/package_release.py --dry-run`

## 3. 代码约定

### 模块与事件

- 跨模块通信优先走 `EventCenter`
- 所有 `subscribe()` 都必须有对称的 `unsubscribe()`
- 长生命周期组件必须提供 `cleanup()`

### UI 与配置

- UI 组件统一放在 `lib/script/ui/`
- 新增 AI 面板配置项时，至少同步以下位置：
  - `lib/script/ui/ai_settings_panel.py`
  - `lib/script/ui/ai_settings_storage.py`
  - `lib/script/ui/ai_settings_validators.py`
  - `config/ollama_config.py`

### 运行时文件

- 用户数据放在 `resc/user/` 或共享配置目录
- 不要把日志、缓存、语音文件、临时资源写入源码目录中的受跟踪文件
- 像 `GSV` 语音缓存这类运行数据，应写到 `resc/user/temp/` 这一类已忽略目录

## 4. 提交前自检

请至少执行：

```powershell
python -m compileall config lib scripts install_deps.py
python scripts/package_release.py --dry-run
```

如果改了文档或门户脚本，再执行：

```powershell
python scripts/generate_doc_portal.py
```

如果改了以下链路，建议做一次手动运行验证：

- AI 回复
- GSV 语音
- 麦克风识别
- 音乐搜索 / 播放
- 对象生成 / 清理
- 控制面板保存配置

## 5. 文档同步

以下类型改动必须同步文档：

- 事件协议变化 → `doc/事件系统使用说明.txt`
- 调度逻辑变化 → `doc/调度系统使用说明.txt`
- 粒子变化 → `doc/粒子效果说明.txt`
- 扩展开发方式变化 → `doc/Script开发指南.txt`
- 版本行为变化 → `README.md`、`CHANGELOG.md`、`RELEASING.md`

## 6. 提交信息建议

- `feat: ...`
- `fix: ...`
- `docs: ...`
- `chore: ...`

请尽量让提交信息直接说明影响范围，例如：

- `feat: 增加GSV语音缓存管理`
- `fix: 修正资料舱脚本目录引用`
- `docs: 重写pre3版本说明文档`

## 7. Issue / PR 建议内容

请尽量带上：

- Windows 版本
- Python 版本
- 复现步骤
- 是否启用 GSV / STT / 音乐模块
- 对应日志或截图

欢迎提交小而准、边界清晰的改动。对于会改变运行时目录、配置落地方式或发布包结构的提交，请务必同步说明原因。
