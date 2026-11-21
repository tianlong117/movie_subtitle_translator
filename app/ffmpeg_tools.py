from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .config import app_dir

TEXT_CODECS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "webvtt",
    "text",
}
IMAGE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvb_subtitle",
    "xsub",
}

STREAM_RE = re.compile(
    r"^\s*Stream #0:(\d+)(?:\[[^\]]*\])?(?:\(([^)]*)\))?:\s*Subtitle:\s*(.+?)\s*$"
)
TITLE_RE = re.compile(r"^\s+title\s*:\s*(.+?)\s*$")

_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class SubtitleTrack:
    index: int
    language: str
    codec: str
    title: str = ""
    image_based: bool = False

    @property
    def label(self) -> str:
        parts = [f"#{self.index}"]
        if self.language:
            parts.append(self.language)
        parts.append(self.codec)
        if self.title:
            parts.append(self.title)
        if self.image_based or self.codec not in TEXT_CODECS:
            parts.append("不支持")
        return " | ".join(parts)


def is_text_track(track: SubtitleTrack) -> bool:
    return not track.image_based and track.codec in TEXT_CODECS


def text_tracks(tracks: list[SubtitleTrack]) -> list[SubtitleTrack]:
    return [track for track in tracks if is_text_track(track)]


def _is_english(language: str) -> bool:
    code = language.lower().split("-")[0].split("_")[0]
    return code in {"en", "eng"}


def find_text_track(tracks: list[SubtitleTrack]) -> SubtitleTrack | None:
    candidates = text_tracks(tracks)
    for track in candidates:
        if _is_english(track.language):
            return track
    return candidates[0] if candidates else None


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidates.append(base / "ffmpeg.exe")
        candidates.extend(sorted(base.glob("ffmpeg*.exe")))
    candidates.append(app_dir() / "ffmpeg.exe")

    try:
        import imageio_ffmpeg

        candidates.append(Path(imageio_ffmpeg.get_ffmpeg_exe()))
    except Exception:
        pass

    located = shutil.which("ffmpeg")
    if located:
        candidates.append(Path(located))

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "找不到 ffmpeg。请确认程序完整，或把 ffmpeg.exe 放到程序目录。"
    )


def stderr_tail(text: str, lines: int = 20) -> str:
    rows = (text or "").strip().splitlines()
    return "\n".join(rows[-lines:])


def _probe_input(path: str, timeout: int = 120) -> str:
    command = [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", path]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        detail = exc.stderr
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        tail = stderr_tail(detail or "")
        message = f"读取视频信息超时：{path}"
        if tail:
            message += f"\n{tail}"
        raise RuntimeError(message) from exc
    return (result.stderr or "") + (result.stdout or "")


def parse_stream_info(text: str) -> list[SubtitleTrack]:
    tracks: list[SubtitleTrack] = []
    last_track: SubtitleTrack | None = None

    for line in text.splitlines():
        match = STREAM_RE.match(line)
        if match:
            index = int(match.group(1))
            language = (match.group(2) or "").split(",")[0].strip().lower()
            codec = match.group(3).strip().split()[0].lower()
            last_track = SubtitleTrack(
                index=index,
                language=language,
                codec=codec,
                image_based=codec in IMAGE_CODECS,
            )
            tracks.append(last_track)
            continue

        if line.lstrip().startswith("Stream #"):
            last_track = None
            continue

        if last_track is not None:
            title_match = TITLE_RE.match(line)
            if title_match:
                last_track.title = title_match.group(1)

    return tracks


def probe_subtitle_tracks(
    video_path: str | Path, timeout: int = 120
) -> list[SubtitleTrack]:
    text = _probe_input(str(video_path), timeout=timeout)
    if "Stream #" not in text:
        raise ValueError(f"无法读取视频流信息：\n{stderr_tail(text)}")
    return parse_stream_info(text)


def build_extract_args(
    video_path: str | Path, track: SubtitleTrack, output_path: str | Path
) -> list[str]:
    codec_args = (
        ["-c:s", "copy"] if track.codec in {"ass", "ssa"} else ["-c:s", "srt"]
    )
    return [
        "-i",
        str(video_path),
        "-map",
        f"0:{track.index}",
        *codec_args,
        str(output_path),
    ]


def _run_ffmpeg(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        command = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *args]
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATIONFLAGS,
        )
    except subprocess.TimeoutExpired as exc:
        detail = exc.stderr
        if isinstance(detail, bytes):
            detail = detail.decode("utf-8", "replace")
        tail = stderr_tail(detail or "")
        message = f"ffmpeg 执行超时（{timeout} 秒）"
        if tail:
            message += f"\n{tail}"
        raise RuntimeError(message) from exc
    except OSError as exc:
        raise RuntimeError(f"无法启动 ffmpeg：{exc}") from exc


def extract_subtitle(
    video_path: str | Path,
    track: SubtitleTrack,
    output_path: str | Path,
    timeout: int = 120,
) -> Path:
    output = Path(output_path)
    if Path(video_path).resolve() == output.resolve():
        raise ValueError("字幕输出路径不能与视频相同")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    try:
        result = _run_ffmpeg(
            build_extract_args(video_path, track, output), timeout=timeout
        )
        if (
            result.returncode != 0
            or not output.exists()
            or output.stat().st_size == 0
        ):
            raise RuntimeError(
                f"字幕提取失败：\n{stderr_tail(result.stderr)}"
            )
        return output
    except Exception:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def build_mux_args(
    video_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    subtitle_count: int,
    language_code: str,
    title: str,
) -> list[str]:
    if Path(video_path).suffix.lower() != ".mkv":
        raise ValueError("仅支持 MKV 视频输入")
    if Path(output_path).suffix.lower() != ".mkv":
        raise ValueError("输出文件必须是 .mkv")
    if subtitle_count < 0:
        raise ValueError("subtitle_count 不能为负数")
    # 新字幕轨放在字幕轨首位：部分播放器（如 QQ 影音）只自动加载第一条字幕，
    # 且切换到后续 ASS 轨时可能初始化失败。
    map_args = ["-map", "0", "-map", "1"]
    if subtitle_count:
        map_args += ["-map", "-0:s", "-map", "0:s"]
    index = "-metadata:s:s:0"
    return [
        "-i",
        str(video_path),
        "-i",
        str(subtitle_path),
        *map_args,
        "-c",
        "copy",
        "-map_chapters",
        "0",
        index,
        f"language={language_code}",
        index,
        f"title={title}",
        "-disposition:s:0",
        "default",
        str(output_path),
    ]


def mux_subtitle(
    video_path: str | Path,
    subtitle_path: str | Path,
    output_path: str | Path,
    subtitle_count: int,
    language_code: str,
    title: str,
    timeout: int = 600,
) -> Path:
    video = Path(video_path)
    output = Path(output_path)
    if video.suffix.lower() != ".mkv":
        raise ValueError("仅支持 MKV 视频输入")
    if output.suffix.lower() != ".mkv":
        raise ValueError("输出文件必须是 .mkv")
    if subtitle_count < 0:
        raise ValueError("subtitle_count 不能为负数")
    if video.resolve() == output.resolve():
        raise ValueError("输出文件不能覆盖原视频")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f"{output.stem}.tmp-",
        suffix=output.suffix,
        dir=str(output.parent),
    )
    os.close(fd)
    temp = Path(temp_name)

    try:
        result = _run_ffmpeg(
            build_mux_args(
                video,
                subtitle_path,
                temp,
                subtitle_count,
                language_code,
                title,
            ),
            timeout=timeout,
        )
        if (
            result.returncode != 0
            or not temp.exists()
            or temp.stat().st_size == 0
        ):
            raise RuntimeError(
                f"字幕封装失败：\n{stderr_tail(result.stderr)}"
            )
        try:
            temp.replace(output)
        except OSError as exc:
            raise RuntimeError(
                f"无法写入输出文件：{output}\n{exc}"
            ) from exc
        return output
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
