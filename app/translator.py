from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import language_label
from .subtitle_io import (
    SubtitleDocument,
    normalize_text,
    protect_tags,
    rebuild_fallback,
    restore_tags,
    save_subtitle,
    set_event_text,
    strip_formatting,
)

logger = logging.getLogger(__name__)

MAX_BATCH_CHARS = 6000
BATCH_ATTEMPTS = 3

ProgressCallback = Callable[[int, int, str], None]


class TranslationError(Exception):
    def __init__(
        self, message: str, partial_path: Path | None = None
    ) -> None:
        super().__init__(message)
        self.partial_path = partial_path


class TranslationCancelled(Exception):
    def __init__(self, partial_path: Path | None = None) -> None:
        super().__init__("翻译已取消")
        self.partial_path = partial_path


@dataclass
class TranslationReport:
    total_events: int
    unique_texts: int
    batches: int


@dataclass
class _Entry:
    event_index: int
    original: str
    masked: str
    tokens: list[str]
    key: str


def split_batches(
    items: list[tuple[int, str]],
    batch_size: int,
    max_chars: int = MAX_BATCH_CHARS,
) -> list[list[tuple[int, str]]]:
    batches: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_chars = 0

    for item_id, text in items:
        length = len(text)
        if current and (
            len(current) >= batch_size or current_chars + length > max_chars
        ):
            batches.append(current)
            current = []
            current_chars = 0
        current.append((item_id, text))
        current_chars += length
        if len(current) >= batch_size:
            batches.append(current)
            current = []
            current_chars = 0

    if current:
        batches.append(current)
    return batches


def _build_prompt(
    movie_name: str,
    source_lang: str,
    target_lang: str,
    batch: list[tuple[int, str]],
    strict: bool = False,
) -> tuple[str, str]:
    source = language_label(source_lang)
    target = language_label(target_lang)
    system = (
        f"你是一名专业的影视字幕译者。请把{source}字幕翻译成{target}。\n"
        "要求：\n"
        "1. 译文简洁、自然、口语化，符合字幕阅读习惯。\n"
        "2. 必须原样保留 ⟦0⟧、⟦1⟧ 这类占位符，数量、内容和顺序都不能改变。\n"
        "3. 只输出译文，不要解释、不要注释、不要添加引号。\n"
        f"4. 输出格式：每行一条，编号|译文；编号必须与输入完全一致，"
        f"共 {len(batch)} 行，不得合并或拆分。"
    )
    if strict:
        system += (
            "\n注意：上一次输出未通过校验（条目缺失、编号不匹配或占位符损坏）。"
            f"本次必须返回全部 {len(batch)} 行，编号与输入完全一致。"
        )
    lines = [f"{item_id}|{text}" for item_id, text in batch]
    prefix = f"影片：{movie_name}\n" if movie_name else ""
    user = (
        prefix
        + "请翻译以下字幕，严格按“编号|译文”逐行输出：\n"
        + "\n".join(lines)
    )
    return system, user


def _build_plain_prompt(
    movie_name: str,
    source_lang: str,
    target_lang: str,
    text: str,
) -> tuple[str, str]:
    source = language_label(source_lang)
    target = language_label(target_lang)
    system = (
        f"你是一名专业的影视字幕译者。请把下面的{source}字幕翻译成{target}。\n"
        "只输出译文，不要解释、不要注释、不要添加引号。"
    )
    prefix = f"影片：{movie_name}\n" if movie_name else ""
    return system, prefix + text


def parse_response(
    text: str, expected_ids: list[int]
) -> dict[int, str] | None:
    lines = text.strip().splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]

    result: dict[int, str] = {}
    for line in lines:
        if not line.strip():
            continue
        match = re.match(r"^\s*(\d+)\s*[|｜]\s*(.*)$", line)
        if not match:
            return None
        item_id = int(match.group(1))
        translated = match.group(2).strip()
        if not translated:
            return None
        if item_id in result:
            return None
        result[item_id] = translated

    if set(result) != set(expected_ids):
        return None
    return result


class TranslationJob:
    def __init__(
        self,
        document: SubtitleDocument,
        client,
        batch_size: int,
        concurrency: int,
        source_lang: str,
        target_lang: str,
        movie_name: str,
        partial_path: Path,
        progress_cb: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.document = document
        self.client = client
        self.batch_size = max(1, batch_size)
        self.concurrency = max(1, concurrency)
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.movie_name = movie_name
        self.partial_path = Path(partial_path)
        self.progress_cb = progress_cb
        self.cancel_event = cancel_event or threading.Event()
        self._done = 0
        self._total = 0

    def run(self) -> TranslationReport:
        if self.cancel_event.is_set():
            saved = self._save_partial()
            raise TranslationCancelled(
                self.partial_path if saved else None
            )

        try:
            groups = self._build_groups()
            self._total = sum(len(group) for group in groups)
        except Exception as exc:
            raise TranslationError(
                f"准备翻译失败：{exc}"
            ) from exc

        if not groups:
            raise TranslationError(
                "没有可翻译的对白（所选字幕可能只有特效或空行）"
            )

        try:
            id_to_group = {
                item_id: group
                for item_id, group in enumerate(groups, start=1)
            }
            representatives = [group[0] for group in groups]
            items = [
                (item_id, representative.masked)
                for item_id, representative in enumerate(
                    representatives, start=1
                )
            ]
            raw_chunks = split_batches(items, self.batch_size)
            chunks = [
                [
                    (item_id, id_to_group[item_id][0])
                    for item_id, _ in chunk
                ]
                for chunk in raw_chunks
            ]

            self._report("开始翻译")
            with ThreadPoolExecutor(
                max_workers=self.concurrency
            ) as pool:
                for start in range(0, len(chunks), self.concurrency):
                    if self.cancel_event.is_set():
                        raise TranslationCancelled()
                    wave = chunks[start : start + self.concurrency]
                    futures = [
                        pool.submit(self._translate_chunk, chunk)
                        for chunk in wave
                    ]
                    for future in as_completed(futures):
                        restored = future.result()
                        for item_id, translated in restored.items():
                            for entry in id_to_group[item_id]:
                                set_event_text(
                                    self.document,
                                    entry.event_index,
                                    translated,
                                )
                            self._done += len(id_to_group[item_id])
                            self._report(
                                f"已完成 {self._done}/{self._total} 条"
                            )
                    if self.cancel_event.is_set():
                        raise TranslationCancelled()
        except TranslationCancelled:
            saved = self._save_partial()
            raise TranslationCancelled(
                self.partial_path if saved else None
            )
        except Exception as exc:
            saved = self._save_partial()
            if self.cancel_event.is_set():
                raise TranslationCancelled(
                    self.partial_path if saved else None
                ) from exc
            if isinstance(exc, TranslationError):
                if saved:
                    if exc.partial_path is None:
                        exc.partial_path = self.partial_path
                else:
                    exc.partial_path = None
                raise
            partial = self.partial_path if saved else None
            message = f"翻译失败：{exc}"
            if not saved:
                message += "\n（已翻译部分保存失败，详见日志）"
            raise TranslationError(
                message, partial_path=partial
            ) from exc

        return TranslationReport(
            total_events=self._total,
            unique_texts=len(groups),
            batches=len(chunks),
        )

    def _build_groups(self) -> list[list[_Entry]]:
        groups: dict[str, list[_Entry]] = {}
        for index in self.document.translatable_indices():
            original = self.document.subs.events[index].text
            masked, tokens = protect_tags(original)
            key = normalize_text(masked) + "\x00" + "|".join(tokens)
            entry = _Entry(index, original, masked, tokens, key)
            groups.setdefault(key, []).append(entry)
        return list(groups.values())

    def _translate_chunk(
        self, chunk: list[tuple[int, _Entry]]
    ) -> dict[int, str]:
        expected = [item_id for item_id, _ in chunk]
        for attempt in range(BATCH_ATTEMPTS):
            if self.cancel_event.is_set():
                raise TranslationCancelled()
            system, user = _build_prompt(
                self.movie_name,
                self.source_lang,
                self.target_lang,
                [(item_id, entry.masked) for item_id, entry in chunk],
                strict=attempt > 0,
            )
            response = self.client.complete(system, user)
            parsed = parse_response(response, expected)
            if parsed is None:
                logger.info("批次返回格式不匹配，重试")
                continue

            restored: dict[int, str] = {}
            valid = True
            for item_id, entry in chunk:
                text = restore_tags(parsed[item_id], entry.tokens)
                if text is None or not text.strip():
                    valid = False
                    break
                restored[item_id] = text.strip()
            if valid:
                return restored
            logger.info("批次占位符校验失败，重试")

        logger.warning("批次重试耗尽，改为逐条翻译")
        result: dict[int, str] = {}
        for item_id, entry in chunk:
            if self.cancel_event.is_set():
                raise TranslationCancelled()
            plain = strip_formatting(entry.original)
            system, user = _build_plain_prompt(
                self.movie_name,
                self.source_lang,
                self.target_lang,
                plain,
            )
            translated = self.client.complete(system, user).strip()
            if not translated:
                raise TranslationError(f"第 {item_id} 条字幕翻译为空")
            result[item_id] = rebuild_fallback(
                entry.original, translated
            )
        return result

    def _report(self, message: str) -> None:
        if self.progress_cb is None:
            return
        try:
            self.progress_cb(self._done, self._total, message)
        except Exception:
            logger.exception("进度回调失败")

    def _save_partial(self) -> bool:
        try:
            save_subtitle(self.document, self.partial_path)
            return True
        except Exception:
            logger.exception("保存 partial 字幕失败")
            return False
