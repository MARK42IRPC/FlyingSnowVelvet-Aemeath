---
name: fsv-browser-ui-check
description: Use when verifying a local web UI with Flying Snow Velvet's bundled browser runtime, including DOM, console, screenshots, assets, responsive layouts, and interaction state.
---

# Browser UI Check

项目已内置浏览器运行时。使用桌宠提供的浏览器桥接能力进行检查，不下载或安装另一套浏览器。

- 默认从本地页面、开发服务器或用户明确给出的地址开始；先确认页面、端口和任务工作区范围。
- 检查页面是否真正加载、关键 DOM 是否存在、控制台是否有错误、资源是否 404，以及桌面和窄视口下是否出现空白、溢出或重叠。
- 对需要视觉判断的页面保留有边界的截图和明确观察结果；不要把截图、cookie、storage state 或账号数据写入源码和发行包。
- 输入、点击和导航优先使用只读检查。登录、提交、上传、下载、删除和修改外部数据属于有副作用操作，交给桌宠审批。
- 不把浏览器结果当作代码测试的替代品；结合目标单元测试、编译检查和页面宿主生命周期验证。
- 浏览器运行时故障时报告运行时错误、地址、复现步骤和已看到的页面状态，不要自行联网下载运行时修复。

Skill 本身不授予浏览器权限；只有当前 DSH profile 暴露浏览器工具或桌宠桥接服务时才执行浏览器操作。
