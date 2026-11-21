from __future__ import annotations

import logging
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

from .config import (
    LANGUAGE_ISO639_2,
    SOURCE_LANGUAGES,
    TARGET_LANGUAGES,
    TARGET_TRACK_TITLE,
    AppConfig,
    LLMSettings,
    app_dir,
    is_llm_configured,
    language_label,
    load_config,
    save_config,
    target_suffix,
)
from .ffmpeg_tools import (
    SubtitleTrack,
    extract_subtitle,
    find_text_track,
    is_text_track,
    mux_subtitle,
    probe_subtitle_tracks,
)
from .language_detect import build_sample, detect_language
from .llm_client import LLMClient
from .subtitle_io import (
    SubtitleDocument,
    load_subtitle,
    save_subtitle,
)
from .translator import (
    TranslationCancelled,
    TranslationError,
    TranslationJob,
)

logger = logging.getLogger(__name__)

COPYRIGHT_TEXT = (
    "软件开发者：天龙\n\n"
    "版权说明\n\n"
    "本软件为自由软件，开发者放弃对软件本身的版权主张，"
    "任何人可自由使用、修改和分发。\n\n"
    "本软件仅提供字幕翻译工具，不提供、不存储、不分发任何字幕或影视内容。"
    "您翻译的字幕仅供个人学习、研究或欣赏，不得上传公开网络、传播分享或用于商业用途。"
    "请确保您的翻译行为符合著作权法规定，因使用本软件产生的版权责任由用户自行承担。"
)

SUBTITLE_SUFFIXES = {".srt", ".ass", ".ssa"}


def default_output_path(video_path: Path, target_lang: str) -> Path:
    video_path = Path(video_path)
    return video_path.with_name(
        f"{video_path.stem}.{target_suffix(target_lang)}.mkv"
    )


def center_dialog_position(
    parent_x: int,
    parent_y: int,
    parent_width: int,
    parent_height: int,
    width: int,
    height: int,
) -> tuple[int, int]:
    x = parent_x + max(0, (parent_width - width) // 2)
    y = parent_y + max(0, (parent_height - height) // 2)
    return x, y


def expected_primary_suffix(source: Path) -> str:
    source = Path(source)
    if source.suffix.lower() in {".ass", ".ssa"}:
        return ".ass"
    return ".srt"


def default_subtitle_output_path(
    source: Path, target_lang: str
) -> Path:
    source = Path(source)
    return source.with_name(
        f"{source.stem}.{target_suffix(target_lang)}"
        f"{expected_primary_suffix(source)}"
    )


def needs_srt_compat(source: Path) -> bool:
    return Path(source).suffix.lower() in {".ass", ".ssa"}


def srt_compat_path(primary: Path) -> Path:
    return Path(primary).with_suffix(".srt")


def save_subtitle_outputs(
    document: SubtitleDocument, source: Path, output: Path
) -> tuple[list[Path], str]:
    output = Path(output)
    save_subtitle(document, output)
    outputs = [output]
    warning = ""
    if needs_srt_compat(source):
        srt_path = srt_compat_path(output)
        if srt_path != output:
            try:
                save_subtitle(document, srt_path)
                outputs.append(srt_path)
            except Exception as exc:
                warning = f"SRT 兼容版生成失败：{exc}"
    return outputs, warning


def make_partial_path(
    video: Path, suffix: str, ext: str, output_parent: Path
) -> Path:
    return output_parent / (
        f"{video.stem}.{suffix}.partial-"
        f"{os.getpid()}-{time.time_ns()}{ext}"
    )


def unique_sidecar_path(path: Path) -> Path:
    path = Path(path)
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(
            f"{path.stem} ({index}){path.suffix}"
        )
        if not candidate.exists():
            return candidate
    raise OSError("无法生成不冲突的字幕文件名")


def _copy_sidecar(
    source: Path, filename: str, directories: list[Path]
) -> Path:
    last_error: OSError | None = None
    seen: set[str] = set()
    for directory in directories:
        try:
            key = str(directory.resolve())
        except OSError:
            key = str(directory)
        if key in seen:
            continue
        seen.add(key)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            target = unique_sidecar_path(directory / filename)
            shutil.copyfile(source, target)
            return target
        except OSError as exc:
            last_error = exc
    raise OSError(f"无法保存字幕文件：{last_error}")


def cancel_message(partial_path: Path | None) -> str:
    if partial_path:
        return (
            "翻译已取消。\n已翻译部分保留在：\n"
            f"{partial_path}"
        )
    return "翻译已取消（已翻译部分保存失败，详见日志）。"


def setup_logging() -> None:
    root = logging.getLogger()
    if any(
        isinstance(handler, RotatingFileHandler)
        for handler in root.handlers
    ):
        return
    root.setLevel(logging.INFO)
    try:
        handler = RotatingFileHandler(
            app_dir() / "translator.log",
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
    except OSError:
        return
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    )
    root.addHandler(handler)


class SubtitleTranslatorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("电影字幕翻译器")
        self.root.geometry("880x660")
        self.root.minsize(780, 580)

        self.config: AppConfig = load_config()
        self.video_path: Path | None = None
        self.tracks: list[SubtitleTrack] = []
        self.track_by_label: dict[str, SubtitleTrack] = {}
        self.external_sub: Path | None = None
        self.external_sub_label: str | None = None
        self.probe_ok = False
        self.probing = False
        self.probe_thread: threading.Thread | None = None
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.message_queue: queue.Queue[tuple] = queue.Queue()
        self.output_is_auto = True
        self.detect_serial = 0
        self.detecting = False

        self._build_ui()
        self._apply_config_to_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_messages)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="视频文件").grid(
            row=0, column=0, sticky="w", pady=4
        )
        self.video_var = tk.StringVar()
        self.video_entry = ttk.Entry(
            frame, textvariable=self.video_var, state="readonly"
        )
        self.video_entry.grid(row=0, column=1, sticky="ew", padx=6)
        self.video_btn = ttk.Button(
            frame, text="浏览...", command=self._choose_video
        )
        self.video_btn.grid(row=0, column=2)

        ttk.Label(frame, text="字幕轨").grid(
            row=1, column=0, sticky="w", pady=4
        )
        self.track_var = tk.StringVar()
        self.track_combo = ttk.Combobox(
            frame, textvariable=self.track_var, state="readonly"
        )
        self.track_combo.grid(row=1, column=1, sticky="ew", padx=6)
        self.track_combo.bind(
            "<<ComboboxSelected>>", self._on_track_selected
        )
        self.external_btn = ttk.Button(
            frame,
            text="选外挂字幕...",
            command=self._choose_external_subtitle,
        )
        self.external_btn.grid(row=1, column=2)

        self.hint_label = ttk.Label(
            frame,
            text="未选择视频文件时，仅选择字幕文件即可单独翻译字幕。",
            foreground="#666666",
        )
        self.hint_label.grid(
            row=2,
            column=0,
            columnspan=3,
            sticky="w",
            padx=6,
            pady=(0, 4),
        )

        ttk.Label(frame, text="语言").grid(
            row=3, column=0, sticky="w", pady=4
        )
        lang_frame = ttk.Frame(frame)
        lang_frame.grid(
            row=3, column=1, columnspan=2, sticky="ew", padx=6
        )
        ttk.Label(lang_frame, text="源语言").pack(side="left")
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(
            lang_frame,
            textvariable=self.source_var,
            values=[label for _, label in SOURCE_LANGUAGES],
            state="readonly",
            width=12,
        )
        self.source_combo.pack(side="left", padx=(4, 4))
        self.source_combo.bind(
            "<<ComboboxSelected>>", self._on_source_changed
        )
        self.detect_lang_btn = ttk.Button(
            lang_frame,
            text="AI 识别",
            command=self._detect_source_language,
        )
        self.detect_lang_btn.pack(side="left", padx=(0, 16))
        ttk.Label(lang_frame, text="目标语言").pack(side="left")
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(
            lang_frame,
            textvariable=self.target_var,
            values=[label for _, label in TARGET_LANGUAGES],
            state="readonly",
            width=12,
        )
        self.target_combo.pack(side="left", padx=4)
        self.target_combo.bind(
            "<<ComboboxSelected>>", self._on_target_changed
        )

        ttk.Label(frame, text="输出").grid(
            row=4, column=0, sticky="w", pady=4
        )
        self.output_var = tk.StringVar()
        self.output_entry = ttk.Entry(
            frame, textvariable=self.output_var
        )
        self.output_entry.grid(row=4, column=1, sticky="ew", padx=6)
        self.output_entry.bind("<KeyRelease>", self._on_output_edited)
        self.output_btn = ttk.Button(
            frame, text="另存为...", command=self._choose_output
        )
        self.output_btn.grid(row=4, column=2)

        self.export_var = tk.BooleanVar(value=True)
        self.export_check = ttk.Checkbutton(
            frame,
            text="同时导出翻译后的字幕（保留原格式）",
            variable=self.export_var,
        )
        self.export_check.grid(
            row=5, column=1, columnspan=2, sticky="w", padx=6, pady=4
        )

        action_frame = ttk.Frame(frame)
        action_frame.grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=(10, 4)
        )
        self.settings_btn = ttk.Button(
            action_frame, text="LLM 设置...", command=self._open_settings
        )
        self.settings_btn.pack(side="left")
        self.cancel_btn = ttk.Button(
            action_frame,
            text="取消",
            command=self._cancel,
            state="disabled",
        )
        self.cancel_btn.pack(side="right")
        self.start_btn = ttk.Button(
            action_frame, text="开始翻译", command=self._start
        )
        self.start_btn.pack(side="right", padx=6)

        self.progress_var = tk.DoubleVar(value=0)
        self.progress_bar = ttk.Progressbar(
            frame, variable=self.progress_var, maximum=100
        )
        self.progress_bar.grid(
            row=7, column=0, columnspan=3, sticky="ew", pady=(8, 4)
        )
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(frame, textvariable=self.status_var).grid(
            row=8, column=0, columnspan=3, sticky="w"
        )

        log_frame = ttk.LabelFrame(frame, text="日志", padding=4)
        log_frame.grid(
            row=9,
            column=0,
            columnspan=3,
            sticky="nsew",
            pady=(8, 0),
        )
        frame.rowconfigure(9, weight=1)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame, height=12, state="disabled", wrap="word"
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.log_text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.link_font = tkfont.nametofont("TkDefaultFont").copy()
        self.link_font.configure(underline=True, size=9)
        self.copyright_label = tk.Label(
            frame,
            text="版权说明",
            fg="#1a73e8",
            cursor="hand2",
            font=self.link_font,
        )
        self.copyright_label.grid(
            row=10, column=0, columnspan=3, pady=(6, 0)
        )
        self.copyright_label.bind(
            "<Button-1>", lambda event: self._open_copyright_dialog()
        )

        self._set_inputs_enabled(True)

    def _apply_config_to_ui(self) -> None:
        self.source_var.set(language_label(self.config.source_lang))
        self.target_var.set(language_label(self.config.target_lang))
        self.export_var.set(self.config.export_subtitle)
        self.status_var.set("就绪")

    def _source_code(self) -> str:
        label = self.source_var.get()
        for code, name in SOURCE_LANGUAGES:
            if name == label:
                return code
        return "en"

    def _target_code(self) -> str:
        label = self.target_var.get()
        for code, name in TARGET_LANGUAGES:
            if name == label:
                return code
        return "zh-Hans"

    def _choose_video(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 MKV 电影",
            filetypes=[("MKV 视频", "*.mkv"), ("所有文件", "*.*")],
        )
        if path:
            self._load_video(Path(path))

    def _load_video(self, path: Path) -> None:
        if path.suffix.lower() != ".mkv":
            messagebox.showerror("错误", "仅支持 .mkv 视频文件")
            return
        self.detect_serial += 1
        self.detecting = False
        self._refresh_detect_button()
        self.video_path = path
        self.video_var.set(str(path))
        self.external_sub = None
        self.external_sub_label = None
        self.tracks = []
        self.track_by_label.clear()
        self.track_combo.configure(values=[], state="disabled")
        self.track_var.set("")
        self.probe_ok = False
        self.probing = True
        self._set_output_auto()
        self._log(f"已选择：{path}")
        self.status_var.set("正在探测字幕轨...")
        self._set_inputs_enabled(False)
        self.probe_thread = threading.Thread(
            target=self._probe_worker, args=(path,), daemon=True
        )
        self.probe_thread.start()

    def _probe_worker(self, path: Path) -> None:
        try:
            tracks = probe_subtitle_tracks(path)
            self._post("probe_ok", path, tracks)
        except Exception as exc:
            logger.exception("探测字幕轨失败")
            self._post("probe_error", path, str(exc))

    def _finish_probe(self, tracks: list[SubtitleTrack]) -> None:
        self.tracks = tracks
        self.probe_ok = True
        self._log(f"发现 {len(tracks)} 条字幕轨")
        labels = []
        for track in tracks:
            labels.append(track.label)
            self.track_by_label[track.label] = track

        if not labels:
            self.track_combo.configure(values=[], state="disabled")
            self._log("没有字幕轨，请点“选外挂字幕...”选择字幕文件")
            messagebox.showinfo(
                "没有字幕轨",
                "该 MKV 没有字幕轨。请点击“选外挂字幕...”选择字幕文件。",
            )
            return

        self.track_combo.configure(values=labels, state="readonly")
        default = find_text_track(tracks)
        if default is None:
            self.track_var.set("")
            self._log("只有图形字幕轨，请选择外挂字幕")
            messagebox.showinfo(
                "只有图形字幕",
                "该文件的字幕是 PGS/DVD 图形字幕，第一版不支持。\n"
                "请点击“选外挂字幕...”选择文本字幕文件。",
            )
            return

        self.track_var.set(default.label)
        self._on_track_selected()

    def _on_track_selected(self, event=None) -> None:
        label = self.track_var.get()
        if label in self.track_by_label:
            if self.detecting:
                self.detect_serial += 1
                self.detecting = False
                self._refresh_detect_button()
            self.external_sub = None
            self.external_sub_label = None
            self.track_combo.configure(
                values=list(self.track_by_label.keys())
            )

    def _on_output_edited(self, event=None) -> None:
        self.output_is_auto = False

    def _set_output_auto(self) -> None:
        self.output_is_auto = True
        if self.video_path is not None:
            self.output_var.set(
                str(
                    default_output_path(
                        self.video_path, self._target_code()
                    )
                )
            )
            return
        if self.external_sub is not None:
            self.output_var.set(
                str(
                    default_subtitle_output_path(
                        self.external_sub, self._target_code()
                    )
                )
            )
            return
        self.output_var.set("")

    def _on_target_changed(self, event=None) -> None:
        if self.output_is_auto:
            self._set_output_auto()

    def _on_source_changed(self, event=None) -> None:
        if not self.detecting:
            return
        self.detect_serial += 1
        self.detecting = False
        self._refresh_detect_button()
        if self.video_path is None and self.external_sub is not None:
            self.status_var.set("仅字幕模式")
        else:
            self.status_var.set("就绪")

    def _choose_external_subtitle(self) -> None:
        path = filedialog.askopenfilename(
            title="选择外挂字幕",
            filetypes=[
                ("字幕文件", "*.srt *.ass *.ssa"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        external = Path(path)
        if external.suffix.lower() not in SUBTITLE_SUFFIXES:
            messagebox.showerror(
                "错误", "字幕仅支持 .srt、.ass、.ssa 文件"
            )
            return
        if not external.is_file():
            messagebox.showerror("错误", "所选字幕文件不存在")
            return
        self.detect_serial += 1
        self.detecting = False
        self._refresh_detect_button()
        self.external_sub = external
        label = f"外挂字幕：{external.name}"
        self.external_sub_label = label
        if self.video_path is None:
            self.tracks = []
            self.track_by_label.clear()
            self.probe_ok = False
            self.track_combo.configure(
                values=[label], state="readonly"
            )
            self.track_var.set(label)
            self._set_output_auto()
            self._sync_mode_widgets()
            self._log(f"未选择视频，仅翻译字幕：{external}")
            self.status_var.set("仅字幕模式")
        else:
            self.track_combo.configure(
                values=list(self.track_by_label.keys()) + [label],
                state="readonly",
            )
            self.track_var.set(label)
            self._log(f"使用外挂字幕：{self.external_sub}")

    def _refresh_detect_button(self) -> None:
        if self.detecting:
            self.detect_lang_btn.configure(
                state="disabled", text="识别中..."
            )
        else:
            self.detect_lang_btn.configure(
                state="normal", text="AI 识别"
            )

    def _detect_source_language(self) -> None:
        if self.detecting:
            return
        if not is_llm_configured(self.config.llm):
            messagebox.showerror(
                "LLM 未配置",
                "请先在“LLM 设置...”中配置 API 地址、Key 和模型",
            )
            return
        track = self._selected_track()
        if self.external_sub is not None:
            target = self._detect_language_worker
            args: tuple = (self.external_sub,)
        elif (
            self.video_path is not None
            and track is not None
            and is_text_track(track)
        ):
            target = self._detect_track_language_worker
            args = (self.video_path, track)
        else:
            messagebox.showinfo(
                "提示",
                "请先选择外挂字幕或文本字幕轨",
            )
            return
        self.detect_serial += 1
        serial = self.detect_serial
        self.detecting = True
        self._refresh_detect_button()
        self.status_var.set("正在识别源语言...")
        settings = self.config.llm
        threading.Thread(
            target=target,
            args=args + (serial, settings),
            daemon=True,
        ).start()

    def _detect_track_language_worker(
        self,
        video: Path,
        track: SubtitleTrack,
        serial: int,
        settings: LLMSettings,
    ) -> None:
        temp_dir = Path(
            tempfile.mkdtemp(prefix="subtitle_detect_")
        )
        try:
            source_ext = (
                f".{track.codec}"
                if track.codec in {"ass", "ssa"}
                else ".srt"
            )
            source = temp_dir / f"detect{source_ext}"
            extract_subtitle(video, track, source)
            self._detect_language_worker(
                source, serial, settings
            )
        except Exception as exc:
            logger.exception("源语言识别失败")
            self._post("detect_failed", serial, str(exc))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def _detect_language_worker(
        self, subtitle: Path, serial: int, settings: LLMSettings
    ) -> None:
        try:
            document = load_subtitle(subtitle)
            sample = build_sample(document)
            if not sample.strip():
                self._post(
                    "detect_failed", serial, "字幕没有可识别文本"
                )
                return
            code = detect_language(LLMClient(settings), sample)
            if code is None:
                self._post(
                    "detect_failed",
                    serial,
                    "模型未返回有效语言代码",
                )
                return
            self._post("detected_lang", serial, code)
        except Exception as exc:
            logger.exception("源语言识别失败")
            self._post("detect_failed", serial, str(exc))

    def _choose_output(self) -> None:
        initial = self.output_var.get()
        if self.video_path is not None:
            options = {
                "title": "选择输出 MKV",
                "defaultextension": ".mkv",
                "filetypes": [("MKV 视频", "*.mkv")],
            }
        elif self.external_sub is not None:
            ext = expected_primary_suffix(self.external_sub)
            options = {
                "title": "选择输出字幕",
                "defaultextension": ext,
                "filetypes": [("字幕文件", f"*{ext}")],
            }
        else:
            options = {
                "title": "选择输出文件",
                "defaultextension": ".srt",
                "filetypes": [("字幕文件", "*.srt *.ass *.ssa")],
            }
        if initial:
            options["initialfile"] = Path(initial).name
            options["initialdir"] = str(Path(initial).parent)
        path = filedialog.asksaveasfilename(**options)
        if path:
            self.output_var.set(path)
            self.output_is_auto = False

    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("LLM 设置")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        settings = self.config.llm
        variables = {
            "base_url": tk.StringVar(value=settings.base_url),
            "api_key": tk.StringVar(value=settings.api_key),
            "model": tk.StringVar(value=settings.model),
            "temperature": tk.StringVar(value=str(settings.temperature)),
            "batch_size": tk.StringVar(value=str(settings.batch_size)),
            "concurrency": tk.StringVar(value=str(settings.concurrency)),
            "timeout": tk.StringVar(value=str(settings.timeout)),
        }
        rows = [
            ("API 地址", "base_url", False),
            ("API Key", "api_key", True),
            ("模型", "model", False),
            ("温度 (0-2)", "temperature", False),
            ("每批条数 (8-80)", "batch_size", False),
            ("并发数 (1-8)", "concurrency", False),
            ("超时秒数 (30-600)", "timeout", False),
        ]
        for row, (label, key, secret) in enumerate(rows):
            ttk.Label(frame, text=label).grid(
                row=row, column=0, sticky="w", pady=3
            )
            ttk.Entry(
                frame,
                textvariable=variables[key],
                width=46,
                show="*" if secret else "",
            ).grid(row=row, column=1, sticky="ew", padx=6, pady=3)

        ttk.Label(
            frame,
            text="API Key 明文保存在 exe 同目录的配置文件中。",
            foreground="#666666",
        ).grid(
            row=len(rows),
            column=0,
            columnspan=2,
            sticky="w",
            pady=(4, 8),
        )

        button_frame = ttk.Frame(frame)
        button_frame.grid(
            row=len(rows) + 1,
            column=0,
            columnspan=2,
            sticky="ew",
        )
        test_btn = ttk.Button(button_frame, text="测试连接")
        test_btn.pack(side="left")
        save_btn = ttk.Button(button_frame, text="保存")
        save_btn.pack(side="right")
        ttk.Button(
            button_frame, text="取消", command=dialog.destroy
        ).pack(side="right", padx=6)

        def read_settings() -> LLMSettings:
            base_url = variables["base_url"].get().strip()
            if not base_url.startswith(("http://", "https://")):
                raise ValueError(
                    "API 地址必须以 http:// 或 https:// 开头"
                )
            model = variables["model"].get().strip()
            if not model:
                raise ValueError("模型不能为空")
            try:
                temperature = float(variables["temperature"].get())
            except ValueError as exc:
                raise ValueError("温度必须是数字") from exc
            if not 0 <= temperature <= 2:
                raise ValueError("温度必须在 0-2 之间")

            def read_int(key: str, name: str, low: int, high: int) -> int:
                try:
                    value = int(variables[key].get())
                except ValueError as exc:
                    raise ValueError(f"{name}必须是整数") from exc
                if not low <= value <= high:
                    raise ValueError(f"{name}必须在 {low}-{high} 之间")
                return value

            return LLMSettings(
                base_url=base_url,
                api_key=variables["api_key"].get().strip(),
                model=model,
                temperature=temperature,
                batch_size=read_int("batch_size", "每批条数", 8, 80),
                concurrency=read_int("concurrency", "并发数", 1, 8),
                timeout=read_int("timeout", "超时秒数", 30, 600),
            )

        def save() -> None:
            try:
                new_settings = read_settings()
            except ValueError as exc:
                messagebox.showerror("参数错误", str(exc), parent=dialog)
                return
            new_config = AppConfig(
                llm=new_settings,
                source_lang=self._source_code(),
                target_lang=self._target_code(),
                export_subtitle=bool(self.export_var.get()),
            )
            try:
                save_config(new_config)
            except OSError as exc:
                messagebox.showerror("保存失败", str(exc), parent=dialog)
                return
            self.config = new_config
            self._log("LLM 设置已保存")
            dialog.destroy()

        def test_connection() -> None:
            try:
                candidate = read_settings()
            except ValueError as exc:
                messagebox.showerror("参数错误", str(exc), parent=dialog)
                return
            candidate.timeout = min(candidate.timeout, 15)
            test_btn.configure(state="disabled", text="测试中...")
            result_queue: queue.Queue = queue.Queue()

            def worker() -> None:
                try:
                    LLMClient(candidate).test_connection()
                    result_queue.put((True, "连接成功"))
                except Exception as exc:
                    result_queue.put((False, str(exc)))

            threading.Thread(target=worker, daemon=True).start()

            def poll() -> None:
                try:
                    ok, message = result_queue.get_nowait()
                except queue.Empty:
                    dialog.after(100, poll)
                    return
                test_btn.configure(state="normal", text="测试连接")
                if ok:
                    messagebox.showinfo(
                        "测试连接", message, parent=dialog
                    )
                else:
                    messagebox.showerror(
                        "测试连接失败", message, parent=dialog
                    )

            dialog.after(100, poll)

        test_btn.configure(command=test_connection)
        save_btn.configure(command=save)

    def _open_copyright_dialog(self) -> tk.Toplevel:
        dialog = tk.Toplevel(self.root)
        dialog.title("版权说明")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)

        text_frame = ttk.Frame(frame)
        text_frame.pack(fill="both", expand=True)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        text = tk.Text(
            text_frame,
            width=72,
            height=16,
            wrap="word",
            relief="flat",
            borderwidth=0,
        )
        text.insert("1.0", COPYRIGHT_TEXT)
        text.configure(state="disabled")
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            text_frame, orient="vertical", command=text.yview
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)

        ttk.Button(
            frame, text="关闭", command=dialog.destroy
        ).pack(pady=(12, 0))

        dialog.update_idletasks()
        width = max(dialog.winfo_reqwidth(), 1)
        height = max(dialog.winfo_reqheight(), 1)
        x, y = center_dialog_position(
            self.root.winfo_rootx(),
            self.root.winfo_rooty(),
            self.root.winfo_width(),
            self.root.winfo_height(),
            width,
            height,
        )
        dialog.geometry(f"+{x}+{y}")
        return dialog

    def _selected_track(self) -> SubtitleTrack | None:
        return self.track_by_label.get(self.track_var.get())

    def _start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        if self.probing:
            messagebox.showinfo("请稍候", "正在探测字幕轨，请稍候")
            return
        if self.detecting:
            messagebox.showinfo(
                "请稍候", "正在识别源语言，请稍候"
            )
            return

        source = self._source_code()
        target = self._target_code()
        standalone = (
            self.video_path is None
            and self.external_sub is not None
        )

        if self.video_path is None and self.external_sub is None:
            messagebox.showerror(
                "错误",
                "请先选择 MKV 视频，或点击“选外挂字幕...”选择字幕文件。",
            )
            return

        track: SubtitleTrack | None = None
        if not standalone:
            if not self.probe_ok:
                messagebox.showerror(
                    "错误", "字幕轨信息不可用，请重新选择视频"
                )
                return
            if self.video_path is None or not self.video_path.exists():
                messagebox.showerror(
                    "错误", "请先选择 MKV 视频文件"
                )
                return
            if self.video_path.suffix.lower() != ".mkv":
                messagebox.showerror(
                    "错误", "仅支持 .mkv 视频文件"
                )
                return

            track = self._selected_track()
            if self.external_sub is None and track is None:
                messagebox.showerror(
                    "错误", "请选择字幕轨或外挂字幕"
                )
                return
            if (
                self.external_sub is None
                and track is not None
                and not is_text_track(track)
            ):
                messagebox.showerror(
                    "错误",
                    "所选字幕轨不是文本字幕，请选择外挂字幕",
                )
                return
            if self.external_sub is not None:
                if (
                    self.external_sub.suffix.lower()
                    not in SUBTITLE_SUFFIXES
                ):
                    messagebox.showerror(
                        "错误",
                        "外挂字幕仅支持 .srt、.ass、.ssa 文件",
                    )
                    return
                if not self.external_sub.is_file():
                    messagebox.showerror(
                        "错误", "所选字幕文件不存在"
                    )
                    return
        else:
            assert self.external_sub is not None
            if (
                self.external_sub.suffix.lower()
                not in SUBTITLE_SUFFIXES
            ):
                messagebox.showerror(
                    "错误", "字幕仅支持 .srt、.ass、.ssa 文件"
                )
                return
            if not self.external_sub.is_file():
                messagebox.showerror(
                    "错误", "所选字幕文件不存在"
                )
                return

        if not is_llm_configured(self.config.llm):
            messagebox.showerror(
                "LLM 未配置",
                "请先在“LLM 设置...”中填写 API 地址、Key 和模型",
            )
            self._open_settings()
            return

        output_text = self.output_var.get().strip()
        if not output_text:
            messagebox.showerror("错误", "请选择输出文件")
            return
        output = Path(output_text)

        if standalone:
            assert self.external_sub is not None
            expected = expected_primary_suffix(self.external_sub)
        else:
            expected = ".mkv"

        if output.suffix.lower() != expected:
            if standalone:
                assert self.external_sub is not None
                messagebox.showerror(
                    "错误",
                    f"当前源字幕是 "
                    f"{self.external_sub.suffix.upper()}，"
                    f"主输出必须是 {expected} 文件。",
                )
            else:
                messagebox.showerror(
                    "错误", "输出文件必须是 .mkv"
                )
            return

        if standalone:
            assert self.external_sub is not None
            if (
                output.resolve()
                == self.external_sub.resolve()
            ):
                messagebox.showerror(
                    "错误", "输出文件不能覆盖源字幕"
                )
                return
        else:
            assert self.video_path is not None
            if output.resolve() == self.video_path.resolve():
                messagebox.showerror(
                    "错误", "输出文件不能覆盖原视频"
                )
                return

        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=output.parent):
                pass
        except OSError as exc:
            messagebox.showerror(
                "错误", f"输出目录不可写：{exc}"
            )
            return

        if standalone:
            assert self.external_sub is not None
            candidate_outputs = [output]
            if needs_srt_compat(self.external_sub):
                candidate_outputs.append(
                    srt_compat_path(output)
                )
            existing = [
                path for path in candidate_outputs if path.exists()
            ]
            if existing and not messagebox.askyesno(
                "文件已存在",
                "以下文件已存在，是否覆盖？\n"
                + "\n".join(str(path) for path in existing),
            ):
                return
        else:
            if output.exists() and not messagebox.askyesno(
                "文件已存在",
                f"{output.name} 已存在，是否覆盖？",
            ):
                return

        self.config.source_lang = source
        self.config.target_lang = target
        self.config.export_subtitle = bool(
            self.export_var.get()
        )
        try:
            save_config(self.config)
        except OSError as exc:
            self._log(f"保存配置失败：{exc}")

        self.cancel_event = threading.Event()
        self._clear_log()
        self.progress_var.set(0)
        self.status_var.set("准备中...")
        self._set_inputs_enabled(False)
        self.cancel_btn.configure(state="normal")

        if standalone:
            assert self.external_sub is not None
            self.worker = threading.Thread(
                target=self._run_subtitle_task,
                args=(
                    self.external_sub,
                    output,
                    source,
                    target,
                ),
                daemon=True,
            )
        else:
            assert self.video_path is not None
            self.worker = threading.Thread(
                target=self._run_task,
                args=(
                    self.video_path,
                    track,
                    self.external_sub,
                    output,
                    source,
                    target,
                    len(self.tracks),
                    bool(self.export_var.get()),
                ),
                daemon=True,
            )
        self.worker.start()

    def _run_task(
        self,
        video: Path,
        track: SubtitleTrack | None,
        external_sub: Path | None,
        output: Path,
        source_lang: str,
        target_lang: str,
        subtitle_count: int,
        export_subtitle: bool,
    ) -> None:
        temp_dir = Path(
            tempfile.mkdtemp(prefix="subtitle_translator_")
        )
        try:
            suffix = target_suffix(target_lang)

            if external_sub is not None:
                self._post("log", f"读取外挂字幕：{external_sub}")
                document = load_subtitle(external_sub)
            else:
                assert track is not None
                source_ext = (
                    f".{track.codec}"
                    if track.codec in {"ass", "ssa"}
                    else ".srt"
                )
                source_path = temp_dir / f"source{source_ext}"
                self._post(
                    "log",
                    f"提取字幕轨 #{track.index}（{track.codec}）",
                )
                extract_subtitle(video, track, source_path)
                document = load_subtitle(source_path)

            ext = document.suffix
            partial_path = make_partial_path(
                video, suffix, ext, output.parent
            )
            self._post(
                "log",
                f"共 {len(document.translatable_indices())} 条可翻译对白，开始调用 LLM",
            )

            client = LLMClient(self.config.llm)
            job = TranslationJob(
                document=document,
                client=client,
                batch_size=self.config.llm.batch_size,
                concurrency=self.config.llm.concurrency,
                source_lang=source_lang,
                target_lang=target_lang,
                movie_name=video.stem,
                partial_path=partial_path,
                progress_cb=self._on_progress,
                cancel_event=self.cancel_event,
            )
            report = job.run()

            if self.cancel_event.is_set():
                saved = False
                try:
                    save_subtitle(document, partial_path)
                    saved = True
                except Exception:
                    logger.exception("保存已翻译字幕失败")
                raise TranslationCancelled(
                    partial_path if saved else None
                )

            self._post(
                "log",
                f"翻译完成：{report.total_events} 条，唯一文本 "
                f"{report.unique_texts} 条，批次 {report.batches}",
            )

            translated_path = temp_dir / f"translated{ext}"
            save_subtitle(document, translated_path)

            sidecar_name = f"{video.stem}.{suffix}{ext}"
            sidecar_dirs = [
                output.parent,
                video.parent,
                Path(tempfile.gettempdir()),
            ]

            self._post("phase", "muxing")
            self._post("log", "正在封装 MKV（视频/音频不重新编码）...")
            language_code = LANGUAGE_ISO639_2.get(target_lang, "und")
            title = TARGET_TRACK_TITLE.get(target_lang, target_lang)
            try:
                mux_subtitle(
                    video,
                    translated_path,
                    output,
                    subtitle_count,
                    language_code,
                    title,
                )
            except Exception as exc:
                saved = False
                fallback: Path | None = None
                try:
                    fallback = _copy_sidecar(
                        translated_path, sidecar_name, sidecar_dirs
                    )
                    saved = True
                    self._post(
                        "log", f"封装失败，已保留字幕文件：{fallback}"
                    )
                except OSError:
                    logger.exception("保留翻译字幕失败")
                if saved and fallback is not None:
                    raise RuntimeError(
                        f"封装失败，翻译字幕已保留在：{fallback}\n{exc}"
                    ) from exc
                raise RuntimeError(
                    f"封装失败，且无法保留翻译字幕：{exc}"
                ) from exc

            export_warning = ""
            if export_subtitle:
                try:
                    export_path = _copy_sidecar(
                        translated_path, sidecar_name, sidecar_dirs
                    )
                    self._post(
                        "log", f"已导出翻译字幕：{export_path}"
                    )
                except OSError as exc:
                    export_warning = f"导出字幕失败：{exc}"
                    logger.exception("导出翻译字幕失败")
                    self._post("log", export_warning)

            try:
                partial_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("删除 partial 字幕失败：%s", exc)
            self._post("progress", 100, 100, "完成")
            self._post("done", [output], export_warning)
        except TranslationCancelled as exc:
            self._post(
                "error", "已取消", cancel_message(exc.partial_path)
            )
        except TranslationError as exc:
            extra = (
                f"\n已翻译部分保留在：\n{exc.partial_path}"
                if exc.partial_path
                else ""
            )
            self._post("error", "翻译失败", f"{exc}{extra}")
        except Exception as exc:
            logger.exception("任务失败")
            self._post("error", "任务失败", str(exc))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._post("finished")

    def _run_subtitle_task(
        self,
        subtitle: Path,
        output: Path,
        source_lang: str,
        target_lang: str,
    ) -> None:
        try:
            self._post("log", f"读取字幕：{subtitle}")
            document = load_subtitle(subtitle)
            suffix = target_suffix(target_lang)
            partial_path = make_partial_path(
                subtitle,
                suffix,
                expected_primary_suffix(subtitle),
                output.parent,
            )
            self._post(
                "log",
                f"共 {len(document.translatable_indices())} 条"
                "可翻译对白，开始调用 LLM",
            )

            client = LLMClient(self.config.llm)
            job = TranslationJob(
                document=document,
                client=client,
                batch_size=self.config.llm.batch_size,
                concurrency=self.config.llm.concurrency,
                source_lang=source_lang,
                target_lang=target_lang,
                movie_name=subtitle.stem,
                partial_path=partial_path,
                progress_cb=self._on_progress,
                cancel_event=self.cancel_event,
            )
            report = job.run()

            if self.cancel_event.is_set():
                saved = False
                try:
                    save_subtitle(document, partial_path)
                    saved = True
                except Exception:
                    logger.exception("保存已翻译字幕失败")
                raise TranslationCancelled(
                    partial_path if saved else None
                )

            self._post(
                "log",
                f"翻译完成：{report.total_events} 条，唯一文本 "
                f"{report.unique_texts} 条，批次 {report.batches}",
            )

            try:
                outputs, warning = save_subtitle_outputs(
                    document, subtitle, output
                )
            except Exception as exc:
                saved = False
                try:
                    save_subtitle(document, partial_path)
                    saved = True
                except Exception:
                    logger.exception("保存 partial 字幕失败")
                message = f"保存翻译字幕失败：{exc}"
                if not saved:
                    message += (
                        "\n（已翻译部分保存失败，详见日志）"
                    )
                raise TranslationError(
                    message,
                    partial_path=partial_path if saved else None,
                ) from exc

            for path in outputs:
                self._post("log", f"已生成字幕：{path}")
            if warning:
                self._post("log", warning)

            try:
                partial_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "删除 partial 字幕失败：%s", exc
                )
            self._post("progress", 100, 100, "完成")
            self._post("done", outputs, warning)
        except TranslationCancelled as exc:
            self._post(
                "error", "已取消", cancel_message(exc.partial_path)
            )
        except TranslationError as exc:
            extra = (
                f"\n已翻译部分保留在：\n{exc.partial_path}"
                if exc.partial_path
                else ""
            )
            self._post("error", "翻译失败", f"{exc}{extra}")
        except Exception as exc:
            logger.exception("任务失败")
            self._post("error", "任务失败", str(exc))
        finally:
            self._post("finished")

    def _on_progress(
        self, done: int, total: int, message: str
    ) -> None:
        self._post("progress", done, total, message)

    def _post(self, kind: str, *payload) -> None:
        self.message_queue.put((kind, payload))

    def _poll_messages(self) -> None:
        try:
            while True:
                kind, payload = self.message_queue.get_nowait()
                if kind == "log":
                    self._log(str(payload[0]))
                elif kind == "progress":
                    done, total, message = payload
                    percent = 0 if not total else int(done * 100 / total)
                    self.progress_var.set(percent)
                    self.status_var.set(f"{message}    {percent}%")
                elif kind == "probe_ok":
                    path, tracks = payload
                    if path == self.video_path:
                        self.probing = False
                        self.status_var.set("就绪")
                        self._set_inputs_enabled(True)
                        self._finish_probe(tracks)
                elif kind == "probe_error":
                    path, message = payload
                    if path == self.video_path:
                        self.probing = False
                        self.probe_ok = False
                        self.status_var.set("探测失败")
                        self._set_inputs_enabled(True)
                        self.track_combo.configure(
                            values=[], state="disabled"
                        )
                        self._log(f"探测字幕轨失败：{message}")
                        messagebox.showerror("探测失败", message)
                elif kind == "detected_lang":
                    serial, code = payload
                    if serial == self.detect_serial:
                        self.detecting = False
                        self._refresh_detect_button()
                        self.source_var.set(language_label(code))
                        self._log(
                            f"已识别源语言：{language_label(code)}"
                        )
                        self.status_var.set(
                            "仅字幕模式"
                            if self.video_path is None
                            and self.external_sub is not None
                            else "就绪"
                        )
                elif kind == "detect_failed":
                    serial, message = payload
                    if serial == self.detect_serial:
                        self.detecting = False
                        self._refresh_detect_button()
                        self._log(f"源语言识别失败：{message}")
                        self.status_var.set(
                            "源语言识别失败，请手动选择"
                        )
                        messagebox.showwarning(
                            "识别失败",
                            "无法识别源语言，请手动选择源语言。",
                        )
                elif kind == "phase":
                    if payload[0] == "muxing":
                        self.cancel_btn.configure(state="disabled")
                        self.status_var.set(
                            "正在封装 MKV（此阶段不可取消）..."
                        )
                elif kind == "done":
                    raw_outputs = payload[0]
                    if isinstance(raw_outputs, (str, Path)):
                        raw_outputs = [raw_outputs]
                    outputs = [
                        Path(path) for path in raw_outputs
                    ]
                    warning = (
                        payload[1] if len(payload) > 1 else ""
                    )
                    self.status_var.set("完成")
                    for path in outputs:
                        self._log(f"已生成：{path}")
                    message = (
                        "已生成：\n"
                        + "\n".join(
                            str(path) for path in outputs
                        )
                        + "\n\n是否打开所在文件夹？"
                    )
                    if warning:
                        message = f"{warning}\n\n{message}"
                    if outputs and messagebox.askyesno(
                        "翻译完成", message
                    ):
                        try:
                            os.startfile(
                                str(outputs[0].parent)
                            )
                        except Exception:
                            pass
                elif kind == "error":
                    title, message = payload
                    self.status_var.set(title)
                    self._log(f"{title}：{message}")
                    if title == "已取消":
                        messagebox.showwarning(title, message)
                    else:
                        messagebox.showerror(title, message)
                elif kind == "finished":
                    self._set_inputs_enabled(True)
                    self.cancel_btn.configure(state="disabled")
                    self.worker = None
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _sync_mode_widgets(self) -> None:
        standalone = (
            self.video_path is None
            and self.external_sub is not None
        )
        if standalone:
            self.export_check.configure(state="disabled")
        elif self.worker is None or not self.worker.is_alive():
            self.export_check.configure(state="normal")

    def _set_inputs_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in (
            self.video_btn,
            self.output_entry,
            self.output_btn,
            self.export_check,
            self.start_btn,
            self.settings_btn,
            self.external_btn,
            self.detect_lang_btn,
        ):
            widget.configure(state=state)
        self.video_entry.configure(
            state="readonly" if enabled else "disabled"
        )
        for combo in (self.source_combo, self.target_combo):
            combo.configure(state="readonly" if enabled else "disabled")
        if enabled:
            self.track_combo.configure(
                state="readonly" if self.track_combo["values"] else "disabled"
            )
            self._sync_mode_widgets()
            self._refresh_detect_button()
        else:
            self.track_combo.configure(state="disabled")

    def _cancel(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            self.cancel_event.set()
            self.status_var.set("正在取消...")
            self._log("已请求取消，等待当前批次完成...")
            self.cancel_btn.configure(state="disabled")

    def _log(self, message: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        logger.info(message)

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askyesno(
                "正在翻译",
                "翻译尚未完成，确定要退出吗？\n"
                "已翻译部分可能无法保存。",
            ):
                return
            self.cancel_event.set()
            self.worker.join(timeout=5)
            if self.worker.is_alive():
                if not messagebox.askyesno(
                    "仍在运行",
                    "后台任务尚未停止，强制退出可能丢失进度。确定退出吗？",
                ):
                    return
        self.root.destroy()


def run() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    setup_logging()
    root = tk.Tk()
    SubtitleTranslatorApp(root)
    root.mainloop()
