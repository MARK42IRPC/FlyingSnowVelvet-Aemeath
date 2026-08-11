# 飞行雪绒文档中心

本文档目录以当前源码为准。版本号的唯一来源是 `config/version_info.py`。带有“计划”“清单”字样的旧文件只用于追溯历史，不代表当前待办。

## 首次阅读

| 目标 | 文档 |
| --- | --- |
| 理解工程结构、生命周期、配置、测试和发布 | [维护手册](维护手册.md) |
| 多个 AI 或开发者并行工作 | [AI 协作规范](AI协作规范.md) |
| 开发对象管理器、粒子和效果 | [Script 开发指南](Script开发指南.txt) |
| 修改或新增事件 | [事件系统使用说明](事件系统使用说明.txt) 与 [已注册的事件](已注册的事件.txt) |
| 修改调度、帧循环或延迟任务 | [调度系统使用说明](调度系统使用说明.txt) |
| 修改粒子效果 | [粒子效果说明](粒子效果说明.txt) |
| 修改主题、布局、动效或跨后端绘制语义 | [跨后端视觉表现契约](视觉表现契约.md) |
| Qt 收敛与绘制后端 | [Qt 收敛方案](Qt收敛方案.md) |
| 实现 Windows DirectX 后端 | [DirectX 后端实现方案](DX后端实现方案.md) |
| 修改 ONNX 语音包、安装器或推理桥接 | [ONNX 语音包协议](语音包协议.md) |
| 开发或维护游戏扩展包 | [游戏包格式](游戏包格式.md) 与 [游戏模式说明](游戏模式说明.txt) |
| 准备发行版 | 根目录 `RELEASING.md` |

## 当前工程入口

- 启动入口：`lib/core/qt_desktop_pet.py`
- 应用生命周期：`lib/script/main.py` 中的 `ApplicationState`
- 事件协议：`lib/core/event/center.py`
- 工作台：`lib/script/ui/workbench_window.py`、`lib/script/workbench/`
- 用户配置：`config/user_settings.py`、`config/general_user_settings.py`
- 音乐公开入口：`lib.script.music.get_music_service()`
- 游戏包服务：`lib/script/gemes/MAIN/game_packages.py`
- 发布入口：`scripts/package_release.py`、`scripts/package_green_release.py`
- 共享发布逻辑：`scripts/release_common.py`
- 自动化测试：`tests/test_*.py`

## 文档分层

### 当前维护文档

`README.md`、`维护手册.md`、`AI协作规范.md` 和各专项协议描述当前实现。代码变化后必须同步。

### 用户与扩展协议

事件、调度、粒子、游戏包等文档属于兼容契约。修改对应接口时，应在同一任务中更新文档和测试。

### 历史归档

以下文件记录已完成重构的背景，不能直接作为实施依据：

- `代码收敛整改清单.txt`
- `统一绘制模块改造计划清单.txt`
- `项目全面优化与收敛计划.md`

如历史文档与源码、维护手册冲突，以源码和维护手册为准。

## 文档维护规则

1. 文档描述行为和边界，不复制大段实现代码。
2. 路径、命令和接口名称必须能在仓库中搜索到。
3. 不写容易失效的测试总数、文件总数和包体积，由验证命令实时产生。
4. 新增专项文档时，将其加入本索引并标明权威源码。
5. 删除或替换接口时，同时清理旧文档引用。

文档直接保存在 `doc/` 中，不生成或维护派生的汇总副本；从本索引进入对应文件阅读。
