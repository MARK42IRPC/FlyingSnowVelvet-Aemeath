"""流式回复记忆模块

职责：
- 订阅 INPUT_CHAT / STREAM_FINAL 事件
- 将用户输入与模型回复按日期分文件写入 resc/user/memory/ 目录
- 写入前移除 ###指令### 标记，并解析 ///主题///
- 格式：[YYYY-MM-DD HH:MM:SS][主题][user:]内容 / [YYYY-MM-DD HH:MM:SS][主题][you:]内容
"""

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.shared_storage import ensure_shared_config_ready, get_project_root, get_shared_config_path
from lib.core.event.center import Event, EventType, get_event_center
from lib.core.logger import get_logger

logger = get_logger(__name__)

_TOOL_MARKER_PATTERN = re.compile(r"###.*?###", re.S)
_TOPIC_MARKER_PATTERN = re.compile(r"^\s*///\s*([^/\r\n]{1,32}?)\s*///\s*", re.S)
_MEMORY_LINE_PATTERN = re.compile(
    r"^\[(?P<timestamp>[^\]]*)\]\[(?P<topic>[^\]]*)\]\[(?P<role>[^\]:]*):\](?P<content>.*)$"
)
_DEFAULT_TOPIC = "日常"
_MEMORY_FILE_GLOB = "memory_*.txt"
_MEMORY_FILE_PREFIX = "memory_"


def _memory_dir(shared: bool = True) -> Path:
    if shared:
        ensure_shared_config_ready()
        return get_shared_config_path("chat", "memory")
    return get_project_root() / "resc" / "user" / "memory"


def _today_filename() -> str:
    return f"{_MEMORY_FILE_PREFIX}{datetime.now().strftime('%Y-%m-%d')}.txt"


def _date_from_filename(name: str) -> str:
    return name[len(_MEMORY_FILE_PREFIX):-len(".txt")]


def _is_memory_file(path: Path) -> bool:
    name = path.name
    return name.startswith(_MEMORY_FILE_PREFIX) and name.endswith(".txt") and len(name) == len("memory_YYYY-MM-DD.txt")


def _migrate_legacy_single_file(legacy_path: Path, target_dir: Path) -> None:
    """将旧版单文件 memory.txt 按日期行拆分到分日文件中。"""
    if not legacy_path.exists():
        return
    try:
        raw = legacy_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    grouped: dict[str, list[str]] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # 行格式: [YYYY-MM-DD HH:MM:SS][... 提取前10位日期部分
        date_key = stripped[1:11] if stripped.startswith("[") else stripped[:10]
        try:
            datetime.strptime(date_key, "%Y-%m-%d")
        except (ValueError, IndexError):
            date_key = "unknown"
        grouped.setdefault(date_key, []).append(stripped)

    if not grouped:
        return

    target_dir.mkdir(parents=True, exist_ok=True)
    for date_key, lines in grouped.items():
        file_path = target_dir / f"{_MEMORY_FILE_PREFIX}{date_key}.txt"
        content = "\n".join(lines) + "\n"
        file_path.write_text(content, encoding="utf-8")

    try:
        legacy_path.unlink()
    except OSError:
        pass
    logger.info("[StreamMemory] 已迁移旧版记忆文件: %s -> %s", legacy_path, target_dir)


class StreamMemory:
    """记录用户输入与模型最终回复到本地按日期分片的 memory 文件。"""

    def __init__(self, memory_dir: Path | None = None):
        self._ec = get_event_center()
        self._write_lock = threading.Lock()

        if memory_dir is None:
            shared_dir = _memory_dir(shared=True)
            legacy_dir = _memory_dir(shared=False)
            self._memory_dir = shared_dir
            self._legacy_memory_dir = legacy_dir
        else:
            self._memory_dir = Path(memory_dir)
            self._legacy_memory_dir = None

        self._memory_dir.mkdir(parents=True, exist_ok=True)
        if self._legacy_memory_dir is not None:
            self._legacy_memory_dir.mkdir(parents=True, exist_ok=True)

        # 迁移旧版单文件 memory.txt
        for base_dir in (self._memory_dir, self._legacy_memory_dir):
            if base_dir is None:
                continue
            legacy_single = base_dir.parent / "memory.txt"
            if legacy_single.exists() and legacy_single.is_file():
                _migrate_legacy_single_file(legacy_single, base_dir)

        self._ec.subscribe(EventType.INPUT_CHAT, self._on_input_chat)
        self._ec.subscribe(EventType.STREAM_FINAL, self._on_stream_final)
        self._suppress_next_response = False
        logger.info("[StreamMemory] 已初始化: %s", self._memory_dir)

    @staticmethod
    def _extract_topic_and_lines(text: str) -> tuple[str, list[str]]:
        if not text:
            return _DEFAULT_TOPIC, []

        normalized = (
            str(text)
            .replace("＃", "#")
            .replace("／", "/")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )
        cleaned = _TOOL_MARKER_PATTERN.sub("", normalized)

        tail_marker = cleaned.rfind("###")
        if tail_marker >= 0:
            cleaned = cleaned[:tail_marker]

        topic = _DEFAULT_TOPIC
        marker_match = _TOPIC_MARKER_PATTERN.match(cleaned)
        if marker_match:
            parsed = (marker_match.group(1) or "").strip()
            if parsed:
                topic = parsed
            cleaned = cleaned[marker_match.end():]
        topic = str(topic).replace("[", "").replace("]", "").strip() or _DEFAULT_TOPIC

        lines: list[str] = []
        for line in cleaned.split("\n"):
            compact = line.strip()
            if compact:
                lines.append(compact)
        return topic, lines

    def _append_lines(self, role: str, topic: str, lines: list[str]) -> None:
        if not lines:
            return

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = "".join(f"[{now}][{topic}][{role}:]{line}\n" for line in lines)
        filename = _today_filename()

        try:
            with self._write_lock:
                for target_dir in (self._memory_dir, self._legacy_memory_dir):
                    if target_dir is None:
                        continue
                    target_dir.mkdir(parents=True, exist_ok=True)
                    file_path = target_dir / filename
                    with file_path.open("a", encoding="utf-8") as f:
                        f.write(payload)
        except OSError as e:
            logger.error("[StreamMemory] 写入失败: %s", e)

    def _on_input_chat(self, event: Event) -> None:
        if str(event.data.get("source", "")).strip() == "tool_recall":
            self._suppress_next_response = True
            return
        text = event.data.get("text", "")
        topic, lines = self._extract_topic_and_lines(text)
        if not lines:
            return
        self._append_lines("user", topic, lines)

    def _on_stream_final(self, event: Event) -> None:
        if self._suppress_next_response:
            self._suppress_next_response = False
            return
        text = event.data.get("text", "")
        topic, lines = self._extract_topic_and_lines(text)
        if not lines:
            return
        self._append_lines("you", topic, lines)

    def record_user_input(self, text: str) -> None:
        topic, lines = self._extract_topic_and_lines(text)
        if not lines:
            return
        self._append_lines("user", topic, lines)

    def cleanup(self) -> None:
        self._ec.unsubscribe(EventType.INPUT_CHAT, self._on_input_chat)
        self._ec.unsubscribe(EventType.STREAM_FINAL, self._on_stream_final)
        logger.info("[StreamMemory] 已清理")

    @staticmethod
    def _list_memory_files(directory: Path) -> list[Path]:
        if not directory.exists():
            return []
        files: list[Path] = []
        for child in directory.iterdir():
            if child.is_file() and _is_memory_file(child):
                files.append(child)
        files.sort()
        return files

    def _read_memory_lines(self) -> list[str]:
        lines: list[str] = []
        primary = self._memory_dir
        fallback = self._legacy_memory_dir

        files = self._list_memory_files(primary)
        if not files and fallback is not None:
            files = self._list_memory_files(fallback)

        for file_path in files:
            try:
                for line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    stripped = line.strip()
                    if stripped:
                        lines.append(stripped)
            except OSError as e:
                logger.debug("[StreamMemory] 读取记忆文件失败 %s: %s", file_path.name, e)
        return lines

    @staticmethod
    def _parse_memory_line(line: str) -> dict[str, str] | None:
        text = str(line or "").strip()
        if not text:
            return None
        match = _MEMORY_LINE_PATTERN.match(text)
        if not match:
            return {
                "timestamp": "",
                "topic": "",
                "role": "memory",
                "content": text,
            }
        return {
            "timestamp": str(match.group("timestamp") or "").strip(),
            "topic": str(match.group("topic") or "").strip(),
            "role": str(match.group("role") or "").strip().lower(),
            "content": str(match.group("content") or "").strip(),
        }

    def get_recent_entries(self, count: int = 12) -> list[dict[str, str]]:
        try:
            limit = int(count or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            return []

        entries: list[dict[str, str]] = []
        for raw_line in reversed(self._read_memory_lines()):
            item = self._parse_memory_line(raw_line)
            if not item or not item.get("content"):
                continue
            entries.append(item)
            if len(entries) >= limit:
                break
        entries.reverse()
        return entries


_instance: Optional[StreamMemory] = None


def get_stream_memory() -> StreamMemory:
    """获取全局 StreamMemory 实例（单例）。"""
    global _instance
    if _instance is None:
        _instance = StreamMemory()
    return _instance


def cleanup_stream_memory() -> None:
    """清理全局 StreamMemory 实例。"""
    global _instance
    if _instance is not None:
        _instance.cleanup()
        _instance = None
