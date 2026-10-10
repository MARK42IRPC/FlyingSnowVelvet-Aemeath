"""故障跟踪窗口的「数据视图模型」：筛选、记录键、级别配色与详情渲染。

批次 3 后续轮次从 `bug_tracker_window.BugTrackerWindow` 切出。这一轮把**与控件树无关的
查询与渲染**那一半搬出来——按启动实例 + 等级筛选记录（`_filtered_records` /
`_record_matches_level_filters`）、模块键与时间 / 级别格式化、级别配色、详情文本渲染，
以及「当前选中记录」。窗口本体只保留控件装配、信号回调与导出 / 打开源码等动作。

本模块不再 `import PyQt5`：两处原本夹带的工具包事实改为注入或留给宿主翻译。

- “列表项把记录序号藏在哪个角色里”是 Qt 事实。窗口在构造时读一次 `int(Qt.UserRole)`
  并交给 `self._record_row_role`，本模块只读这个整数。
- 等级配色依赖窗口模块的主题色全局（`_DANGER` / `_WARNING` / `_CYAN`）。本模块只回答
  “这条记录属于哪个语义等级”（`_level_color_name` → `danger` / `warning` / `cyan`），
  由窗口本体把它翻译成 `QColor`。

其余方法按 `self` 解析 `_records` / `_error_list` 等窗口状态。
"""

from __future__ import annotations

from pathlib import Path

from lib.script.bug_tracker.storage import BugRecord


class BugTrackerDataMixin:
    """故障跟踪窗口的筛选与渲染；由 `BugTrackerWindow` 混入。"""

    def _level_color_name(self, levelno: int) -> str:
        """记录的语义等级色名（`danger` / `warning` / `cyan`），由宿主翻译成颜色。"""

        if levelno >= 40:
            return "danger"
        if levelno >= 30:
            return "warning"
        return "cyan"

    def _filtered_records(self) -> list[BugRecord]:
        records = self._records
        if self._instance_filter:
            records = [record for record in records if record.instance_id == self._instance_filter]
        return [record for record in records if self._record_matches_level_filters(record)]

    def _record_matches_level_filters(self, record: BugRecord) -> bool:
        if record.levelno >= 40:
            return bool(self._level_filters.get("error", True))
        if record.levelno >= 30:
            return bool(self._level_filters.get("warn", True))
        return bool(self._level_filters.get("info", True))

    def _module_key(self, record: BugRecord) -> str:
        return record.module or Path(record.pathname).stem or record.logger or "unknown"

    def _format_when(self, record: BugRecord) -> str:
        dt = record.iso_datetime
        if dt is None:
            return record.timestamp[:19] if record.timestamp else "--"
        return dt.strftime("%H:%M:%S")

    def _level_name(self, levelno: int) -> str:
        if levelno >= 50:
            return "CRITICAL"
        if levelno >= 40:
            return "ERROR"
        if levelno >= 30:
            return "WARN"
        if levelno >= 20:
            return "INFO"
        return "DEBUG"


    def _render_detail(self, record: BugRecord) -> str:
        parts = [
            f"实例: {record.instance_label or '-'}",
            f"日志文件: {record.log_path or '-'}",
            f"时间: {record.timestamp or '-'}",
            f"级别: {record.level or '-'} ({record.levelno})",
            f"日志器: {record.logger or '-'}",
            f"模块: {self._module_key(record)}",
            f"位置: {record.pathname or '-'}:{record.lineno or 0}",
            f"函数: {record.func_name or '-'}",
            f"进程: {record.process or 0}",
            f"线程: {record.thread_name or '-'}",
            "",
            f"消息: {record.message or '-'}",
        ]
        if record.exception:
            parts.extend(["", "异常堆栈:", record.exception])
        if record.stack_info:
            parts.extend(["", "附加堆栈:", record.stack_info])
        return "\n".join(parts)

    def _selected_record(self) -> BugRecord | None:
        current = self._error_list.currentItem()
        if current is None:
            return None
        idx = int(current.data(self._record_row_role) or 0)
        records = self._filtered_records()
        if 0 <= idx < len(records):
            return records[idx]
        return None


__all__ = [
    "BugTrackerDataMixin",
]
