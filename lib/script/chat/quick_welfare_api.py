"""快捷福利 API：把 Kilo 公共免费池包装成一条「顺序兜底」的轮询车道。

原实现见 GitHub `Ebony-Vinyl/dsh-our-free-model` 的 Kilo 车道（`src/kilo.js`）：Kilo AI
的公共网关 `https://ai-gateway.kilo.ai/api/v1` 对 `isFree: true` 的那些模型**不需要
任何密钥**——没有 Key、没有登录、没有账号。这里只取其中与桌宠有关的两件事：

1. **免费池清单**：`GET /models` 取 `isFree` 为真的 id，缓存 6 小时；拉不到时退回
   本模块内置的静态清单，保证断网也能发请求（由网关自己判定模型是否可用）。
2. **顺序兜底**：一次聊天请求按「轮转起点」依次尝试清单里的模型，某个模型失败就换
   下一个；某个模型成功就把它提前、失败的进冷却，冷却期内不再排到前面。只要已经
   流出正文，就不再换模型——半截回复拼上另一个模型的续写比直接报错更难排查。

与「备用福利 API」的区别：那条车道由项目自己的发布源下发固定模型，稳定但只有一两个
模型；这条车道模型多、能轮着来，代价是个别模型会慢到分钟级，所以界面上写明
「可能响应较慢且不稳定」。

**这条车道不能带非空 `Authorization`**：网关对任何非空 Bearer 直接回
`401 INVALID_TOKEN`（实测 `Bearer public` 也不行），所以 `api_key` 必须保持空串。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import requests

from lib.core.logger import get_logger
from lib.core.services.network_policy import API_TIMEOUT_SECS

logger = get_logger(__name__)

#: Kilo 公共网关的 OpenAI 兼容基地址（官方文档发布的新域名）。
QUICK_WELFARE_BASE_URL = "https://ai-gateway.kilo.ai/api/v1"
#: 清单拉取超时：它只是列表，慢到这个程度就没必要继续等。
QUICK_WELFARE_LISTING_TIMEOUT_SECS = max(6.0, float(API_TIMEOUT_SECS))
#: 清单缓存有效期：上游随时会上下架模型，6 小时既跟得上又不会每轮请求都打一次。
QUICK_WELFARE_LISTING_TTL_SECS = 6 * 60 * 60
#: 清单拉取失败后的重试间隔：别让断网期间每一轮请求都去撞一次列表接口。
QUICK_WELFARE_LISTING_RETRY_SECS = 15 * 60

#: 单次聊天请求最多试几个模型。模型多不等于要多等：3 个已经能覆盖绝大多数临时故障，
#: 且单个模型自己还有 10 秒超时与 1 次重试的预算。
QUICK_WELFARE_MAX_MODELS_PER_TURN = 3
#: 模型冷却：连续失败一次的冷却与连续失败两次以上的冷却。
QUICK_WELFARE_SHORT_COOLDOWN_SECS = 10 * 60
QUICK_WELFARE_LONG_COOLDOWN_SECS = 30 * 60

#: 实测可用的看图模型（2026-11-11 逐模型验证，图片含可核对的颜色/形状/文字）。
#:
#: 上游声明的 `input_modalities` 只说明「模型接受图片」，不说明「真的会看图作答」。
#: 实测中被排除的三个都能收图、但给不出可用回复：
#: - `dots-studio/dots-3-note-preview:free`：只输出 reasoning，正文为空；
#: - `nvidia/nemotron-3.5-super-vl:free`：同样只输出 reasoning，且要 37 秒；
#: - `thinkingmachines/inkling-2-small:free`：持续 429。
#: 它们仍留在普通轮转里（纯文本可用），只是不再被「带图请求」优先选中。
QUICK_WELFARE_VERIFIED_VISION_MODELS = frozenset({
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "openrouter/free",
})

#: 带图请求的视觉优先顺序：先挑实测能答的，再退回到「声明支持图片」的其余模型。
QUICK_WELFARE_VISION_PRIORITY = (
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "openrouter/free",
)

#: 内置兜底清单：`GET /models` 的 `isFree` 切片（2026-11-11 实测）。顺序有意从
#: 「未知质量」的自动路由排到最后——自动路由让上游替我们挑模型，延迟不受控。
QUICK_WELFARE_FALLBACK_MODELS = (
    "liquid/lfm-3-1.7b:free",
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "thinkingmachines/inkling-2-small:free",
    "dots-studio/dots-3-note-preview:free",
    "baidu/ernie-5.5-flash-preview:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "stealth/glyph-cluster",
    "kilo-auto/free",
    "openrouter/free",
)

#: 与上游自己的客户端一致的归属头：网关按 referrer 给来源计数，这里不伪装成编辑器。
_ATTRIBUTION_HEADERS = {
    "http-referer": "https://kilo.ai/",
    "x-title": "FlyingSnowVelvet",
}


@dataclass
class _ModelHealth:
    """单个模型的车道健康度：冷却截止时间 + 连续失败次数。"""

    cooldown_until: float = 0.0
    failures: int = 0


class QuickWelfareRouter:
    """免费池清单 + 顺序兜底的模型轮转器（线程安全）。

    状态全部在进程内存里：冷却与排序是「这台机器当前这一轮」的经验，没有落盘价值，
    重启后重新从清单开头试起即可。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._models: tuple[str, ...] = tuple(QUICK_WELFARE_FALLBACK_MODELS)
        #: 能看图的模型集合：拉不到清单时用内置兜底，拉到清单后按上游声明的能力刷新。
        self._vision_models: frozenset[str] = QUICK_WELFARE_VERIFIED_VISION_MODELS
        self._listing_fetched_at = 0.0
        self._listing_failed_at = 0.0
        self._health: dict[str, _ModelHealth] = {}
        #: 轮转起点：每轮请求从这里开始，保证同一个首模型不会被连续压满。
        self._cursor = 0

    # ── 清单 ─────────────────────────────────────────────────────────

    def models(self) -> tuple[str, ...]:
        """当前可用的模型清单（不含冷却过滤），必要时先刷新一次。"""
        self._refresh_listing_if_due()
        with self._lock:
            return self._models

    def _refresh_listing_if_due(self) -> None:
        now = time.monotonic()
        if self._listing_fetched_at and now - self._listing_fetched_at < QUICK_WELFARE_LISTING_TTL_SECS:
            return
        if self._listing_failed_at and now - self._listing_failed_at < QUICK_WELFARE_LISTING_RETRY_SECS:
            return
        try:
            models, vision_models = fetch_free_models()
        except Exception as exc:
            self._listing_failed_at = time.monotonic()
            logger.warning("[QuickWelfare] 免费池清单刷新失败，沿用当前清单: %s", exc)
            return
        if not models:
            self._listing_failed_at = time.monotonic()
            logger.warning("[QuickWelfare] 免费池清单为空，沿用当前清单")
            return
        with self._lock:
            self._models = models
            self._vision_models = frozenset(vision_models)
            self._listing_fetched_at = time.monotonic()
            self._listing_failed_at = 0.0
            if self._cursor >= len(self._models):
                self._cursor = 0
        logger.info(
            "[QuickWelfare] 免费池清单已刷新：%d 个模型（其中 %d 个支持图片）",
            len(models),
            len(self._vision_models & set(models)),
        )

    # ── 顺序兜底 ─────────────────────────────────────────────────────

    def supports_vision(self, model: str) -> bool:
        """该模型是否声明了图片输入能力（清单拉不到时按内置兜底判断）。"""
        self._refresh_listing_if_due()
        with self._lock:
            return str(model or "") in self._vision_models

    def turn_models(
        self,
        limit: int = QUICK_WELFARE_MAX_MODELS_PER_TURN,
        *,
        needs_vision: bool = False,
    ) -> list[str]:
        """给出这一轮请求要按顺序尝试的模型。

        顺序规则：先把不在冷却里的模型按轮转起点排一圈，再把冷却中的模型按冷却到期
        时间垫在最后——全部模型都在冷却时，这一轮仍然照常发出去，而不是直接判死。

        ``needs_vision`` 为真时（这次请求带图片），能看图的模型整体排在前面：否则
        大概率第一个就撞上纯文本模型，白白浪费一次请求、还多等一个超时。看图的模型
        不够用时，剩下的文本模型仍然垫在后面——「先试能看图的」不等于「只试能看图的」。
        """
        models = self.models()
        if not models:
            return []
        now = time.monotonic()
        with self._lock:
            vision_models = self._vision_models
            start = self._cursor % len(models)
            self._cursor = (start + 1) % len(models)
            rotated = list(models[start:]) + list(models[:start])
            cooled: list[tuple[float, str]] = []
            healthy: list[str] = []
            for model in rotated:
                health = self._health.get(model)
                if health is None or health.cooldown_until <= now:
                    healthy.append(model)
                else:
                    cooled.append((health.cooldown_until, model))
        cooled.sort()
        order = healthy + [model for _until, model in cooled]
        if needs_vision:
            #: 三级稳定分区（组内保持轮转顺序）：
            #: 1. 实测能看图作答的模型；
            #: 2. 其余「声明支持图片」的模型（可能只吐思考，作为后备）；
            #: 3. 纯文本模型（最后才用，带图时基本拿不到有用回复）。
            verified = set(QUICK_WELFARE_VERIFIED_VISION_MODELS)
            order = (
                [model for model in order if model in verified]
                + [model for model in order if model in vision_models and model not in verified]
                + [model for model in order if model not in vision_models]
            )
        #: 去重保序：清单刷新前后可能短暂出现重复项。
        seen: set[str] = set()
        unique: list[str] = []
        for model in order:
            if model in seen:
                continue
            seen.add(model)
            unique.append(model)
        return unique[: max(1, int(limit))]

    def report_success(self, model: str) -> None:
        """某个模型答成功了：清掉冷却，并把它提到下一轮的起点。"""
        models = self.models()
        with self._lock:
            self._health.pop(model, None)
            if model in models:
                self._cursor = models.index(model)

    def report_failure(self, model: str) -> None:
        """某个模型失败了：连续失败越多，冷却越久。"""
        with self._lock:
            health = self._health.setdefault(model, _ModelHealth())
            health.failures += 1
            cooldown = (
                QUICK_WELFARE_SHORT_COOLDOWN_SECS if health.failures == 1
                else QUICK_WELFARE_LONG_COOLDOWN_SECS
            )
            health.cooldown_until = time.monotonic() + cooldown

    def cooldown_remaining(self, model: str) -> float:
        """返回该模型还要冷却多少秒；不在冷却中时返回 0。"""
        with self._lock:
            health = self._health.get(model)
        if health is None:
            return 0.0
        return max(0.0, health.cooldown_until - time.monotonic())

    def reset(self) -> None:
        """重置全部运行时状态（测试与「换出口重试」一类的入口用）。"""
        with self._lock:
            self._health.clear()
            self._cursor = 0
            self._listing_fetched_at = 0.0
            self._listing_failed_at = 0.0
            self._models = tuple(QUICK_WELFARE_FALLBACK_MODELS)


def fetch_free_model_ids(timeout: float = QUICK_WELFARE_LISTING_TIMEOUT_SECS) -> tuple[str, ...]:
    """只取免费模型的 id（保留给既有调用面与测试）。"""
    return fetch_free_models(timeout)[0]


def _fetch_free_models_impl(timeout: float) -> tuple[tuple[str, ...], frozenset[str]]:
    """从 Kilo 网关拉一次模型清单。

    拉取失败直接抛出，由调用方决定沿用旧清单；返回空元组表示「拉到了但没有免费模型」，
    同样按失败处理（上游临时改形状时不该把车道清空）。
    """
    response = requests.get(
        f"{QUICK_WELFARE_BASE_URL}/models",
        headers={
            "accept": "application/json",
            "user-agent": "FlyingSnowVelvet",
            **_ATTRIBUTION_HEADERS,
        },
        timeout=(timeout, timeout),
    )
    try:
        response.raise_for_status()
        payload = response.json()
    finally:
        response.close()
    items = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("免费池清单格式无效")
    models: list[str] = []
    vision: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or not item.get("isFree"):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id or model_id in models:
            continue
        models.append(model_id)
        architecture = item.get("architecture")
        modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
        if isinstance(modalities, list) and "image" in modalities:
            vision.add(model_id)
    return tuple(models), frozenset(vision)


def fetch_free_models(timeout: float = QUICK_WELFARE_LISTING_TIMEOUT_SECS) -> tuple[tuple[str, ...], frozenset[str]]:
    """拉一次免费池清单：`(全部免费模型, 其中支持图片输入的模型)`。

    图片能力取自上游自己的声明（`architecture.input_modalities` 含 `image`），不在本地
    维护一张会过期的白名单。
    """
    return _fetch_free_models_impl(timeout)


def quick_welfare_config(model: str) -> dict:
    """构造单个免费池模型的 OpenAI 兼容运行时配置。

    `api_key` 固定为空：这条车道的网关见到非空 Bearer 会回 401（见模块说明）。
    """
    return {
        "api_type": "openai_compatible",
        "base_url": QUICK_WELFARE_BASE_URL,
        "model": str(model or ""),
        "api_key": "",
        "key_source": "quick_welfare",
        "force_mode": "5",
        "strict_mode": True,
        "error": "",
    }


_router: QuickWelfareRouter | None = None
_ROUTER_LOCK = threading.Lock()


def get_quick_welfare_router() -> QuickWelfareRouter:
    """进程内单例：冷却与轮转起点要跨请求共享，不能每个请求新建一个。"""
    global _router
    with _ROUTER_LOCK:
        if _router is None:
            _router = QuickWelfareRouter()
        return _router


def clear_quick_welfare_state() -> None:
    """清掉单例（测试与热重载用）。"""
    global _router
    with _ROUTER_LOCK:
        _router = None
