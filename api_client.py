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
) -> str:
    """调用 chat/completions，返回改写文本。

    对 429/超时/5xx 指数退避重试；其余错误立即抛出。
    """
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
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
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

            if "choices" in result_json and result_json["choices"]:
                return result_json["choices"][0]["message"]["content"]
            if "output" in result_json:
                return str(result_json["output"])
            if "result" in result_json:
                return str(result_json["result"])
            return resp.text

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
