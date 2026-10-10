"""故障跟踪窗口的「数据视图模型」：筛选、记录键、级别配色与详情渲染。

批次 3 后续轮次从 `bug_tracker_window.BugTrackerWindow` 切出。这一轮把**与控件树无关的
查询与渲染**那一半搬出来——按启动实例 + 等级筛选记录（`_filtered_records` /
`_record_matches_level_filters`）、模块键与时间 / 级别格式化、级别配色、详情文本渲染，
以及「当前选中记录」。窗口本体只保留控件装配、信号回调与导出 / 打开源码等动作。

切分保持逐行等价：`BugTrackerDataMixin` 的方法体与搬出前一致（缩进也未变），
`BugTrackerWindow` 只是多继承本 mixin。`_color_for_level` 依赖模块级主题色全局
（`_DANGER` / `_WARNING` / `_CYAN`），为避免复制两份主题状态，混入方法经 `self` 的
工厂钩子 `_level_color(name)` 取色；该钩子由窗口本体实现，返回的就是本模块全局的同一批
`QColor`。其余方法按 `self` 解析 `_records` / `_error_list` 等窗口状态。

本模块仍在 `lib/script/ui/` 下、仍 `import PyQt5`，按第 34.2 节规则登记
`frozen_ui_qt_importers`。
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

from lib.script.bug_tracker.storage import BugRecord


class BugTrackerDataMixin:
    """故障跟踪窗口的筛选与渲染；由 `BugTrackerWindow` 混入。"""

    def _level_color(self, name: str) -> QColor:  # pragma: no cover - 由宿主覆盖
        raise NotImplementedError

    def _color_for_level(self, levelno: int) -> QColor:
        if levelno >= 40:
            return self._level_color("_DANGER")
        if levelno >= 30:
            return self._level_color("_WARNING")
        return self._level_color("_CYAN")

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
        idx = int(current.data(Qt.UserRole) or 0)
        records = self._filtered_records()
        if 0 <= idx < len(records):
            return records[idx]
        return None


__all__ = [
    "BugTrackerDataMixin",
]
