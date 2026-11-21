import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import LLMSettings
from app.ffmpeg_tools import (
    extract_subtitle,
    ffmpeg_path,
    mux_subtitle,
    probe_subtitle_tracks,
)
from app.llm_client import LLMClient
from app.subtitle_io import load_subtitle, save_subtitle
from app.translator import TranslationJob
from tests.fake_llm_server import FakeLLMServer
from tests.make_test_mkv import (
    make_test_mkv,
    parse_duration_seconds,
    probe_media_text,
)


def ffmpeg_available() -> bool:
    try:
        ffmpeg_path()
        return True
    except FileNotFoundError:
        return False


@unittest.skipUnless(ffmpeg_available(), "ffmpeg 不可用")
class EndToEndTests(unittest.TestCase):
    def test_extract_translate_mux(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            work = Path(temp_dir)
            video = make_test_mkv(work / "movie.mkv")

            video_media = probe_media_text(video)
            duration = parse_duration_seconds(video_media)
            self.assertGreaterEqual(duration, 9.5)
            self.assertLessEqual(duration, 10.5)
            self.assertIn("Video:", video_media)
            self.assertIn("Audio:", video_media)

            tracks = probe_subtitle_tracks(video)
            self.assertEqual(len(tracks), 1)
            self.assertTrue(
                any(track.language == "eng" for track in tracks)
            )

            extracted = extract_subtitle(
                video, tracks[0], work / "source.ass"
            )
            document = load_subtitle(extracted)
            self.assertEqual(
                len(document.translatable_indices()), 2
            )

            with patch.dict(
                os.environ,
                {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"},
            ):
                with FakeLLMServer() as server:
                    settings = LLMSettings(
                        base_url=server.base_url,
                        api_key="test-key",
                        model="fake-model",
                        batch_size=1,
                        concurrency=2,
                        timeout=30,
                    )
                    client = LLMClient(settings)
                    self.assertEqual(client.test_connection(), "OK")
                    job = TranslationJob(
                        document=document,
                        client=client,
                        batch_size=settings.batch_size,
                        concurrency=settings.concurrency,
                        source_lang="en",
                        target_lang="zh-Hans",
                        movie_name="movie",
                        partial_path=work / "partial.ass",
                    )
                    report = job.run()
                    self.assertEqual(server.request_count, 3)

            self.assertEqual(report.total_events, 2)
            self.assertEqual(report.batches, 2)
            translated = work / "translated.ass"
            save_subtitle(document, translated)

            output = work / "movie.zh.mkv"
            mux_subtitle(
                video,
                translated,
                output,
                subtitle_count=1,
                language_code="chi",
                title="中文（简体）",
            )
            self.assertTrue(output.exists())

            output_media = probe_media_text(output)
            output_duration = parse_duration_seconds(output_media)
            self.assertGreaterEqual(output_duration, 9.5)
            self.assertLessEqual(output_duration, 10.5)
            self.assertIn("Video:", output_media)
            self.assertIn("Audio:", output_media)

            new_tracks = probe_subtitle_tracks(output)
            self.assertEqual(len(new_tracks), 2)
            self.assertEqual(new_tracks[0].language, "chi")
            self.assertEqual(new_tracks[0].title, "中文（简体）")
            self.assertTrue(
                any(track.language == "eng" for track in new_tracks)
            )

            english = [
                track for track in new_tracks if track.language == "eng"
            ]
            chinese = [
                track for track in new_tracks if track.language == "chi"
            ]
            self.assertEqual(len(english), 1)
            self.assertEqual(len(chinese), 1)
            self.assertEqual(english[0].title, "English")
            self.assertEqual(english[0].codec, "ass")
            self.assertEqual(chinese[0].title, "中文（简体）")

            english_file = extract_subtitle(
                output, english[0], work / "english.ass"
            )
            english_doc = load_subtitle(english_file)
            english_texts = [
                event.text
                for event in english_doc.subs.events
                if event.type == "Dialogue"
            ]
            self.assertEqual(
                english_texts,
                [r"{\an8}Hello\Nworld", "Goodbye"],
            )
            self.assertFalse(
                any("中" in text for text in english_texts)
            )

            chinese_file = extract_subtitle(
                output, chinese[0], work / "chinese.ass"
            )
            chinese_doc = load_subtitle(chinese_file)
            texts = [
                event.text
                for event in chinese_doc.subs.events
                if event.type == "Dialogue"
            ]
            self.assertEqual(
                texts,
                [r"中{\an8}Hello\Nworld", "中Goodbye"],
            )

            self.assertEqual(report.batches, 2)


if __name__ == "__main__":
    unittest.main()
