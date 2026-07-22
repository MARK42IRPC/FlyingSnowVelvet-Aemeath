"""Shared storage helpers for bug tracker records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from config.user_storage_paths import get_user_state_dir

_BUG_TRACKER_EVENT_FILE = "bug_tracker_events.jsonl"
_APP_LOG_GLOB = "app_*.log"
_LOG_LINE_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\] "
    r"\[(?P<level>[A-Z]+)\s*\] "
    r"\[(?P<logger>[^\]]*)\] "
    r"(?P<message>.*)$"
)
_LEVEL_TO_NO = {
    "CRITICAL": 50,
    "ERROR": 40,
    "WARNING": 30,
    "WARN": 30,
    "INFO": 20,
    "DEBUG": 10,
}


@dataclass(slots=True)
class BugInstanceInfo:
    instance_id: str
    instance_label: str
    log_path: str
    error_count: int
    last_timestamp: str


@dataclass(slots=True)
class BugRecord:
    timestamp: str
    logger: str
    level: str
    levelno: int
    message: str
    pathname: str
    lineno: int
    func_name: str
    module: str
    process: int
    thread_name: str
    exception: str
    stack_info: str
    instance_id: str
    instance_label: str
    log_path: str
    raw: dict

    @property
    def source_label(self) -> str:
        if self.pathname:
            return f"{self.pathname}:{self.lineno or 0}"
        if self.log_path:
            return f"{self.instance_label} / {Path(self.log_path).name}"
        return self.logger or self.module or "unknown"

    @property
    def fingerprint(self) -> str:
        if self.pathname:
            return f"{self.pathname}|{self.lineno}|{self.message}"
        return f"{self.logger}|{self.module}|{self.message}"

    @property
    def unique_key(self) -> str:
        return "|".join((
            self.instance_id or "",
            self.timestamp or "",
            self.logger or "",
            self.pathname or "",
            str(self.lineno or 0),
            self.message or "",
        ))

    @property
    def iso_datetime(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.timestamp)
        except Exception:
            return None


class BugTrackerLogStore:
    def __init__(self, path: Path | None = None):
        self._event_path = Path(path) if path is not None else get_bug_tracker_event_log_path()
        self._log_dir = get_project_log_dir()

    @property
    def path(self) -> Path:
        return self._event_path

    def reset(self) -> None:
        return

    def snapshot_token(self) -> tuple:
        log_stats = []
        for path in self._iter_app_logs():
            try:
                stat = path.stat()
                log_stats.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
            except OSError:
                log_stats.append((path.name, 0, 0))
        try:
            event_stat = self._event_path.stat()
            event_token = (int(event_stat.st_mtime_ns), int(event_stat.st_size))
        except OSError:
            event_token = (0, 0)
        return tuple(log_stats), event_token

    def read_new_records(self) -> list[BugRecord]:
        # 为了统一实例分类与结构化补全，这里每次都重建全量快照。
        return self.load_all_records()

    def load_all_records(self) -> list[BugRecord]:
        enriched_payloads = self._load_structured_payloads()
        structured_map = {self._structured_key_from_payload(payload): payload for payload in enriched_payloads}
        matched_keys: set[str] = set()

        records: list[BugRecord] = []
        for log_path in self._iter_app_logs():
            records.extend(self._parse_app_log(log_path, structured_map, matched_keys))

        for payload in enriched_payloads:
            key = self._structured_key_from_payload(payload)
            if key in matched_keys:
                continue
            records.append(self._record_from_payload(payload))

        records.sort(
            key=lambda record: record.iso_datetime or datetime.min,
            reverse=True,
        )
        return records

    def list_instances(self, records: list[BugRecord] | None = None) -> list[BugInstanceInfo]:
        records = records or []
        counts: dict[str, int] = {}
        last_ts: dict[str, str] = {}
        by_id: dict[str, BugInstanceInfo] = {}

        for path in self._iter_app_logs():
            instance_id = path.name
            by_id[instance_id] = BugInstanceInfo(
                instance_id=instance_id,
                instance_label=_instance_label_from_path(path),
                log_path=str(path),
                error_count=0,
                last_timestamp="",
            )

        for record in records:
            instance_id = record.instance_id or "live"
            counts[instance_id] = counts.get(instance_id, 0) + 1
            current_ts = last_ts.get(instance_id, "")
            if record.timestamp and record.timestamp > current_ts:
                last_ts[instance_id] = record.timestamp
            if instance_id not in by_id:
                by_id[instance_id] = BugInstanceInfo(
                    instance_id=instance_id,
                    instance_label=record.instance_label or instance_id,
                    log_path=record.log_path or "",
                    error_count=0,
                    last_timestamp="",
                )

        for instance_id, info in by_id.items():
            info.error_count = counts.get(instance_id, 0)
            info.last_timestamp = last_ts.get(instance_id, "")

        def _sort_key(info: BugInstanceInfo) -> tuple:
            try:
                dt = datetime.fromisoformat(info.last_timestamp) if info.last_timestamp else None
            except Exception:
                dt = None
            try:
                label_dt = datetime.strptime(info.instance_label, "%Y-%m-%d %H:%M:%S")
            except Exception:
                label_dt = None
            best = dt or label_dt or datetime.min
            return (best, info.instance_label)

        return sorted(by_id.values(), key=_sort_key, reverse=True)

    def _iter_app_logs(self) -> list[Path]:
        if not self._log_dir.exists():
            return []
        return sorted(self._log_dir.glob(_APP_LOG_GLOB), key=lambda path: path.name)

    def _parse_app_log(
        self,
        log_path: Path,
        structured_map: dict[str, dict],
        matched_keys: set[str],
    ) -> list[BugRecord]:
        instance_id = log_path.name
        instance_label = _instance_label_from_path(log_path)
        records: list[BugRecord] = []
        current: dict | None = None

        def flush_current() -> None:
            nonlocal current
            if current is None:
                return
            key = self._structured_key(
                current["timestamp"],
                current["logger"],
                current["level"],
                current["message"],
            )
            payload = structured_map.get(key)
            if payload is not None:
                matched_keys.add(key)
            trace_text = "\n".join(current["trace_lines"]).strip()
            record = self._record_from_parts(
                timestamp=current["timestamp"],
                logger=current["logger"],
                level=current["level"],
                levelno=int(current["levelno"]),
                message=current["message"],
                pathname=str((payload or {}).get("pathname", "") or ""),
                lineno=int((payload or {}).get("lineno", 0) or 0),
                func_name=str((payload or {}).get("funcName", "") or ""),
                module=str((payload or {}).get("module", "") or _module_from_logger(current["logger"])),
                process=int((payload or {}).get("process", 0) or 0),
                thread_name=str((payload or {}).get("threadName", "") or ""),
                exception=str((payload or {}).get("exception", "") or trace_text),
                stack_info=str((payload or {}).get("stack_info", "") or ""),
                instance_id=instance_id,
                instance_label=instance_label,
                log_path=str(log_path),
                raw=(payload or current),
            )
            records.append(record)
            current = None

        try:
            with log_path.open("r", encoding="utf-8", errors="ignore") as fh:
                for raw_line in fh:
                    line = raw_line.rstrip("\r\n")
                    match = _LOG_LINE_RE.match(line)
                    if match is not None:
                        flush_current()
                        level = str(match.group("level") or "").strip().upper()
                        if level not in {"INFO", "WARN", "WARNING", "ERROR", "CRITICAL"}:
                            continue
                        current = {
                            "timestamp": _normalize_timestamp_value(str(match.group("ts") or "")),
                            "logger": str(match.group("logger") or "").strip(),
                            "level": level,
                            "levelno": _LEVEL_TO_NO.get(level, 0),
                            "message": str(match.group("message") or "").strip(),
                            "trace_lines": [],
                        }
                        continue
                    if current is not None:
                        current["trace_lines"].append(line)
        except OSError:
            return records

        flush_current()
        return records

    def _load_structured_payloads(self) -> list[dict]:
        path = self._event_path
        if not path.exists():
            return []

        payloads: list[dict] = []
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(payload, dict):
                        payloads.append(payload)
        except OSError:
            return []
        return payloads

    def _record_from_payload(self, payload: dict) -> BugRecord:
        return self._record_from_parts(
            timestamp=str(payload.get("timestamp", "") or ""),
            logger=str(payload.get("logger", "") or ""),
            level=str(payload.get("level", "") or ""),
            levelno=int(payload.get("levelno", 0) or 0),
            message=str(payload.get("message", "") or ""),
            pathname=str(payload.get("pathname", "") or ""),
            lineno=int(payload.get("lineno", 0) or 0),
            func_name=str(payload.get("funcName", "") or ""),
            module=str(payload.get("module", "") or ""),
            process=int(payload.get("process", 0) or 0),
            thread_name=str(payload.get("threadName", "") or ""),
            exception=str(payload.get("exception", "") or ""),
            stack_info=str(payload.get("stack_info", "") or ""),
            instance_id=str(payload.get("instance_id", "") or "live"),
            instance_label=str(payload.get("instance_label", "") or "实时事件"),
            log_path=str(payload.get("log_path", "") or ""),
            raw=payload,
        )

    def _record_from_parts(
        self,
        *,
        timestamp: str,
        logger: str,
        level: str,
        levelno: int,
        message: str,
        pathname: str,
        lineno: int,
        func_name: str,
        module: str,
        process: int,
        thread_name: str,
        exception: str,
        stack_info: str,
        instance_id: str,
        instance_label: str,
        log_path: str,
        raw: dict,
    ) -> BugRecord:
        return BugRecord(
            timestamp=_normalize_timestamp_value(timestamp),
            logger=logger,
            level=level,
            levelno=levelno,
            message=message,
            pathname=pathname,
            lineno=lineno,
            func_name=func_name,
            module=module,
            process=process,
            thread_name=thread_name,
            exception=exception,
            stack_info=stack_info,
            instance_id=instance_id,
            instance_label=instance_label,
            log_path=log_path,
            raw=raw,
        )

    def _structured_key_from_payload(self, payload: dict) -> str:
        return self._structured_key(
            str(payload.get("timestamp", "") or ""),
            str(payload.get("logger", "") or ""),
            str(payload.get("level", "") or ""),
            str(payload.get("message", "") or ""),
        )

    @staticmethod
    def _structured_key(timestamp: str, logger: str, level: str, message: str) -> str:
        return "|".join((
            _normalize_timestamp_value(timestamp),
            str(logger or "").strip(),
            str(level or "").strip().upper(),
            str(message or "").strip(),
        ))


def get_bug_tracker_event_log_path() -> Path:
    path = get_user_state_dir(_BUG_TRACKER_EVENT_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_project_log_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "logs"


def _normalize_timestamp_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.strptime(text, fmt)
            return dt.isoformat(timespec="milliseconds")
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text).isoformat(timespec="milliseconds")
    except Exception:
        return text


def _instance_label_from_path(path: Path) -> str:
    stem = path.stem
    if stem.startswith("app_"):
        text = stem[4:]
        try:
            dt = datetime.strptime(text, "%Y%m%d_%H%M%S_%f")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return stem


def _module_from_logger(logger: str) -> str:
    text = str(logger or "").strip()
    if not text:
        return "unknown"
    parts = [part for part in text.split(".") if part]
    if len(parts) >= 2:
        return parts[-1]
    return parts[0]
