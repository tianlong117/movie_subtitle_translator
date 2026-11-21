from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pysubs2

FORMAT_BY_SUFFIX = {".srt": "srt", ".ass": "ass", ".ssa": "ssa"}
UTF8_ENCODINGS = ("utf-8-sig", "utf-8")
LEGACY_ENCODINGS = ("gbk", "cp1252")
OVERRIDE_RE = re.compile(r"\{[^}]*\}")
DRAWING_RE = re.compile(r"\\p[1-9]")
PROTECT_RE = re.compile(r"(\{[^}]*\}|\\[Nnh])")
PLACEHOLDER_RE = re.compile(r"⟦(\d+)⟧")
LEADING_TAGS_RE = re.compile(r"^((?:\{[^}]*\})+)")


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _decode_text(raw: bytes) -> str:
    for encoding in UTF8_ENCODINGS:
        try:
            return _normalize_newlines(raw.decode(encoding))
        except UnicodeDecodeError:
            continue

    decoded: dict[str, str] = {}
    for encoding in LEGACY_ENCODINGS:
        try:
            decoded[encoding] = raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    if not decoded:
        raise ValueError("无法识别字幕文件编码（尝试过 UTF-8、GBK、CP1252）")

    gbk_text = decoded.get("gbk")
    if gbk_text is not None:
        visible = [ch for ch in gbk_text if not ch.isspace()]
        cjk = sum(1 for ch in visible if "一" <= ch <= "鿿")
        # GBK and cp1252 can both decode the same bytes. Prefer GBK only for
        # clearly CJK-heavy files; a few accented Western characters can form
        # valid GBK pairs, so require a minimum count and ratio.
        if cjk >= 5 and visible and cjk / len(visible) >= 0.05:
            return _normalize_newlines(gbk_text)

    if "cp1252" in decoded:
        return _normalize_newlines(decoded["cp1252"])
    return _normalize_newlines(gbk_text or "")


# The ⟦n⟧ glyph namespace is reserved for placeholders here. Any literal
# ⟦n⟧ sequence or literal {...} block already present in SRT text will be
# treated as preserved formatting rather than translatable content.
def protect_tags(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def replace(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"⟦{len(tokens) - 1}⟧"

    return PROTECT_RE.sub(replace, text), tokens


def restore_tags(text: str, tokens: list[str]) -> str | None:
    found = PLACEHOLDER_RE.findall(text)
    if found != [str(index) for index in range(len(tokens))]:
        return None

    def replace(match: re.Match[str]) -> str:
        return tokens[int(match.group(1))]

    return PLACEHOLDER_RE.sub(replace, text)


def rebuild_fallback(original: str, translated: str) -> str:
    """Keep only leading override blocks; formatting inside the translated text is intentionally dropped."""
    match = LEADING_TAGS_RE.match(original)
    prefix = match.group(1) if match else ""
    body = _normalize_newlines(translated.strip()).replace("\n", "\\N")
    return prefix + body


def normalize_text(text: str) -> str:
    """Normalize text for deduplication (whitespace collapse + casefold)."""
    return re.sub(r"\s+", " ", text).strip().casefold()


@dataclass
class SubtitleDocument:
    subs: pysubs2.SSAFile
    fmt: str
    path: Path | None = None

    @property
    def suffix(self) -> str:
        return ".ass" if self.fmt in {"ass", "ssa"} else ".srt"

    def translatable_indices(self) -> list[int]:
        indices: list[int] = []
        for index, event in enumerate(self.subs.events):
            if getattr(event, "type", "Dialogue") != "Dialogue":
                continue
            text = event.text or ""
            if not text.strip():
                continue
            if DRAWING_RE.search(text):
                continue
            if not strip_formatting(text).strip():
                continue
            indices.append(index)
        return indices


def load_subtitle(path: str | Path) -> SubtitleDocument:
    path = Path(path)
    fmt = FORMAT_BY_SUFFIX.get(path.suffix.lower())
    if fmt is None:
        raise ValueError("仅支持 .srt、.ass、.ssa 字幕文件")

    text = _decode_text(path.read_bytes())
    try:
        subs = pysubs2.SSAFile.from_string(text, format_=fmt)
    except Exception as exc:
        raise ValueError(f"无法解析字幕文件：{exc}") from exc
    if not subs.events:
        raise ValueError("字幕文件没有可读取的对白")
    return SubtitleDocument(subs=subs, fmt=fmt, path=path)


def save_subtitle(document: SubtitleDocument, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.subs.save(str(path), encoding="utf-8")
    return path


def strip_formatting(text: str) -> str:
    text = OVERRIDE_RE.sub("", text)
    text = text.replace("\\N", "\n").replace("\\n", "\n").replace("\\h", " ")
    return text.strip()


def set_event_text(document: SubtitleDocument, index: int, text: str) -> None:
    document.subs.events[index].text = text
