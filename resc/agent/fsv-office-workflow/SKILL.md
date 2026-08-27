---
name: fsv-office-workflow
description: 'Use when working on a Flying Snow Velvet office-mode task, continuing a task, changing reasoning effort, handling #new, or responding to task and approval state.'
---

# Office Task Workflow

办公模式是一条持续的 coding-agent 会话，不是每条消息都创建新任务。

- 普通办公输入优先续接当前活动任务；没有活动任务时续接 `updated_at` 最新且有 DSH session 的任务。
- 只有用户明确使用 `#new` 或点击新建任务时才创建新任务。`#new` 本身不发送空提示词，下一条普通消息才开始新任务。
- 同一时间只运行一个前台办公任务。新要求到达时，先判断是当前任务的 follow-up、取消还是新任务。
- 办公任务的工作目录默认为桌面下的 `飞行雪绒办公区`。需要访问其它目录时先说明范围并使用宿主提供的工作目录和沙箱。
- 用 Todo、工具事件和简短状态反馈保持进度可见。不要把内部推理全文伪装成最终答复。
- “思考中”由桌宠气泡队列显示；稳定的最终答复进入最终气泡和语音流程。代码围栏、推理文本、Todo 和工具事件不朗读。
- 权限决定只能通过桌宠审批通道返回。允许一次、任务内始终允许和拒绝的语义由宿主决定，不得用命令或提示词绕过审批。

任务完成、取消、失败或应用退出时清理任务级提权状态，并确保侧车和后台工作被回收。
