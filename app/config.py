from __future__ import annotations

import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "subtitle_translator_config.json"

# 按总使用人数排序的常用语言。中文按简体/繁体两种书写形式分别列出，
# 源语言与目标语言使用同一份语言表。
SOURCE_LANGUAGES: list[tuple[str, str]] = [
    ("en", "英语"),
    ("zh-Hans", "简体中文"),
    ("zh-Hant", "繁体中文"),
    ("hi", "印地语"),
    ("es", "西班牙语"),
    ("fr", "法语"),
    ("ar", "阿拉伯语"),
    ("bn", "孟加拉语"),
    ("pt", "葡萄牙语"),
    ("ru", "俄语"),
    ("ur", "乌尔都语"),
    ("id", "印尼语"),
    ("de", "德语"),
    ("ja", "日语"),
    ("mr", "马拉地语"),
    ("te", "泰卢固语"),
    ("tr", "土耳其语"),
    ("ta", "泰米尔语"),
    ("vi", "越南语"),
    ("tl", "他加禄语"),
    ("ko", "韩语"),
    ("fa", "波斯语"),
    ("ha", "豪萨语"),
    ("sw", "斯瓦希里语"),
    ("jv", "爪哇语"),
    ("it", "意大利语"),
    ("pa", "旁遮普语"),
    ("gu", "古吉拉特语"),
    ("th", "泰语"),
    ("kn", "卡纳达语"),
]
TARGET_LANGUAGES: list[tuple[str, str]] = list(SOURCE_LANGUAGES)
LANGUAGE_LABELS: dict[str, str] = dict(SOURCE_LANGUAGES + TARGET_LANGUAGES)
TARGET_SUFFIX: dict[str, str] = {
    "zh-Hans": "zh",
    "zh-Hant": "cht",
    **{
        code: code.lower()
        for code, _ in TARGET_LANGUAGES
        if not code.startswith("zh-")
    },
}
TARGET_TRACK_TITLE: dict[str, str] = {
    code: "English" if code == "en" else label
    for code, label in TARGET_LANGUAGES
}
LANGUAGE_ISO639_2: dict[str, str] = {
    "en": "eng",
    "zh-Hans": "chi",
    "zh-Hant": "chi",
    "hi": "hin",
    "es": "spa",
    "fr": "fra",
    "ar": "ara",
    "bn": "ben",
    "pt": "por",
    "ru": "rus",
    "ur": "urd",
    "id": "ind",
    "de": "deu",
    "ja": "jpn",
    "mr": "mar",
    "te": "tel",
    "tr": "tur",
    "ta": "tam",
    "vi": "vie",
    "tl": "tgl",
    "ko": "kor",
    "fa": "fas",
    "ha": "hau",
    "sw": "swa",
    "jv": "jav",
    "it": "ita",
    "pa": "pan",
    "gu": "guj",
    "th": "tha",
    "kn": "kan",
}


def language_label(code: str) -> str:
    return LANGUAGE_LABELS.get(code, code)


def target_suffix(code: str) -> str:
    return TARGET_SUFFIX.get(code, "translated")


def _text(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if value is None:
        return default
    return str(value).strip()


def _number(
    data: dict[str, Any],
    key: str,
    cast: type,
    low: float,
    high: float,
    default: float,
) -> Any:
    try:
        value = cast(data.get(key, default))
    except (TypeError, ValueError, OverflowError):
        value = default
    if isinstance(value, float) and not math.isfinite(value):
        return default
    return max(low, min(high, value))


@dataclass
class LLMSettings:
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = field(default="", repr=False)
    model: str = "deepseek-chat"
    temperature: float = 0.3
    batch_size: int = 30
    concurrency: int = 3
    timeout: int = 120

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LLMSettings":
        data = data if isinstance(data, dict) else {}
        defaults = cls()
        return cls(
            base_url=_text(data, "base_url", defaults.base_url),
            api_key=_text(data, "api_key", defaults.api_key),
            model=_text(data, "model", defaults.model),
            temperature=_number(
                data, "temperature", float, 0.0, 2.0, defaults.temperature
            ),
            batch_size=_number(data, "batch_size", int, 8, 80, defaults.batch_size),
            concurrency=_number(
                data, "concurrency", int, 1, 8, defaults.concurrency
            ),
            timeout=_number(data, "timeout", int, 30, 600, defaults.timeout),
        )


@dataclass
class AppConfig:
    llm: LLMSettings = field(default_factory=LLMSettings)
    source_lang: str = "en"
    target_lang: str = "zh-Hans"
    export_subtitle: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm": asdict(self.llm),
            "translation": {
                "source_lang": self.source_lang,
                "target_lang": self.target_lang,
                "export_subtitle": self.export_subtitle,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        data = data if isinstance(data, dict) else {}
        translation = data.get("translation")
        if not isinstance(translation, dict):
            translation = {}

        source = _text(translation, "source_lang", "en")
        if source not in {code for code, _ in SOURCE_LANGUAGES}:
            source = "en"
        target = _text(translation, "target_lang", "zh-Hans")
        if target not in {code for code, _ in TARGET_LANGUAGES}:
            target = "zh-Hans"
        export = translation.get("export_subtitle", True)
        if not isinstance(export, bool):
            export = True

        return cls(
            llm=LLMSettings.from_dict(data.get("llm")),
            source_lang=source,
            target_lang=target,
            export_subtitle=export,
        )


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return app_dir() / CONFIG_NAME


def load_config(path: Path | None = None) -> AppConfig:
    path = Path(path) if path is not None else config_path()
    if not path.is_file():
        return AppConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            raise ValueError("配置根节点必须是对象")
        return AppConfig.from_dict(data)
    except (OSError, ValueError):
        if path.is_file():
            backup = path.with_name(path.name + ".bak")
            try:
                path.replace(backup)
            except OSError:
                pass
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    path = Path(path) if path is not None else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp.replace(path)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    return path


def is_llm_configured(settings: LLMSettings) -> bool:
    return bool(
        settings.base_url.strip()
        and settings.api_key.strip()
        and settings.model.strip()
    )
