import tempfile
import unittest
from pathlib import Path

from app.subtitle_io import (
    load_subtitle,
    normalize_text,
    protect_tags,
    rebuild_fallback,
    restore_tags,
    save_subtitle,
    set_event_text,
    strip_formatting,
)

ASS_TEXT = """\
[Script Info]
Title: Test
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 640
PlayResY: 480

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,{\\an8}Hello\\Nworld
Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,{\\i1}Italic text{\\i0}
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,{\\p1}m 0 0 l 100 0 100 100 0 100{\\p0}
Comment: 0,0:00:06.00,0:00:07.00,Default,,0,0,0,,Should not translate
"""

FILTER_ASS_TEXT = r"""\
[Script Info]
Title: Test
ScriptType: v4.00+
PlayResX: 640
PlayResY: 480

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Normal
Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,{\an8}
Dialogue: 0,0:00:04.00,0:00:06.00,Default,,0,0,0,,
Comment: 0,0:00:06.00,0:00:07.00,Default,,0,0,0,,Comment
"""

SSA_TEXT = """\
[Script Info]
Title: Test
ScriptType: v4.00

[V4 Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, TertiaryColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, AlphaLevel, Encoding
Style: Default,Arial,20,16777215,255,65535,0,0,0,1,2,0,2,10,10,10,0,1

[Events]
Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: Marked=0,0:00:00.00,0:00:02.00,Default,,0,0,0,,Hello SSA
"""

SRT_TEXT = """\
1
00:00:00,000 --> 00:00:02,000
Hello world

2
00:00:02,000 --> 00:00:04,000
Second line
"""


class SubtitleIoTests(unittest.TestCase):
    def test_load_ass_preserves_styles_and_events(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.ass"
            path.write_text(ASS_TEXT, encoding="utf-8")
            doc = load_subtitle(path)
        self.assertEqual(doc.fmt, "ass")
        self.assertEqual(doc.suffix, ".ass")
        self.assertEqual(len(doc.subs.events), 4)
        self.assertEqual(doc.subs.styles["Default"].fontname, "Arial")
        self.assertEqual(doc.subs.events[0].start, 0)
        self.assertEqual(doc.subs.events[0].end, 2000)
        self.assertIn(r"{\an8}", doc.subs.events[0].text)

    def test_translatable_indices_skips_comments_and_drawings(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.ass"
            path.write_text(ASS_TEXT, encoding="utf-8")
            doc = load_subtitle(path)
        self.assertEqual(doc.translatable_indices(), [0, 1])

    def test_load_srt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.srt"
            path.write_text(SRT_TEXT, encoding="utf-8")
            doc = load_subtitle(path)
        self.assertEqual(doc.fmt, "srt")
        self.assertEqual(doc.suffix, ".srt")
        self.assertEqual(len(doc.subs.events), 2)
        self.assertEqual(doc.subs.events[1].start, 2000)
        self.assertEqual(doc.translatable_indices(), [0, 1])
        self.assertEqual(doc.subs.events[0].text, "Hello world")
        self.assertEqual(doc.subs.events[1].text, "Second line")

    def test_crlf_srt_text_is_clean(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.srt"
            path.write_bytes(
                b"1\r\n00:00:00,000 --> 00:00:02,000\r\nHello world\r\n\r\n"
                b"2\r\n00:00:02,000 --> 00:00:05,000\r\nLine one\r\nLine two\r\n"
            )
            doc = load_subtitle(path)
        self.assertEqual(doc.subs.events[0].text, "Hello world")
        self.assertEqual(doc.subs.events[1].text, "Line one\\NLine two")
        self.assertNotIn("\r", doc.subs.events[0].text)
        self.assertNotIn("\r", doc.subs.events[1].text)

    def test_gbk_srt_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.srt"
            content = "1\n00:00:00,000 --> 00:00:01,000\n你好世界，这是一句中文字幕。\n"
            path.write_bytes(content.encode("gbk"))
            doc = load_subtitle(path)
        self.assertIn("你好", doc.subs.events[0].text)
        self.assertIn("字幕", doc.subs.events[0].text)

    def test_cp1252_srt_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.srt"
            content = "1\n00:00:00,000 --> 00:00:01,000\nmañana, señor\n"
            path.write_bytes(content.encode("cp1252"))
            doc = load_subtitle(path)
        self.assertIn("mañana", doc.subs.events[0].text)

    def test_utf8_bom_srt(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.srt"
            path.write_bytes(b"\xef\xbb\xbf" + SRT_TEXT.encode("utf-8"))
            doc = load_subtitle(path)
        self.assertEqual(doc.subs.events[0].text, "Hello world")

    def test_unrecognized_encoding_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.srt"
            path.write_bytes(
                b"1\n00:00:00,000 --> 00:00:01,000\n\x81\n"
            )
            with self.assertRaises(ValueError):
                load_subtitle(path)

    def test_header_only_ass_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.ass"
            path.write_text(ASS_TEXT.split("[Events]")[0], encoding="utf-8")
            with self.assertRaises(ValueError):
                load_subtitle(path)

    def test_filter_skips_blank_and_override_only(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.ass"
            path.write_text(FILTER_ASS_TEXT, encoding="utf-8")
            doc = load_subtitle(path)
        self.assertEqual(doc.translatable_indices(), [0])

    def test_load_ssa_and_convert_suffix(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.ssa"
            path.write_text(SSA_TEXT, encoding="utf-8")
            doc = load_subtitle(path)
            self.assertEqual(doc.fmt, "ssa")
            self.assertEqual(doc.suffix, ".ass")
            self.assertEqual(doc.subs.events[0].text, "Hello SSA")
            target = Path(td) / "nested" / "b.ass"
            returned = save_subtitle(doc, target)
            self.assertEqual(returned, target)
            self.assertTrue(target.is_file())
            reloaded = load_subtitle(target)
        self.assertEqual(reloaded.subs.styles["Default"].fontname, "Arial")
        self.assertEqual(reloaded.subs.events[0].text, "Hello SSA")

    def test_save_roundtrip_preserves_times_and_style(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "a.ass"
            source.write_text(ASS_TEXT, encoding="utf-8")
            doc = load_subtitle(source)
            target = Path(td) / "nested" / "b.ass"
            returned = save_subtitle(doc, target)
            self.assertEqual(returned, target)
            reloaded = load_subtitle(target)
        self.assertEqual(reloaded.subs.styles["Default"].fontname, "Arial")
        self.assertEqual(reloaded.subs.events[0].start, 0)
        self.assertEqual(reloaded.subs.events[0].end, 2000)
        self.assertIn(r"{\an8}", reloaded.subs.events[0].text)

    def test_save_writes_utf8_and_creates_parent(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "a.srt"
            source.write_text(SRT_TEXT, encoding="utf-8")
            doc = load_subtitle(source)
            set_event_text(doc, 0, "你好世界")
            target = Path(td) / "nested" / "out.srt"
            returned = save_subtitle(doc, target)
            self.assertEqual(returned, target)
            self.assertIn("你好世界".encode("utf-8"), target.read_bytes())

    def test_strip_formatting(self):
        self.assertEqual(
            strip_formatting(r"{\an8}Hello\Nworld"),
            "Hello\nworld",
        )
        self.assertEqual(strip_formatting(r"{\i1}Italic{\i0}"), "Italic")
        self.assertEqual(strip_formatting(r"  {\i1}A\hB\NC  "), "A B\nC")

    def test_set_event_text(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.srt"
            path.write_text(SRT_TEXT, encoding="utf-8")
            doc = load_subtitle(path)
        set_event_text(doc, 0, "你好")
        self.assertEqual(doc.subs.events[0].text, "你好")

    def test_unsupported_extension_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "a.txt"
            path.write_text("hello", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_subtitle(path)


class SubtitleTagTests(unittest.TestCase):
    def test_protect_and_restore_roundtrip(self):
        original = r"{\an8}Hello\Nworld"
        masked, tokens = protect_tags(original)
        self.assertEqual(masked, "⟦0⟧Hello⟦1⟧world")
        self.assertEqual(tokens, [r"{\an8}", r"\N"])
        self.assertEqual(restore_tags(masked, tokens), original)

    def test_restore_rejects_missing_placeholder(self):
        _, tokens = protect_tags(r"{\an8}Hello\Nworld")
        self.assertIsNone(restore_tags("⟦0⟧Hello", tokens))

    def test_restore_rejects_out_of_order_placeholder(self):
        _, tokens = protect_tags(r"{\an8}Hello\Nworld")
        self.assertIsNone(restore_tags("⟦1⟧Hello⟦0⟧world", tokens))

    def test_protect_without_tags(self):
        masked, tokens = protect_tags("Hello")
        self.assertEqual(masked, "Hello")
        self.assertEqual(tokens, [])
        self.assertEqual(restore_tags("Hello", tokens), "Hello")

    def test_normalize_text(self):
        self.assertEqual(normalize_text("  Hello   World "), "hello world")

    def test_rebuild_fallback_keeps_leading_tags(self):
        self.assertEqual(
            rebuild_fallback(r"{\an8}Hello\Nworld", "你好\n世界"),
            r"{\an8}你好\N世界",
        )

    def test_restore_rejects_duplicate_placeholder(self):
        _, tokens = protect_tags(r"{\an8}Hello\Nworld")
        self.assertIsNone(restore_tags("⟦0⟧Hello⟦0⟧", tokens))

    def test_restore_handles_multi_digit_placeholders(self):
        tokens = [f"<{index}>" for index in range(12)]
        text = "".join(f"⟦{index}⟧" for index in range(12))
        self.assertEqual(restore_tags(text, tokens), "".join(tokens))

    def test_restore_rejects_huge_placeholder_without_raising(self):
        _, tokens = protect_tags(r"{\an8}Hello")
        huge = "⟦" + "9" * 5000 + "⟧"
        self.assertIsNone(restore_tags(huge, tokens))

    def test_protect_handles_n_h_and_uppercase_n(self):
        masked, tokens = protect_tags(r"A\nB\hC\ND")
        self.assertEqual(masked, "A⟦0⟧B⟦1⟧C⟦2⟧D")
        self.assertEqual(tokens, [r"\n", r"\h", r"\N"])
        self.assertEqual(restore_tags(masked, tokens), r"A\nB\hC\ND")

    def test_rebuild_fallback_normalizes_newline_variants(self):
        self.assertEqual(
            rebuild_fallback(r"{\an8}Hello\Nworld", "你好\r\n世界"),
            r"{\an8}你好\N世界",
        )
        self.assertEqual(
            rebuild_fallback(r"{\an8}Hello\Nworld", "你好\r世界"),
            r"{\an8}你好\N世界",
        )

    def test_rebuild_fallback_without_leading_tags(self):
        self.assertEqual(rebuild_fallback("Hello", "你好\n世界"), r"你好\N世界")

    def test_empty_text_roundtrip(self):
        masked, tokens = protect_tags("")
        self.assertEqual(masked, "")
        self.assertEqual(tokens, [])
        self.assertEqual(restore_tags("", tokens), "")


if __name__ == "__main__":
    unittest.main()
