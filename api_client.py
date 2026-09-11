# api_client.py
"""OpenAI 兼容接口调用，带 429/超时退避重试。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

# 可重试的 HTTP 状态码
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

# 推理模型可能把正文放在这些字段
_REASONING_KEYS = ("reasoning_content", "reasoning", "thinking", "thought")

# DeepSeek 官方模型别名：错误别名会被静默映射或导致异常行为
_MODEL_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
}

_FENCE_RE = None


def _strip_code_fence(text: str) -> str:
    """去掉模型偶尔包的 ``` / ```text 围栏，避免污染章节解析。"""
    global _FENCE_RE
    if not text:
        return text
    if _FENCE_RE is None:
        import re as _re
        _FENCE_RE = _re.compile(
            r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*)\n```\s*$",
            _re.DOTALL,
        )
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    return text


def _normalize_model(model: str) -> str:
    model = (model or "").strip()
    return _MODEL_ALIASES.get(model, model)


class ApiError(Exception):
    def __init__(self, message: str, status_code: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ApiError):
        return exc.retryable
    if isinstance(exc, requests.Timeout):
        return True
    if isinstance(exc, requests.ConnectionError):
        return True
    return False


def _as_text(value: Any) -> str:
    """content 可能是 str / None / list[{text}]。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts).strip()
    return str(value).strip()


def _extract_message_text(message: Dict[str, Any]) -> tuple[str, str]:
    """从 message 取正文；返回 (text, source_key)。"""
    content = _as_text(message.get("content"))
    if content:
        return content, "content"
    for key in _REASONING_KEYS:
        alt = _as_text(message.get(key))
        if alt:
            return alt, key
    return "", ""


def _empty_content_error(result_json: Dict[str, Any]) -> ApiError:
    choices = result_json.get("choices") or []
    choice0 = choices[0] if choices else {}
    message = choice0.get("message") or {}
    finish = choice0.get("finish_reason") or choice0.get("finish_details")
    usage = result_json.get("usage") or {}
    keys = list(message.keys())
    hint = (
        "模型 200 返回但正文为空。常见原因：\n"
        "1) max_tokens 过小，推理模型把额度耗尽（usage="
        f"{usage}, finish_reason={finish}）；请把 Max Tokens 调到 8000+ 再试\n"
        "2) 当前模型名不适用或服务商路由异常（message keys={keys}）\n"
        "3) 换非推理模型（如 deepseek-chat）或另一套 API 方案"
    )
    return ApiError(hint, retryable=False)


def call_chat_completion(
    api_url: str,
    *,
    api_key: str = "",
    model: str = "gpt-3.5-turbo",
    system_prompt: str = "",
    user_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 4000,
    timeout: int = 120,
    max_retries: int = 3,
    backoff_base: float = 2.0,
    backoff_cap: float = 30.0,
    enable_thinking: bool = False,
) -> str:
    """调用 chat/completions，返回改写文本。

    对 429/超时/5xx 指数退避重试；其余错误立即抛出。
    enable_thinking=False 时向兼容端点下发「关闭思考」参数（各家字段名不一）。
    思考模型若因 max_tokens 被截断，会自动放宽 max_tokens 再试一次。
    """
    model = _normalize_model(model)
    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if not enable_thinking:
        # DeepSeek / SiliconFlow / 部分 OpenAI 兼容网关常见关闭思考写法
        payload["enable_thinking"] = False
        payload["chat_template_kwargs"] = {"enable_thinking": False}
        # 部分新版 DeepSeek 网关
        payload["thinking"] = {"type": "disabled"}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last_exc: Optional[Exception] = None
    thinking_params_sent = not enable_thinking
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout)

            # 部分网关不认识思考相关字段会 400：去掉后重试一次
            if resp.status_code == 400 and thinking_params_sent:
                body_l = (resp.text or "").lower()
                if any(k in body_l for k in ("enable_thinking", "thinking", "chat_template_kwargs")):
                    logger.warning("网关拒绝思考相关参数，已去掉后重试")
                    payload.pop("enable_thinking", None)
                    payload.pop("chat_template_kwargs", None)
                    payload.pop("thinking", None)
                    thinking_params_sent = False
                    resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout)

            if resp.status_code in RETRYABLE_STATUS:
                retry_after = None
                try:
                    retry_after = float(resp.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    pass
                msg = f"API 返回 {resp.status_code}: {resp.text[:200]}"
                last_exc = ApiError(msg, status_code=resp.status_code, retryable=True)
                if attempt < max_retries:
                    delay = retry_after if retry_after is not None else min(
                        backoff_base * (2 ** attempt), backoff_cap
                    )
                    logger.warning(
                        "API 可重试错误 (attempt %s/%s), %.1fs 后重试: %s",
                        attempt + 1, max_retries, delay, msg,
                    )
                    time.sleep(delay)
                    continue
                raise last_exc

            resp.raise_for_status()

            try:
                result_json = resp.json()
            except json.JSONDecodeError as e:
                sample = resp.text[:200]
                lower = resp.text.lower()
                if "html" in lower or "<!doctype html>" in lower:
                    raise ApiError(
                        "API 返回了 HTML 网页内容而不是 JSON。请检查 API URL 是否完整"
                        "（通常需以 /v1/chat/completions 结尾）。",
                        retryable=False,
                    ) from e
                raise ApiError(f"解析 API 响应为 JSON 失败 (前200字符: {sample})。") from e

            if isinstance(result_json, dict) and result_json.get("choices"):
                choice0 = result_json["choices"][0] or {}
                message = choice0.get("message") or {}
                if not isinstance(message, dict):
                    message = {}
                text, source = _extract_message_text(message)
                finish = choice0.get("finish_reason")
                usage = result_json.get("usage") or {}
                completion_details = usage.get("completion_tokens_details") or {}
                reasoning_tokens = completion_details.get("reasoning_tokens", 0)

                # 思考模型把额度耗在 reasoning 上，正文为空/极短且 finish=length → 放宽 max_tokens 再试
                if (not text or len(text) < 20) and finish == "length":
                    bumped = max(int(payload["max_tokens"]) * 2, 16000)
                    if bumped != payload["max_tokens"] and attempt < max_retries:
                        logger.warning(
                            "正文过短且 finish_reason=length (reasoning_tokens=%s, usage=%s)，"
                            "max_tokens 由 %s 提升到 %s 后重试",
                            reasoning_tokens, usage, payload["max_tokens"], bumped,
                        )
                        payload["max_tokens"] = bumped
                        delay = min(backoff_base * (2 ** attempt), backoff_cap)
                        time.sleep(delay)
                        continue

                if not text:
                    logger.error(
                        "API 正文为空 model=%s finish_reason=%s usage=%s message_keys=%s "
                        "reasoning_tokens=%s",
                        model, finish, usage, list(message.keys()), reasoning_tokens,
                    )
                    raise _empty_content_error(result_json)

                text = _strip_code_fence(text)
                if source != "content":
                    logger.warning(
                        "content 为空，已回退使用 message.%s（模型=%s, finish=%s）",
                        source, model, finish,
                    )
                if finish == "length":
                    logger.warning(
                        "模型因 max_tokens 截断 (finish_reason=length, reasoning_tokens=%s)，正文可能不完整",
                        reasoning_tokens,
                    )
                logger.info(
                    "API 成功 model=%s finish=%s content_len=%s source=%s usage=%s",
                    model, finish, len(text), source, usage,
                )
                return text

            if isinstance(result_json, dict):
                if "output" in result_json:
                    out = _as_text(result_json["output"])
                    if out:
                        return out
                if "result" in result_json:
                    out = _as_text(result_json["result"])
                    if out:
                        return out
            # 最后兜底：整包文本非空则返回（避免无意义空串）
            raw = (resp.text or "").strip()
            if raw and len(raw) > 2:
                logger.warning("无法从标准结构取到 choices，回退返回原始响应文本")
                return raw
            raise ApiError(
                f"API 响应缺少 choices。keys={list(result_json)[:8] if isinstance(result_json, dict) else type(result_json)}",
                retryable=False,
            )

        except ApiError:
            raise
        except Exception as e:
            last_exc = e
            if attempt < max_retries and _is_retryable(e):
                delay = min(backoff_base * (2 ** attempt), backoff_cap)
                logger.warning(
                    "API 网络错误 (attempt %s/%s), %.1fs 后重试: %s",
                    attempt + 1, max_retries, delay, e,
                )
                time.sleep(delay)
                continue
            if isinstance(e, requests.Timeout):
                raise ApiError(f"API 请求超时 ({timeout}s)", retryable=True) from e
            if isinstance(e, requests.ConnectionError):
                raise ApiError(f"API 连接失败: {e}", retryable=True) from e
            if isinstance(e, requests.HTTPError):
                status = e.response.status_code if e.response is not None else None
                raise ApiError(f"API HTTP 错误: {e}", status_code=status) from e
            raise ApiError(f"API 调用失败: {e}") from e

    raise ApiError(f"API 重试耗尽: {last_exc}", retryable=True)
