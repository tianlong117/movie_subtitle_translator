from __future__ import annotations

import re
from typing import Any

from .config import SOURCE_LANGUAGES
from .subtitle_io import SubtitleDocument, strip_formatting

_CODE_PATTERN = re.compile(
    r"\b("
    + "|".join(re.escape(code) for code, _ in SOURCE_LANGUAGES)
    + r")\b",
    re.IGNORECASE,
)
_CODE_BY_LOWER = {
    code.casefold(): code for code, _ in SOURCE_LANGUAGES
}

_ENGLISH_ALIASES: tuple[tuple[str, str], ...] = (
    ("traditional chinese", "zh-Hant"),
    ("simplified chinese", "zh-Hans"),
    ("chinese traditional", "zh-Hant"),
    ("chinese simplified", "zh-Hans"),
    ("mandarin chinese", "zh-Hans"),
    ("traditional", "zh-Hant"),
    ("simplified", "zh-Hans"),
    ("mandarin", "zh-Hans"),
    ("chinese", "zh-Hans"),
    ("english", "en"),
    ("hindi", "hi"),
    ("spanish", "es"),
    ("french", "fr"),
    ("arabic", "ar"),
    ("bengali", "bn"),
    ("portuguese", "pt"),
    ("russian", "ru"),
    ("urdu", "ur"),
    ("indonesian", "id"),
    ("german", "de"),
    ("japanese", "ja"),
    ("marathi", "mr"),
    ("telugu", "te"),
    ("turkish", "tr"),
    ("tamil", "ta"),
    ("vietnamese", "vi"),
    ("tagalog", "tl"),
    ("filipino", "tl"),
    ("korean", "ko"),
    ("persian", "fa"),
    ("farsi", "fa"),
    ("hausa", "ha"),
    ("swahili", "sw"),
    ("javanese", "jv"),
    ("italian", "it"),
    ("punjabi", "pa"),
    ("gujarati", "gu"),
    ("thai", "th"),
    ("kannada", "kn"),
)


def build_sample(
    document: SubtitleDocument,
    max_events: int = 30,
    max_chars: int = 2000,
) -> str:
    parts: list[str] = []
    total = 0
    for index in document.translatable_indices():
        text = strip_formatting(
            document.subs.events[index].text
        ).strip()
        if not text:
            continue
        parts.append(text)
        total += len(text)
        if len(parts) >= max_events or total >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


def build_prompt(sample: str) -> tuple[str, str]:
    options = "；".join(
        f"{code}={label}" for code, label in SOURCE_LANGUAGES
    )
    system = (
        "你是语言识别器。请判断下面字幕文本的源语言，"
        "只输出一个代码，不要解释。\n"
        f"可选代码：{options}\n"
        "中文请区分简体中文（zh-Hans）和繁体中文（zh-Hant）。"
    )
    return system, sample


def parse_detected_language(response: str) -> str | None:
    if not response:
        return None
    text = response.strip()
    cleaned = text.strip(
        " \t\r\n`'\"。.,，:：;；()（）[]【】"
    )
    if cleaned.casefold() in _CODE_BY_LOWER:
        return _CODE_BY_LOWER[cleaned.casefold()]

    match = _CODE_PATTERN.search(text)
    if match:
        return _CODE_BY_LOWER.get(match.group(1).casefold())

    for code, label in SOURCE_LANGUAGES:
        if label in text:
            return code

    lower = text.casefold()
    for alias, code in _ENGLISH_ALIASES:
        if alias in lower:
            return code
    return None


def detect_language(client: Any, sample: str) -> str | None:
    system, user = build_prompt(sample)
    response = client.complete(system, user, max_tokens=512)
    return parse_detected_language(response)
