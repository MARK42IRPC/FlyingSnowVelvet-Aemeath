"""Ollama ??????????????"""

import json
import subprocess
import threading
import time

import requests

from config.config import TIMEOUTS
from lib.core.compute_hub import get_compute_hub
from lib.core.event.center import EventType, Event

from .ollama_registry import record_model_error, update_models_from_names
from .ollama_support import logger, OLLAMA_BASE_URL, PING_INTERVAL_MS


class OllamaBootstrapMixin:
    def _on_app_pre_start(self, event: Event):
        """预启动阶段：后台静默尝试启动 ollama（仅 Ollama 模式）"""
        self._ensure_cli_model_refresh()

        if self._use_api_key:
            self._is_running     = True
            self._selected_model = self._active_config.get('model')
            logger.info("[OllamaManager] API Key 模式就绪，模型: %s", self._selected_model)
            return
        if self._rule_reply_mode:
            self._is_running = False
            self._selected_model = 'rule_reply'
            logger.warning("[OllamaManager] 规则回复模式就绪（不连接外部 API / Ollama）")
            return
        if self._api_type == 'error':
            self._is_running = False
            self._selected_model = None
            logger.error("[OllamaManager] 模式不可用: %s", self._mode_error or "unknown")
            return

        logger.info("[OllamaManager] 尝试在后台启动 ollama 服务...")
        future = get_compute_hub().submit_latest(
            "ollama_try_start",
            self._try_start_ollama,
            executor="io",
        )
        if future is None:
            logger.debug("[OllamaManager] ollama 预启动任务仍在运行，跳过重复提交")

    def _on_app_main(self, event: Event):
        """应用主阶段：启动后台状态探测与周期调度。"""
        if self._api_type != 'ollama':
            logger.info("[OllamaManager] 当前模式(%s)：跳过 ping 定时器", self._api_type)
        else:
            future = get_compute_hub().submit_latest(
                "ollama_ping",
                self._ping,
                executor="io",
            )
            if future is None:
                logger.debug("[OllamaManager] 初始 ping 任务仍在运行，跳过重复提交")

            if self._scheduler is None:
                raise RuntimeError("OllamaManager 在本地模式下需要注入 Scheduler")
            self._ping_timer = self._scheduler.create_periodic_timer(self._on_ping_tick)
            self._ping_timer.start(PING_INTERVAL_MS)
            logger.info("[OllamaManager] Ping 定时器已启动（间隔 %ds）", PING_INTERVAL_MS // 1000)

    def _ensure_cli_model_refresh(self) -> None:
        if getattr(self, "_cli_refresh_started", False):
            return
        self._cli_refresh_started = True
        future = get_compute_hub().submit_latest(
            "ollama_cli_refresh",
            self._refresh_models_from_cli,
            executor="io",
        )
        if future is None:
            logger.debug("[OllamaManager] CLI 模型刷新任务仍在运行，跳过重复提交")

    def _refresh_models_from_cli(self) -> None:
        commands = (
            (["ollama", "list", "--json"], True),
            (["ollama", "list"], False),
        )
        for cmd, json_mode in commands:
            try:
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=15,
                    creationflags=creationflags,
                )
            except FileNotFoundError:
                record_model_error("未找到 ollama 命令")
                logger.warning("[OllamaManager] 未找到 ollama 命令，无法检测本地模型")
                return
            except subprocess.TimeoutExpired:
                record_model_error("ollama list 命令超时")
                logger.warning("[OllamaManager] 列出 Ollama 模型超时: %s", " ".join(cmd))
                return
            except Exception as e:
                record_model_error(str(e))
                logger.warning("[OllamaManager] 列出 Ollama 模型失败(%s): %s", " ".join(cmd), e)
                return

            stdout = result.stdout or ""
            stderr = result.stderr or ""
            if result.returncode != 0:
                stderr_lower = (stderr or "").lower()
                if json_mode and any(token in stderr_lower for token in ("unknown flag", "invalid argument", "flag provided")):
                    continue
                error_text = stderr.strip() or stdout.strip() or f"退出码 {result.returncode}"
                record_model_error(error_text)
                logger.warning("[OllamaManager] 列出 Ollama 模型失败(%s): %s", " ".join(cmd), error_text)
                return

            names = self._parse_cli_model_names(stdout, json_mode=json_mode)
            if not names and json_mode:
                names = self._parse_cli_model_names(stdout, json_mode=False)
            update_models_from_names(names, source="cli", allow_empty=True)
            record_model_error("")
            if names:
                logger.info("[OllamaManager] CLI 检测到 %d 个 Ollama 模型", len(names))
            else:
                logger.info("[OllamaManager] CLI 列表显示 0 个 Ollama 模型")
            return

        record_model_error("未能解析 ollama list 输出")
        logger.warning("[OllamaManager] 未能解析 ollama list 输出")

    @staticmethod
    def _parse_cli_model_names(output: str, *, json_mode: bool) -> list[str]:
        names: list[str] = []
        for line in (output or "").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if json_mode:
                try:
                    payload = json.loads(stripped)
                except Exception:
                    continue
                name = str(payload.get("name") or "").strip()
                if name and name not in names:
                    names.append(name)
            else:
                lowered = stripped.lower()
                if lowered.startswith("name") and len(stripped.split()) > 1:
                    continue
                if set(stripped) == {"-"}:
                    continue
                parts = stripped.split()
                if not parts:
                    continue
                name = parts[0].strip()
                if name and name.lower() != "name" and name not in names:
                    names.append(name)
        return names

    def _try_start_ollama(self):
        """
        后台线程：确保 ollama 运行，并在预启动阶段同步检测模型可用性。

        流程：
        1. 若服务已运行 → 立即应用模型状态，返回
        2. 尝试启动 ollama serve
        3. 轮询等待服务就绪（最多 ~16 秒）→ 应用模型状态
        4. 超时 → 记录警告，由后续定时 ping 接管
        """
        try:
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=TIMEOUTS['api_list'])
            if resp.ok:
                logger.info("[OllamaManager] ollama 服务已在运行，跳过启动")
                self._apply_status_direct(resp)
                return
        except Exception:
            pass

        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            proc = subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            with self._ollama_proc_lock:
                self._started_ollama = True
                self._ollama_process = proc
            logger.info("[OllamaManager] 已启动 ollama serve，等待服务就绪...")
        except FileNotFoundError:
            logger.warning("[OllamaManager] 警告：未找到 ollama 命令，请先安装 ollama")
            return
        except Exception as e:
            logger.error("[OllamaManager] 启动 ollama 失败: %s", e)
            return

        for attempt in range(1, 9):
            time.sleep(2)
            try:
                resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=TIMEOUTS['api_request'])
                if resp.ok:
                    logger.info("[OllamaManager] ollama 服务就绪（第 %d 次检测）", attempt)
                    self._apply_status_direct(resp)
                    return
            except Exception:
                pass
            logger.debug("[OllamaManager] 等待 ollama 就绪...（%d/8）", attempt)

        logger.info("[OllamaManager] 预启动检测超时，由定时 ping 继续接管")

    def _on_ping_tick(self):
        """Qt 主线程定时回调：派发后台 ping 线程，不阻塞主线程"""
        future = get_compute_hub().submit_latest(
            "ollama_ping",
            self._ping,
            executor="io",
        )
        if future is None:
            logger.debug("[OllamaManager] ping 任务仍在运行，跳过本轮探测")

    def _ping(self):
        """后台线程：GET /api/tags，结果派发回管理器所属线程。"""
        try:
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=TIMEOUTS['api_list'])
            if resp.ok:
                models = resp.json().get("models", [])
                self._callback_dispatcher.dispatch(self._on_status_ready, True, models)
                return
        except Exception:
            pass

        self._callback_dispatcher.dispatch(self._on_status_ready, False, [])

    def cleanup(self):
        """停止定时器，取消事件订阅"""
        self._is_running = False
        if self._ping_timer:
            self._ping_timer.stop()
            self._ping_timer = None
        if self._scheduler is not None:
            self._scheduler.cleanup()
            self._scheduler = None
        self._callback_dispatcher.cleanup()

        with self._chat_state_lock:
            self._chat_callbacks.clear()
            self._chat_chunk_callbacks.clear()
        with self._api_rate_lock:
            self._api_request_timestamps.clear()

        with self._pull_response_lock:
            pull_response = self._pull_response
            self._pull_response = None
        if pull_response is not None:
            try:
                pull_response.close()
            except requests.RequestException:
                pass

        self._shutdown_started_ollama()
        self._event_center.unsubscribe(EventType.APP_PRE_START, self._on_app_pre_start)
        self._event_center.unsubscribe(EventType.APP_MAIN,      self._on_app_main)

    def _shutdown_started_ollama(self):
        """应用退出时结束本实例启动的 ollama 进程。"""
        if self._use_api_key:
            return

        with self._ollama_proc_lock:
            proc = self._ollama_process
            started = self._started_ollama
            self._ollama_process = None
            self._started_ollama = False

        if not started or proc is None:
            return

        if proc.poll() is not None:
            return

        try:
            proc.terminate()
            proc.wait(timeout=3)
            logger.info("[OllamaManager] 已结束 ollama 后台进程")
            return
        except Exception:
            pass

        try:
            proc.kill()
            proc.wait(timeout=2)
            logger.info("[OllamaManager] 已强制结束 ollama 后台进程")
        except Exception as e:
            logger.warning("[OllamaManager] 结束 ollama 进程失败: %s", e)

