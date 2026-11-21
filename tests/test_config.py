import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import (
    AppConfig,
    LANGUAGE_ISO639_2,
    LLMSettings,
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
    TARGET_SUFFIX,
    TARGET_TRACK_TITLE,
    app_dir,
    is_llm_configured,
    load_config,
    save_config,
    target_suffix,
)


class ConfigTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = load_config(Path(td) / "missing.json")
        self.assertEqual(cfg.llm.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(cfg.llm.model, "deepseek-chat")
        self.assertEqual(cfg.source_lang, "en")
        self.assertEqual(cfg.target_lang, "zh-Hans")
        self.assertTrue(cfg.export_subtitle)

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            cfg = AppConfig(
                llm=LLMSettings(api_key="sk-test", model="deepseek-chat", batch_size=20),
                source_lang="ja",
                target_lang="zh-Hant",
                export_subtitle=False,
            )
            save_config(cfg, path)
            loaded = load_config(path)
        self.assertEqual(loaded.llm.api_key, "sk-test")
        self.assertEqual(loaded.llm.batch_size, 20)
        self.assertEqual(loaded.source_lang, "ja")
        self.assertEqual(loaded.target_lang, "zh-Hant")
        self.assertFalse(loaded.export_subtitle)

    def test_partial_json_uses_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(json.dumps({"llm": {"model": "x"}}), encoding="utf-8")
            cfg = load_config(path)
        self.assertEqual(cfg.llm.model, "x")
        self.assertEqual(cfg.llm.base_url, "https://api.deepseek.com/v1")
        self.assertEqual(cfg.llm.concurrency, 3)

    def test_corrupt_json_is_backed_up(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text("{not json", encoding="utf-8")
            cfg = load_config(path)
            backup = path.with_name(path.name + ".bak")
            self.assertTrue(backup.exists())
        self.assertEqual(cfg.llm.model, "deepseek-chat")

    def test_values_are_clamped(self):
        settings = LLMSettings.from_dict(
            {
                "batch_size": 999,
                "concurrency": 0,
                "timeout": 5,
                "temperature": 9,
            }
        )
        self.assertEqual(settings.batch_size, 80)
        self.assertEqual(settings.concurrency, 1)
        self.assertEqual(settings.timeout, 30)
        self.assertEqual(settings.temperature, 2.0)

    def test_unknown_languages_fall_back(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                json.dumps({"translation": {"source_lang": "xx", "target_lang": "yy"}}),
                encoding="utf-8",
            )
            cfg = load_config(path)
        self.assertEqual(cfg.source_lang, "en")
        self.assertEqual(cfg.target_lang, "zh-Hans")

    def test_language_helpers(self):
        self.assertEqual(target_suffix("zh-Hans"), "zh")
        self.assertEqual(target_suffix("zh-Hant"), "cht")
        self.assertEqual(target_suffix("xx"), "translated")
        self.assertEqual(LANGUAGE_ISO639_2["zh-Hans"], "chi")

    def test_language_options_are_unified_top_30(self):
        self.assertEqual(SOURCE_LANGUAGES, TARGET_LANGUAGES)
        codes = [code for code, _ in SOURCE_LANGUAGES]
        labels = [label for _, label in SOURCE_LANGUAGES]
        self.assertEqual(len(codes), 30)
        self.assertEqual(len(set(codes)), 30)
        self.assertEqual(len(set(labels)), 30)
        self.assertTrue(
            {
                "en",
                "zh-Hans",
                "zh-Hant",
                "hi",
                "es",
                "fr",
                "ar",
                "bn",
                "pt",
                "ru",
                "ja",
                "de",
                "ko",
                "it",
                "tr",
                "vi",
                "th",
            }.issubset(codes)
        )

    def test_default_languages_are_english_to_simplified_chinese(self):
        cfg = AppConfig()
        self.assertEqual(cfg.source_lang, "en")
        self.assertEqual(cfg.target_lang, "zh-Hans")
        self.assertIn(
            cfg.source_lang, {code for code, _ in SOURCE_LANGUAGES}
        )
        self.assertIn(
            cfg.target_lang, {code for code, _ in TARGET_LANGUAGES}
        )

    def test_every_target_language_has_complete_metadata(self):
        self.assertEqual(len(TARGET_LANGUAGES), 30)
        suffixes = []
        for code, _ in TARGET_LANGUAGES:
            with self.subTest(code=code):
                self.assertIn(code, TARGET_SUFFIX)
                self.assertIn(code, TARGET_TRACK_TITLE)
                self.assertIn(code, LANGUAGE_ISO639_2)
                self.assertNotEqual(TARGET_SUFFIX[code], "translated")
                self.assertTrue(TARGET_TRACK_TITLE[code])
                self.assertEqual(
                    LANGUAGE_ISO639_2[code],
                    LANGUAGE_ISO639_2[code].lower(),
                )
                suffixes.append(TARGET_SUFFIX[code])
        self.assertEqual(len(set(suffixes)), len(TARGET_LANGUAGES))

    def test_is_llm_configured(self):
        self.assertFalse(is_llm_configured(LLMSettings(api_key="")))
        self.assertTrue(is_llm_configured(LLMSettings(api_key="sk-x")))

    def test_app_dir_exists(self):
        self.assertTrue(app_dir().is_dir())

    def test_directory_path_returns_defaults_without_renaming(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.mkdir()
            cfg = load_config(path)
            self.assertTrue(path.is_dir())
            self.assertFalse(path.with_name(path.name + ".bak").exists())
        self.assertEqual(cfg.llm.model, "deepseek-chat")

    def test_non_finite_numbers_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                '{"llm": {"batch_size": Infinity, "timeout": 1e999, "temperature": NaN}}',
                encoding="utf-8",
            )
            cfg = load_config(path)
        self.assertEqual(cfg.llm.batch_size, 30)
        self.assertEqual(cfg.llm.timeout, 120)
        self.assertEqual(cfg.llm.temperature, 0.3)

    def test_non_object_json_is_backed_up(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text("[]", encoding="utf-8")
            cfg = load_config(path)
            self.assertTrue(path.with_name(path.name + ".bak").exists())
        self.assertEqual(cfg.llm.model, "deepseek-chat")

    def test_save_creates_parents_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "config.json"
            save_config(AppConfig(), path)
            self.assertTrue(path.is_file())
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_utf8_bom_config_is_read(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            payload = json.dumps({"llm": {"model": "custom"}}).encode("utf-8")
            path.write_bytes(b"\xef\xbb\xbf" + payload)
            cfg = load_config(path)
        self.assertEqual(cfg.llm.model, "custom")

    def test_api_key_is_not_in_repr(self):
        self.assertNotIn("sk-secret", repr(LLMSettings(api_key="sk-secret")))

    def test_numeric_bounds_both_directions(self):
        cases = [
            ("batch_size", 7, 8),
            ("batch_size", 81, 80),
            ("concurrency", 0, 1),
            ("concurrency", 9, 8),
            ("timeout", 29, 30),
            ("timeout", 601, 600),
            ("temperature", -0.1, 0.0),
            ("temperature", 2.1, 2.0),
        ]
        for key, value, expected in cases:
            with self.subTest(key=key, value=value):
                settings = LLMSettings.from_dict({key: value})
                self.assertEqual(getattr(settings, key), expected)

    def test_app_dir_is_project_root_in_dev(self):
        self.assertEqual(app_dir(), Path(__file__).resolve().parents[1])

    def test_app_dir_frozen_uses_executable_directory(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(Path("C:/app/SubtitleTranslator.exe"))
        ):
            self.assertEqual(app_dir(), Path("C:/app"))


if __name__ == "__main__":
    unittest.main()
