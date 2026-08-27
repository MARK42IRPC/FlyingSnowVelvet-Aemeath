---
name: fsv-workbench-ui
description: Use when building or changing the office page, approval dialog, workbench pages, pet controls, bubbles, or other desktop UI in Flying Snow Velvet.
---

# Workbench UI

工作台和办公页应保持桌宠现有的紧凑、可扫描和粉青视觉语言。

- 新工具页注册元数据和无参数 factory，首次访问前不构造页面。支持嵌入和独立窗口时使用 `QtWorkbenchToolPage` 及独立实例。
- 页面刷新使用工作台共享接口，不让宿主识别页面私有刷新方法；关闭、隐藏和重建时释放信号、定时器、后台任务和临时窗口。
- 复用 `office_style.py`、共享主题和视觉 presenter。不要在页面或权限弹窗中复制颜色、字体、按钮状态或气泡几何常量。
- 页面 section 使用完整宽度布局和受约束内容，不在卡片里套卡片，不用纯装饰性渐变、光斑或超大营销式区域。
- 任务列表、状态、Todo、推理文本和工具事件优先支持扫描、比较、恢复、取消和删除确认；运行中任务不能直接删除。
- 权限弹窗明确显示工具、理由、目标和当前任务范围，允许/始终允许/拒绝的行为交给控制器，不在 UI 中自行修改策略。
- 气泡、语音、思考状态和办公页面状态要避免互相覆盖；固定格式控件使用稳定尺寸和响应式约束，检查窄窗口和高 DPI。

视觉改动完成后保留 Qt 基准，并按任务需要验证 DX parity、截图、DPI 和真实硬件手工项。
