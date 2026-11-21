from __future__ import annotations

import time
from typing import Any, Callable

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)

from .config import LLMSettings


class LLMError(Exception):
    """LLM 调用失败。"""


class LLMAuthError(LLMError):
    """鉴权或权限错误，不应重试。"""


class LLMRetryableError(LLMError):
    """可重试错误在重试耗尽后抛出。"""


class LLMClient:
    def __init__(
        self,
        settings: LLMSettings,
        client: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries 必须至少为 1")
        self.settings = settings
        self._client = client if client is not None else OpenAI(
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout=settings.timeout,
            max_retries=0,
        )
        self._sleep = sleep
        self._max_retries = max_retries

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.settings.temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                response = self._client.chat.completions.create(**kwargs)
                if not response.choices:
                    raise LLMRetryableError("LLM 返回空 choices")
                content = response.choices[0].message.content or ""
                if not content.strip():
                    raise LLMRetryableError("LLM 返回空内容")
                return content
            except (AuthenticationError, PermissionDeniedError) as exc:
                raise LLMAuthError(
                    f"LLM 鉴权失败，请检查 API Key、地址和模型名：{exc}"
                ) from exc
            except BadRequestError as exc:
                raise LLMError(f"LLM 请求参数错误：{exc}") from exc
            except (
                RateLimitError,
                APITimeoutError,
                APIConnectionError,
                InternalServerError,
            ) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code in {408, 409}:
                    last_error = exc
                else:
                    raise LLMError(
                        f"LLM 请求失败（HTTP {exc.status_code}）：{exc}"
                    ) from exc
            except LLMRetryableError as exc:
                last_error = exc
            except Exception as exc:
                raise LLMError(f"LLM 调用失败：{exc}") from exc

            if attempt < self._max_retries:
                self._sleep(2 ** (attempt - 1))

        raise LLMRetryableError(
            f"LLM 连续 {self._max_retries} 次失败：{last_error}"
        ) from last_error

    def test_connection(self) -> str:
        return self.complete(
            "You are a connectivity tester.",
            "Reply with OK.",
            max_tokens=5,
        )
