"""快捷福利 API 的请求实现：在免费池里按顺序兜底换模型。

主模块 `quick_welfare_api` 只管清单、冷却与轮转顺序；这里负责真正发请求，并把
「哪个模型失败了」回报给轮转器。它与 `_openai_chat_api` 同住 API 客户端层，
因为换模型这件事必须能拿到「这次是不是已经流出了正文」——只有这一层知道。

一条请求最多试 `QUICK_WELFARE_MAX_MODELS_PER_TURN` 个模型，每个模型：
- 连接/读取超时 `QUICK_WELFARE_TURN_TIMEOUT_SECS`，**不重试**：这条车道的兜底方式是
  换下一个模型，而不是在同一个模型上反复等。
- 图片请求被拒时，先在同一个模型上退成纯文本再试一次（与手动 API 的行为一致）；
  仍失败才换模型。
- 一旦已经吐出过正文就不再换模型：半截回复接上另一个模型的续写，比报错更难排查。
"""

from __future__ import annotations

import time

import requests

from lib.core.logger import get_logger

from ._multimodal import is_image_input_error
from .quick_welfare_api import (
    QUICK_WELFARE_MAX_MODELS_PER_TURN,
    QUICK_WELFARE_MAX_OUTPUT_TOKENS,
    get_quick_welfare_router,
    is_quota_exhausted,
    quick_welfare_config,
    quick_welfare_reasoning_enabled,
)

logger = get_logger(__name__)

#: 单个模型的连接/读取超时。免费池里个别模型会慢到分钟级，超过这个上限就换下一个：
#: 3 个模型的最差情况仍然在一分钟内，通常第一个模型就答完了。
QUICK_WELFARE_TURN_TIMEOUT_SECS = 20.0
#: 单模型不重试：重试预算花在「换模型」上，而不是等同一个目标自己恢复。
QUICK_WELFARE_TURN_RETRY_ATTEMPTS = 1


class _EmittedProbe:
    """包一层流式回调，记录这次请求是否已经吐过正文。"""

    def __init__(self, on_chunk_emit):
        self._on_chunk_emit = on_chunk_emit
        self.emitted = False

    def __call__(self, text: str) -> None:
        if text:
            self.emitted = True
        if self._on_chunk_emit is not None:
            self._on_chunk_emit(text)


class QuickWelfareMixin:
    def _quick_welfare_chat_api(self, message: str, persona: str,
                                on_chunk_emit=None, images: list[bytes] = None,
                                history: list[dict] | None = None,
                                request_id: int | None = None,
                                allow_tools: bool = True,
                                on_tool_call=None,
                                on_reasoning_emit=None) -> str:
        """在免费池里按轮转顺序逐个模型尝试，返回第一个成功的回复。

        ``on_reasoning_emit`` 只在「模型已经开始思考、但还没写正文」时被调用，
        上层据此每隔几秒提示一次「思考中…」，避免长思考看起来像卡死。
        """
        router = get_quick_welfare_router()
        models = router.turn_models(
            QUICK_WELFARE_MAX_MODELS_PER_TURN,
            # 带图时先试能看图的模型：否则第一个模型大概率是纯文本的，白白多等一次超时。
            needs_vision=bool(images),
        )
        if not models:
            raise RuntimeError("快捷福利 API 当前没有可用的免费模型")

        last_error: Exception | None = None
        for index, model in enumerate(models):
            probe = _EmittedProbe(on_chunk_emit)
            started_at = time.monotonic()
            try:
                text = self._openai_chat_api(
                    message,
                    persona,
                    on_chunk_emit=probe,
                    images=images,
                    history=history,
                    request_id=request_id,
                    config_override=quick_welfare_config(model),
                    allow_tools=allow_tools,
                    on_tool_call=on_tool_call,
                    retry_attempts=QUICK_WELFARE_TURN_RETRY_ATTEMPTS,
                    timeout_secs=QUICK_WELFARE_TURN_TIMEOUT_SECS,
                    max_output_tokens_override=QUICK_WELFARE_MAX_OUTPUT_TOKENS,
                    reasoning_enabled_override=quick_welfare_reasoning_enabled(),
                    on_reasoning_emit=on_reasoning_emit,
                )
            except requests.HTTPError as exc:
                error_text = self._extract_error(exc) if hasattr(self, "_extract_error") else str(exc)
                # 免费池配额耗尽（`429 PROMOTION_MODEL_LIMIT_REACHED`）是 IP 级的：池里
                # 所有模型会同时回同一个 429，换模型只是把同一个错误再撞三遍，还会让
                # 界面等满三个超时才报错。这种情况整轮直接结束，如实上报。
                if is_quota_exhausted(error_text):
                    logger.warning(
                        "[QuickWelfare] 免费池配额已耗尽（%s），本轮不再换模型: %s",
                        error_text[:80],
                        model,
                    )
                    raise RuntimeError(
                        f"快捷福利 API 的免费额度已用完：{error_text[:160]}"
                    ) from exc
                if images and is_image_input_error(error_text):
                    # 带图请求优先保住图片：只要后面还有能看图的模型，就换它继续带图试，
                    # 而不是在当前这个不吃图的模型上把图片丢掉——用户要的是「看图回答」，
                    # 纯文本回复等于没回答这个问题。只有能看图的都试完了才退回纯文本。
                    remaining_vision = [
                        candidate for candidate in models[index + 1:]
                        if router.supports_vision(candidate)
                    ]
                    if remaining_vision:
                        last_error = exc
                        router.report_failure(model)
                        if probe.emitted:
                            logger.error("[QuickWelfare] %s 已流出正文后失败，不再换模型", model)
                            raise
                        logger.warning(
                            "[QuickWelfare] %s 不接受图片，换能看图的 %s 继续带图重试",
                            model,
                            remaining_vision[0],
                        )
                        continue
                    logger.warning("[QuickWelfare] %s 不接受图片，池里已无看图模型，改为纯文本重试", model)
                    try:
                        text = self._openai_chat_api(
                            message,
                            persona,
                            on_chunk_emit=probe,
                            images=None,
                            history=history,
                            request_id=request_id,
                            config_override=quick_welfare_config(model),
                            allow_tools=allow_tools,
                            on_tool_call=on_tool_call,
                            retry_attempts=QUICK_WELFARE_TURN_RETRY_ATTEMPTS,
                            timeout_secs=QUICK_WELFARE_TURN_TIMEOUT_SECS,
                            max_output_tokens_override=QUICK_WELFARE_MAX_OUTPUT_TOKENS,
                            reasoning_enabled_override=quick_welfare_reasoning_enabled(),
                            on_reasoning_emit=on_reasoning_emit,
                        )
                    except Exception as retry_exc:
                        last_error = retry_exc
                        router.report_failure(model)
                        if probe.emitted:
                            logger.error("[QuickWelfare] %s 已流出正文后失败，不再换模型", model)
                            raise
                        logger.warning(
                            "[QuickWelfare] %s 纯文本重试仍失败，换下一个模型: %s",
                            model,
                            retry_exc,
                        )
                        continue
                else:
                    last_error = exc
                    router.report_failure(model)
                    if probe.emitted:
                        logger.error("[QuickWelfare] %s 已流出正文后失败，不再换模型", model)
                        raise
                    logger.warning("[QuickWelfare] %s 请求失败，换下一个模型: %s", model, error_text)
                    continue
            except Exception as exc:
                last_error = exc
                router.report_failure(model)
                if probe.emitted:
                    logger.error("[QuickWelfare] %s 已流出正文后失败，不再换模型", model)
                    raise
                logger.warning("[QuickWelfare] %s 请求异常，换下一个模型: %s", model, exc)
                continue

            # 有些模型把全部输出预算花在思考上，正文一个字符都没写出来（Nemotron VL 系
            # 尤其明显）。这种情况不能当成功返回——上层会把它显示成「当前模式不可用」，
            # 而实际上只要换一个模型就能答。已经吐出过正文的不受影响。
            if not str(text or "").strip() and getattr(self, "_last_stream_reasoning_only", False):
                router.report_failure(model)
                if probe.emitted:
                    logger.error("[QuickWelfare] %s 已流出正文后只剩思考内容，不再换模型", model)
                    return text
                logger.warning("[QuickWelfare] %s 只有思考内容、没有正文，换下一个模型", model)
                last_error = RuntimeError(f"{model} 只输出了思考内容，没有正文")
                continue

            router.report_success(model)
            logger.info(
                "[QuickWelfare] %s 应答成功（第 %d 个模型，%.1fs）",
                model,
                index + 1,
                time.monotonic() - started_at,
            )
            return text

        if last_error is not None:
            raise last_error
        raise RuntimeError("快捷福利 API 本轮没有模型应答")
