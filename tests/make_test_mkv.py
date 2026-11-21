from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from app.ffmpeg_tools import ffmpeg_path

_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

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
Dialogue: 0,0:00:00.00,0:00:03.00,Default,,0,0,0,,{\\an8}Hello\\Nworld
Dialogue: 0,0:00:03.00,0:00:06.00,Default,,0,0,0,,Goodbye
"""


def make_test_mkv(output: Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        subtitle = Path(temp_dir) / "test.ass"
        subtitle.write_text(ASS_TEXT, encoding="utf-8")
        command = [
            ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=10:size=320x240:rate=10",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=10",
            "-i",
            str(subtitle),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2",
            "-c:v",
            "mpeg4",
            "-q:v",
            "5",
            "-c:a",
            "aac",
            "-c:s",
            "ass",
            "-metadata:s:s:0",
            "language=eng",
            "-metadata:s:s:0",
            "title=English",
            str(output),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            creationflags=_CREATIONFLAGS,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "生成测试 MKV 失败：\n" + result.stderr[-2000:]
            )
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("生成测试 MKV 失败：输出文件为空")
    return output


def probe_media_text(path: Path) -> str:
    result = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        creationflags=_CREATIONFLAGS,
    )
    return (result.stderr or "") + (result.stdout or "")


def parse_duration_seconds(text: str) -> float:
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text
    )
    if match is None:
        raise ValueError("无法读取视频时长")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
