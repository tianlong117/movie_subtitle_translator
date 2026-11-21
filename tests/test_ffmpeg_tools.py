import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ffmpeg_tools import (
    SubtitleTrack,
    _probe_input,
    _run_ffmpeg,
    build_extract_args,
    build_mux_args,
    extract_subtitle,
    ffmpeg_path,
    find_text_track,
    is_text_track,
    mux_subtitle,
    parse_stream_info,
    probe_subtitle_tracks,
    text_tracks,
)

SAMPLE_FFMPEG_OUTPUT = """\
Input #0, matroska,webm, from 'movie.mkv':
  Duration: 00:01:00.00, start: 0.000000, bitrate: 1000 kb/s
  Stream #0:0: Video: h264 (High), yuv420p, 1920x1080, 24 fps
  Stream #0:1(eng): Audio: aac (LC), 48000 Hz, stereo, fltp
  Stream #0:2(eng): Subtitle: subrip (srt)
    Metadata:
      title           : English SDH
  Stream #0:3(chi): Subtitle: hdmv_pgs_subtitle (pgssub)
    Metadata:
      title           : Chinese
  Stream #0:4(jpn): Subtitle: ass (ssa)
    Metadata:
      title           : 日本語
"""

VARIANT_OUTPUT = """\
  Stream #0:0: Video: h264, yuv420p, 1920x1080
    Metadata:
      title           : Movie
  Stream #0:1[0x2](eng): Subtitle: mov_text (tx3g / 0x67337874), 0 kb/s (default)
  Stream #0:2: Subtitle: subrip (srt)
    Metadata:
      title           : No language
"""

TITLE_BLEED_OUTPUT = """\
  Stream #0:0(eng): Subtitle: subrip (srt)
    Metadata:
      title           : First
  Stream #0:1: Video: h264, yuv420p, 1920x1080
    Metadata:
      title           : Movie
  Stream #0:2(eng): Subtitle: ass (ssa)
"""


class FfmpegProbeTests(unittest.TestCase):
    def setUp(self):
        self.tracks = parse_stream_info(SAMPLE_FFMPEG_OUTPUT)

    def test_parses_only_subtitle_streams(self):
        self.assertEqual([track.index for track in self.tracks], [2, 3, 4])

    def test_parses_language_codec_and_title(self):
        first, second, third = self.tracks
        self.assertEqual(
            (first.language, first.codec, first.title),
            ("eng", "subrip", "English SDH"),
        )
        self.assertEqual(second.codec, "hdmv_pgs_subtitle")
        self.assertTrue(second.image_based)
        self.assertEqual(third.title, "日本語")

    def test_text_tracks_excludes_image_tracks(self):
        self.assertEqual([track.index for track in text_tracks(self.tracks)], [2, 4])

    def test_find_text_track_prefers_english(self):
        self.assertEqual(find_text_track(self.tracks).index, 2)

    def test_find_text_track_falls_back_to_first_text(self):
        tracks = [
            SubtitleTrack(0, "jpn", "ass"),
            SubtitleTrack(1, "chi", "subrip"),
        ]
        self.assertEqual(find_text_track(tracks).index, 0)

    def test_find_text_track_returns_none_when_only_images(self):
        tracks = [
            SubtitleTrack(0, "eng", "hdmv_pgs_subtitle", image_based=True)
        ]
        self.assertIsNone(find_text_track(tracks))

    def test_is_text_track(self):
        self.assertTrue(is_text_track(SubtitleTrack(0, "eng", "ass")))
        self.assertFalse(
            is_text_track(
                SubtitleTrack(0, "eng", "hdmv_pgs_subtitle", image_based=True)
            )
        )
        self.assertFalse(is_text_track(SubtitleTrack(0, "eng", "unknown_codec")))
        self.assertFalse(
            is_text_track(SubtitleTrack(0, "eng", "mov_text"))
        )

    def test_label_text_track(self):
        track = SubtitleTrack(2, "eng", "ass", "English")
        self.assertEqual(track.label, "#2 | eng | ass | English")

    def test_label_image_track_marks_unsupported(self):
        track = SubtitleTrack(
            3, "chi", "hdmv_pgs_subtitle", "Chinese", image_based=True
        )
        self.assertIn("不支持", track.label)

    def test_label_empty_language_is_omitted(self):
        track = SubtitleTrack(4, "", "subrip")
        self.assertEqual(track.label, "#4 | subrip")

    def test_label_unknown_codec_marks_unsupported(self):
        track = SubtitleTrack(5, "eng", "unknown_codec")
        self.assertIn("不支持", track.label)

    def test_parser_handles_brackets_default_and_missing_language(self):
        tracks = parse_stream_info(VARIANT_OUTPUT)
        self.assertEqual([track.index for track in tracks], [1, 2])
        self.assertEqual(tracks[0].codec, "mov_text")
        self.assertEqual(tracks[0].language, "eng")
        self.assertEqual(tracks[0].title, "")
        self.assertEqual(tracks[1].language, "")
        self.assertEqual(tracks[1].title, "No language")

    def test_title_does_not_bleed_from_later_stream(self):
        tracks = parse_stream_info(TITLE_BLEED_OUTPUT)
        self.assertEqual([track.index for track in tracks], [0, 2])
        self.assertEqual(tracks[0].title, "First")
        self.assertEqual(tracks[1].title, "")

    def test_find_text_track_does_not_treat_enq_as_english(self):
        tracks = [
            SubtitleTrack(0, "jpn", "ass"),
            SubtitleTrack(1, "enq", "ass"),
        ]
        self.assertEqual(find_text_track(tracks).index, 0)

    def test_ffmpeg_path_is_available(self):
        try:
            path = ffmpeg_path()
        except FileNotFoundError:
            self.skipTest("ffmpeg 不可用")
        self.assertTrue(Path(path).is_file())


class ProbeTests(unittest.TestCase):
    def test_probe_delegates_to_parser(self):
        with patch(
            "app.ffmpeg_tools._probe_input", return_value=SAMPLE_FFMPEG_OUTPUT
        ) as mocked:
            tracks = probe_subtitle_tracks("movie.mkv")
        self.assertEqual([track.index for track in tracks], [2, 3, 4])
        mocked.assert_called_once_with("movie.mkv", timeout=120)

    def test_probe_raises_on_unreadable_output(self):
        with patch(
            "app.ffmpeg_tools._probe_input",
            return_value="Invalid data found when processing input",
        ):
            with self.assertRaises(ValueError):
                probe_subtitle_tracks("bad.mkv")

    def test_probe_input_timeout_includes_stderr_tail(self):
        error = subprocess.TimeoutExpired(
            cmd=["ffmpeg"], timeout=1, stderr=b"partial stderr line"
        )
        with patch(
            "app.ffmpeg_tools.ffmpeg_path", return_value="ffmpeg"
        ), patch(
            "app.ffmpeg_tools.subprocess.run", side_effect=error
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _probe_input("movie.mkv", timeout=1)
        message = str(ctx.exception)
        self.assertIn("读取视频信息超时", message)
        self.assertIn("partial stderr line", message)


class FfmpegCommandTests(unittest.TestCase):
    def test_extract_ass_args_copy(self):
        track = SubtitleTrack(index=2, language="eng", codec="ass")
        args = build_extract_args("in.mkv", track, "out.ass")
        self.assertEqual(
            args,
            ["-i", "in.mkv", "-map", "0:2", "-c:s", "copy", "out.ass"],
        )

    def test_extract_srt_args_transcode(self):
        track = SubtitleTrack(index=3, language="eng", codec="mov_text")
        args = build_extract_args("in.mkv", track, "out.srt")
        self.assertEqual(
            args,
            ["-i", "in.mkv", "-map", "0:3", "-c:s", "srt", "out.srt"],
        )

    def test_mux_args_puts_new_subtitle_track_first(self):
        args = build_mux_args(
            "movie.mkv", "zh.ass", "out.mkv", 2, "chi", "中文（简体）"
        )
        self.assertEqual(
            args,
            [
                "-i", "movie.mkv",
                "-i", "zh.ass",
                "-map", "0",
                "-map", "1",
                "-map", "-0:s",
                "-map", "0:s",
                "-c", "copy",
                "-map_chapters", "0",
                "-metadata:s:s:0", "language=chi",
                "-metadata:s:s:0", "title=中文（简体）",
                "-disposition:s:0", "default",
                "out.mkv",
            ],
        )

    def test_mux_args_without_original_subtitles(self):
        args = build_mux_args(
            "movie.mkv", "zh.ass", "out.mkv", 0, "chi", "中文（简体）"
        )
        self.assertEqual(
            args,
            [
                "-i", "movie.mkv",
                "-i", "zh.ass",
                "-map", "0",
                "-map", "1",
                "-c", "copy",
                "-map_chapters", "0",
                "-metadata:s:s:0", "language=chi",
                "-metadata:s:s:0", "title=中文（简体）",
                "-disposition:s:0", "default",
                "out.mkv",
            ],
        )


class FfmpegFailureTests(unittest.TestCase):
    def test_extract_removes_partial_output_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "out.srt"
            track = SubtitleTrack(0, "eng", "subrip")

            def fake_run(args, timeout):
                Path(args[-1]).write_text("partial", encoding="utf-8")
                raise RuntimeError("boom")

            with patch("app.ffmpeg_tools._run_ffmpeg", side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    extract_subtitle("in.mkv", track, output)
            self.assertFalse(output.exists())

    def test_extract_rejects_output_same_as_video(self):
        with tempfile.TemporaryDirectory() as td:
            video = Path(td) / "movie.mkv"
            video.write_bytes(b"x")
            track = SubtitleTrack(0, "eng", "subrip")
            with self.assertRaises(ValueError):
                extract_subtitle(video, track, video)
            self.assertTrue(video.exists())

    def test_mux_removes_temp_on_replace_failure(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            video = work / "movie.mkv"
            video.write_bytes(b"x")
            subtitle = work / "zh.ass"
            subtitle.write_text("", encoding="utf-8")
            output = work / "movie.zh.mkv"
            output.mkdir()

            def fake_run(args, timeout):
                Path(args[-1]).write_bytes(b"temp-mkv")
                return subprocess.CompletedProcess(args=args, returncode=0)

            with patch("app.ffmpeg_tools._run_ffmpeg", side_effect=fake_run):
                with self.assertRaises(RuntimeError):
                    mux_subtitle(video, subtitle, output, 0, "chi", "中文")
            self.assertEqual(list(work.glob("movie.zh.tmp-*.mkv")), [])

    def test_mux_rejects_non_mkv_input(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            with self.assertRaises(ValueError):
                mux_subtitle(
                    work / "movie.mp4",
                    work / "zh.ass",
                    work / "out.mkv",
                    0,
                    "chi",
                    "中文",
                )

    def test_mux_rejects_non_mkv_output(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            with self.assertRaises(ValueError):
                mux_subtitle(
                    work / "movie.mkv",
                    work / "zh.ass",
                    work / "out.mp4",
                    0,
                    "chi",
                    "中文",
                )

    def test_mux_rejects_negative_subtitle_count(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td)
            with self.assertRaises(ValueError):
                mux_subtitle(
                    work / "movie.mkv",
                    work / "zh.ass",
                    work / "out.mkv",
                    -1,
                    "chi",
                    "中文",
                )

    def test_run_ffmpeg_timeout_includes_context(self):
        error = subprocess.TimeoutExpired(
            cmd=["ffmpeg"], timeout=5, stderr=b"partial stderr"
        )
        with patch(
            "app.ffmpeg_tools.ffmpeg_path", return_value="ffmpeg"
        ), patch(
            "app.ffmpeg_tools.subprocess.run", side_effect=error
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run_ffmpeg(["-i", "x"], timeout=5)
        message = str(ctx.exception)
        self.assertIn("5 秒", message)
        self.assertIn("partial stderr", message)


if __name__ == "__main__":
    unittest.main()
