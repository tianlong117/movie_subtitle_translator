import os
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

import pysubs2

from app.config import AppConfig, LLMSettings
from app.gui import (
    COPYRIGHT_TEXT,
    SubtitleTranslatorApp,
    cancel_message,
    center_dialog_position,
    default_output_path,
    default_subtitle_output_path,
    expected_primary_suffix,
    make_partial_path,
    needs_srt_compat,
    save_subtitle_outputs,
    srt_compat_path,
    unique_sidecar_path,
)
from app.subtitle_io import (
    SubtitleDocument,
    load_subtitle,
    save_subtitle,
)
from tests.fake_llm_server import FakeLLMServer


def tk_available() -> bool:
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except tk.TclError:
        return False


def make_document(
    fmt: str = "ass", text: str = "Hello"
) -> SubtitleDocument:
    subs = pysubs2.SSAFile()
    subs.events.append(
        pysubs2.SSAEvent(start=0, end=1000, text=text)
    )
    return SubtitleDocument(subs=subs, fmt=fmt)


class GuiHelperTests(unittest.TestCase):
    def test_default_output_path_simplified_chinese(self):
        result = default_output_path(Path("D:/movies/Inception.mkv"), "zh-Hans")
        self.assertEqual(result, Path("D:/movies/Inception.zh.mkv"))

    def test_default_output_path_traditional_chinese(self):
        result = default_output_path(Path("D:/movies/Inception.mkv"), "zh-Hant")
        self.assertEqual(result, Path("D:/movies/Inception.cht.mkv"))

    def test_default_output_path_unknown_language(self):
        result = default_output_path(Path("D:/movies/Inception.mkv"), "xx")
        self.assertEqual(result, Path("D:/movies/Inception.translated.mkv"))

    def test_cancel_message_with_path(self):
        path = Path("D:/movies/movie.zh.partial.srt")
        self.assertIn(str(path), cancel_message(path))

    def test_cancel_message_without_path(self):
        self.assertIn("保存失败", cancel_message(None))

    def test_make_partial_path_is_run_unique(self):
        video = Path("D:/movies/Movie.mkv")
        first = make_partial_path(
            video, "zh", ".srt", Path("D:/movies")
        )
        second = make_partial_path(
            video, "zh", ".srt", Path("D:/movies")
        )
        self.assertNotEqual(first, second)
        self.assertIn(".partial-", first.name)
        self.assertTrue(first.name.startswith("Movie.zh.partial-"))
        self.assertTrue(first.name.endswith(".srt"))

    def test_unique_sidecar_path_returns_same_when_free(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "movie.zh.ass"
            self.assertEqual(unique_sidecar_path(path), path)

    def test_unique_sidecar_path_avoids_existing(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "movie.zh.ass"
            path.write_text("existing", encoding="utf-8")
            candidate = unique_sidecar_path(path)
            self.assertEqual(candidate, Path(td) / "movie.zh (1).ass")
            self.assertNotEqual(candidate, path)

    def test_subtitle_output_paths_use_target_language(self):
        self.assertEqual(
            default_subtitle_output_path(
                Path("D:/movies/Movie.en.srt"), "zh-Hans"
            ),
            Path("D:/movies/Movie.en.zh.srt"),
        )
        self.assertEqual(
            default_subtitle_output_path(
                Path("D:/movies/Movie.en.ass"), "ja"
            ),
            Path("D:/movies/Movie.en.ja.ass"),
        )
        self.assertEqual(
            default_subtitle_output_path(
                Path("D:/movies/Movie.en.ssa"), "zh-Hant"
            ),
            Path("D:/movies/Movie.en.cht.ass"),
        )

    def test_expected_primary_suffix(self):
        self.assertEqual(
            expected_primary_suffix(Path("Movie.srt")), ".srt"
        )
        self.assertEqual(
            expected_primary_suffix(Path("Movie.ass")), ".ass"
        )
        self.assertEqual(
            expected_primary_suffix(Path("Movie.ssa")), ".ass"
        )

    def test_needs_srt_compat(self):
        self.assertFalse(needs_srt_compat(Path("Movie.srt")))
        self.assertTrue(needs_srt_compat(Path("Movie.ass")))
        self.assertTrue(needs_srt_compat(Path("Movie.ssa")))

    def test_srt_compat_path(self):
        self.assertEqual(
            srt_compat_path(Path("Movie.en.ja.ass")),
            Path("Movie.en.ja.srt"),
        )

    def test_save_subtitle_outputs_srt_source_writes_one_file(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "Movie.en.zh.srt"
            outputs, warning = save_subtitle_outputs(
                make_document("srt"),
                Path("Movie.en.srt"),
                output,
            )
            self.assertEqual(outputs, [output])
            self.assertEqual(warning, "")
            self.assertTrue(output.is_file())
            self.assertFalse(output.with_suffix(".ass").exists())

    def test_save_subtitle_outputs_ass_source_writes_srt_compat(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "Movie.en.ja.ass"
            outputs, warning = save_subtitle_outputs(
                make_document("ass"),
                Path("Movie.en.ass"),
                output,
            )
            srt_path = Path(td) / "Movie.en.ja.srt"
            self.assertEqual(outputs, [output, srt_path])
            self.assertEqual(warning, "")
            self.assertTrue(output.is_file())
            self.assertTrue(srt_path.is_file())
            loaded = load_subtitle(srt_path)
            self.assertEqual(loaded.subs.events[0].text, "Hello")

    def test_save_subtitle_outputs_reports_srt_failure(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "Movie.en.ja.ass"
            real_save = save_subtitle

            def fake_save(document, path):
                if Path(path).suffix.lower() == ".srt":
                    raise OSError("disk full")
                real_save(document, path)

            with patch(
                "app.gui.save_subtitle", side_effect=fake_save
            ):
                outputs, warning = save_subtitle_outputs(
                    make_document("ass"),
                    Path("Movie.en.ass"),
                    output,
                )
            self.assertEqual(outputs, [output])
            self.assertIn("SRT 兼容版生成失败", warning)
            self.assertTrue(output.is_file())

    def test_save_subtitle_outputs_primary_failure_propagates(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "Movie.en.ja.ass"

            def fake_save(document, path):
                raise OSError("disk full")

            with patch(
                "app.gui.save_subtitle", side_effect=fake_save
            ):
                with self.assertRaises(OSError):
                    save_subtitle_outputs(
                        make_document("ass"),
                        Path("Movie.en.ass"),
                        output,
                    )

    def test_copyright_text_contains_required_notices(self):
        for phrase in (
            "软件开发者：天龙",
            "自由软件",
            "不提供、不存储、不分发",
            "版权责任由用户自行承担",
        ):
            self.assertIn(phrase, COPYRIGHT_TEXT)

    def test_center_dialog_position(self):
        self.assertEqual(
            center_dialog_position(
                100, 50, 800, 600, 400, 300
            ),
            (300, 200),
        )
        self.assertEqual(
            center_dialog_position(
                100, 50, 200, 100, 400, 300
            ),
            (100, 50),
        )


@unittest.skipUnless(tk_available(), "Tk 不可用")
class StandaloneWorkerTests(unittest.TestCase):
    def test_ass_source_writes_ass_and_srt(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            source = work / "Movie.en.ass"
            save_subtitle(make_document("ass"), source)
            output = work / "Movie.en.zh.ass"

            with patch.dict(
                os.environ,
                {
                    "NO_PROXY": "127.0.0.1",
                    "no_proxy": "127.0.0.1",
                },
            ):
                with FakeLLMServer() as server:
                    root = tk.Tk()
                    root.withdraw()
                    try:
                        app = SubtitleTranslatorApp(root)
                        app.config = AppConfig(
                            llm=LLMSettings(
                                base_url=server.base_url,
                                api_key="test-key",
                                model="fake-model",
                                batch_size=1,
                                concurrency=1,
                                timeout=30,
                            ),
                            source_lang="en",
                            target_lang="zh-Hans",
                        )
                        app.cancel_event = threading.Event()
                        app._run_subtitle_task(
                            source, output, "en", "zh-Hans"
                        )
                    finally:
                        root.destroy()

            self.assertTrue(output.is_file())
            srt_output = work / "Movie.en.zh.srt"
            self.assertTrue(srt_output.is_file())
            self.assertIn(
                "中Hello",
                load_subtitle(output).subs.events[0].text,
            )
            self.assertIn(
                "中Hello",
                load_subtitle(srt_output).subs.events[0].text,
            )


@unittest.skipUnless(tk_available(), "Tk 不可用")
class LanguageDetectionTests(unittest.TestCase):
    def test_detects_source_language_and_updates_ui(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            source = work / "sample.ja.srt"
            save_subtitle(
                make_document("srt", "こんにちは"), source
            )

            with patch.dict(
                os.environ,
                {
                    "NO_PROXY": "127.0.0.1",
                    "no_proxy": "127.0.0.1",
                },
            ):
                with FakeLLMServer() as server:
                    root = tk.Tk()
                    root.withdraw()
                    try:
                        app = SubtitleTranslatorApp(root)
                        app.config = AppConfig(
                            llm=LLMSettings(
                                base_url=server.base_url,
                                api_key="test-key",
                                model="fake-model",
                                batch_size=1,
                                concurrency=1,
                                timeout=30,
                            ),
                            source_lang="en",
                            target_lang="zh-Hans",
                        )
                        app.video_path = None
                        app.external_sub = source
                        app.detect_serial = 1
                        app.detecting = True
                        app.source_var.set("英语")
                        app._detect_language_worker(
                            source, 1, app.config.llm
                        )
                        app._poll_messages()
                        self.assertEqual(
                            app.source_var.get(), "日语"
                        )
                        self.assertFalse(app.detecting)
                        self.assertEqual(
                            app.status_var.get(), "仅字幕模式"
                        )
                    finally:
                        root.destroy()

    def test_manual_source_change_cancels_pending_detection(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = SubtitleTranslatorApp(root)
            app.external_sub = Path("sample.ja.srt")
            app.detect_serial = 3
            app.detecting = True
            app._refresh_detect_button()
            app._on_source_changed()
            self.assertEqual(app.detect_serial, 4)
            self.assertFalse(app.detecting)
            self.assertEqual(
                app.detect_lang_btn.cget("text"), "AI 识别"
            )
            self.assertEqual(
                app.status_var.get(), "仅字幕模式"
            )
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
