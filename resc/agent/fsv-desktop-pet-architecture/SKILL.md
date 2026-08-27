---
name: fsv-desktop-pet-architecture
description: Use when changing Flying Snow Velvet core, chat, office, events, workbench, Qt or DirectX integration, lifecycle cleanup, or cross-module contracts.
---

# Desktop Pet Architecture

先以源码、测试和当前维护文档为事实源，再选择最小的模块边界。

- `lib/core` 提供事件、绘制、调度、后端和生命周期协议；`lib/script` 承载聊天、办公、服务和业务；核心层不得反向依赖具体业务 provider 或 Qt 页面。
- 跨模块调用优先使用明确服务接口。事件用于广播、解耦或跨线程切换，并为每个订阅提供对称取消。
- 长生命周期对象必须有幂等 `cleanup()`，后台 I/O 使用 `ComputeHub`，子进程只由拥有者保存和清理。
- Qt 类型不得泄漏到核心协议、共享视觉描述或业务事件。工作台页面使用页面注册表和 factory，嵌入实例与独立窗口实例不能互相改父对象复用。
- 修改聊天或办公路由时检查模式 generation、迟到结果、气泡队列、语音请求和退出链路，避免陪伴结果污染办公模式。
- 修改 UI 时复用现有工作台主题、共享视觉 presenter 和 `office_style.py`，不要复制颜色常量或为单一后端增加业务分支。
- 接口、配置、事件、发行边界或扩展契约变化必须同步文档和测试；不要恢复已删除的旧兼容壳。

开始编辑前读取仓库根 `AGENTS.md`、`doc/README.md`、`doc/维护手册.md` 和目标模块对应的专项协议。
