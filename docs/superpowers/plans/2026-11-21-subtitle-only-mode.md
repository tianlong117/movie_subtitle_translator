# 仅翻译字幕模式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 允许不选择电影、只选择字幕文件时翻译字幕，输出文件后缀跟随目标语言，ASS/SSA 源额外生成 SRT 兼容版。

**Architecture:** 在现有 GUI 中新增一个独立的仅字幕工作流：复用“选外挂字幕...”入口，无电影时进入仅字幕模式；新增纯路径辅助函数和 `_run_subtitle_task`，电影封装流程保持不动。日志框下方增加版权说明入口。

**Tech Stack:** Python 3.13、Tkinter/ttk、pysubs2、OpenAI 兼容客户端、unittest、PyInstaller。

---

## 计划须知

- 规格文档：`movie_subtitle_translator/docs/superpowers/specs/2026-11-21-subtitle-only-mode-design.md`
- 仓库当前已有与本功能无关的暂存文件（`host_scanner/**/*.pyc`）。**所有提交必须使用 `git commit --only <明确文件>`，不要提交其他暂存内容。**
- 上一轮“30 种语言”改动已经实现并通过测试，但尚未提交；本计划不重复修改语言表。
- 工作目录始终是 `D:\python\movie_subtitle_translator`，命令中的路径相对于该目录。

## File Structure

- `app/gui.py`：新增纯路径辅助函数、版权文本、界面提示、仅字幕 worker 和开始校验。
- `tests/test_gui.py`：新增路径辅助函数、版权文本和仅字幕 worker 的测试。
- `README.md`：补充仅字幕模式说明。
- 不新增业务模块；本次改动保持在现有 GUI 文件内，避免扩大范围。

---

### Task 1: 字幕输出路径辅助函数

**Files:**
- Modify: `app/gui.py`（顶部 imports 和第 50 行附近）
- Test: `tests/test_gui.py`

- [ ] **Step 1: 写失败测试**

将 `tests/test_gui.py` 顶部的 imports 替换为以下内容：

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pysubs2

from app.gui import (
    cancel_message,
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
```

在 `GuiHelperTests` 类之前加入测试辅助函数：

```python
def make_document(fmt: str = "ass") -> SubtitleDocument:
    subs = pysubs2.SSAFile()
    subs.events.append(
        pysubs2.SSAEvent(start=0, end=1000, text="Hello")
    )
    return SubtitleDocument(subs=subs, fmt=fmt)
```

在 `GuiHelperTests` 类中加入以下测试方法：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
python -m unittest tests.test_gui -v
```

Expected: `ImportError: cannot import name 'default_subtitle_output_path'`。

- [ ] **Step 3: 实现最小代码**

修改 `app/gui.py` 的 imports：

```python
from .subtitle_io import (
    SubtitleDocument,
    load_subtitle,
    save_subtitle,
)
```

在 `logger = logging.getLogger(__name__)` 后面加入：

```python
SUBTITLE_SUFFIXES = {".srt", ".ass", ".ssa"}
```

在 `default_output_path` 后面加入以下函数：

```python
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
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
python -m unittest tests.test_gui -v
```

Expected: 全部通过，包括新增的 8 个测试。

- [ ] **Step 5: 提交**

```bash
git commit --only movie_subtitle_translator/app/gui.py movie_subtitle_translator/tests/test_gui.py -m "feat: add subtitle-only output helpers"
```

---

### Task 2: 版权说明文本常量

**Files:**
- Modify: `app/gui.py`
- Test: `tests/test_gui.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_gui.py` 的 `app.gui` imports 中加入：

```python
    COPYRIGHT_TEXT,
```

在 `GuiHelperTests` 类中加入：

```python
    def test_copyright_text_contains_required_notices(self):
        for phrase in (
            "软件开发者：天龙",
            "自由软件",
            "不提供、不存储、不分发",
            "版权责任由用户自行承担",
        ):
            self.assertIn(phrase, COPYRIGHT_TEXT)
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
python -m unittest tests.test_gui -v
```

Expected: `ImportError: cannot import name 'COPYRIGHT_TEXT'`。

- [ ] **Step 3: 添加常量**

在 `logger = logging.getLogger(__name__)` 后面加入：

```python
COPYRIGHT_TEXT = (
    "软件开发者：天龙\n\n"
    "版权说明\n\n"
    "本软件为自由软件，开发者放弃对软件本身的版权主张，"
    "任何人可自由使用、修改和分发。\n\n"
    "本软件仅提供字幕翻译工具，不提供、不存储、不分发任何字幕或影视内容。"
    "您翻译的字幕仅供个人学习、研究或欣赏，不得上传公开网络、传播分享或用于商业用途。"
    "请确保您的翻译行为符合著作权法规定，因使用本软件产生的版权责任由用户自行承担。"
)
```

- [ ] **Step 4: 运行测试，确认通过**

Run:

```bash
python -m unittest tests.test_gui -v
```

Expected: 全部通过。

- [ ] **Step 5: 提交**

```bash
git commit --only movie_subtitle_translator/app/gui.py movie_subtitle_translator/tests/test_gui.py -m "feat: add copyright notice text"
```

---

### Task 3: 界面提示与版权说明对话框

**Files:**
- Modify: `app/gui.py`

- [ ] **Step 1: 添加字体 import**

在 `import tkinter as tk` 后面加入：

```python
import tkinter.font as tkfont
```

- [ ] **Step 2: 替换 `_build_ui`**

将 `app/gui.py` 中完整的 `_build_ui` 方法替换为：

```python
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
        self.source_combo.pack(side="left", padx=(4, 16))
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
```

- [ ] **Step 3: 添加版权说明对话框**

在 `_open_settings` 方法之后、`_selected_track` 之前加入：

```python
    def _open_copyright_dialog(self) -> tk.Toplevel:
        dialog = tk.Toplevel(self.root)
        dialog.title("版权说明")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)

        text = tk.Text(
            frame,
            width=64,
            height=10,
            wrap="word",
            relief="flat",
            borderwidth=0,
        )
        text.insert("1.0", COPYRIGHT_TEXT)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)

        ttk.Button(
            frame, text="关闭", command=dialog.destroy
        ).pack(pady=(12, 0))
        return dialog
```

- [ ] **Step 4: 手工验证界面**

Run:

```bash
PYTHONIOENCODING=utf-8 python - <<'PY'
import tkinter as tk
from app.gui import SubtitleTranslatorApp
root = tk.Tk()
root.withdraw()
app = SubtitleTranslatorApp(root)
print("hint:", app.hint_label.cget("text"))
print("link:", app.copyright_label.cget("text"))
dialog = app._open_copyright_dialog()
print("dialog:", dialog.title())
dialog.destroy()
root.destroy()
PY
```

Expected:

```text
hint: 未选择视频文件时，仅选择字幕文件即可单独翻译字幕。
link: 版权说明
dialog: 版权说明
```

- [ ] **Step 5: 提交**

```bash
git commit --only movie_subtitle_translator/app/gui.py -m "feat: add subtitle-only hint and copyright dialog"
```

---

### Task 4: 仅字幕模式的界面状态

**Files:**
- Modify: `app/gui.py`

- [ ] **Step 1: 替换输出自动路径逻辑**

将 `_set_output_auto` 和 `_on_target_changed` 替换为：

```python
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
```

- [ ] **Step 2: 修改输出另存为对话框**

将 `_choose_output` 替换为：

```python
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
```

- [ ] **Step 3: 修改外挂字幕选择逻辑**

将 `_choose_external_subtitle` 替换为：

```python
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
```

- [ ] **Step 4: 添加模式同步方法并调整启用状态**

在 `_set_inputs_enabled` 之前加入：

```python
    def _sync_mode_widgets(self) -> None:
        standalone = (
            self.video_path is None
            and self.external_sub is not None
        )
        if standalone:
            self.export_check.configure(state="disabled")
        elif self.worker is None or not self.worker.is_alive():
            self.export_check.configure(state="normal")
```

在 `_set_inputs_enabled` 方法末尾，`else` 分支之后加入：

```python
        if enabled:
            self._sync_mode_widgets()
```

完整结尾应为：

```python
        if enabled:
            self.track_combo.configure(
                state=(
                    "readonly"
                    if self.track_combo["values"]
                    else "disabled"
                )
            )
            self._sync_mode_widgets()
        else:
            self.track_combo.configure(state="disabled")
```

- [ ] **Step 5: 手工验证状态**

Run:

```bash
PYTHONIOENCODING=utf-8 python - <<'PY'
import tkinter as tk
from pathlib import Path
from app.gui import SubtitleTranslatorApp
root = tk.Tk()
root.withdraw()
app = SubtitleTranslatorApp(root)
app.target_var.set("简体中文")
app.external_sub = Path("D:/tmp/Movie.en.ass")
app._sync_mode_widgets()
app._set_output_auto()
print("output:", app.output_var.get())
print("export:", app.export_check["state"])
app.target_var.set("日语")
app._on_target_changed()
print("after target:", app.output_var.get())
root.destroy()
PY
```

Expected:

```text
output: D:\tmp\Movie.en.zh.ass
export: disabled
after target: D:\tmp\Movie.en.ja.ass
```

- [ ] **Step 6: 提交**

```bash
git commit --only movie_subtitle_translator/app/gui.py -m "feat: add subtitle-only mode state"
```

---

### Task 5: 仅字幕开始校验与翻译任务

**Files:**
- Modify: `app/gui.py`
- Test: `tests/test_gui.py`

- [ ] **Step 1: 写失败的端到端 worker 测试**

在 `tests/test_gui.py` 顶部补充 imports：

```python
import os
import threading
import tkinter as tk

from app.config import AppConfig, LLMSettings
from app.gui import SubtitleTranslatorApp
from tests.fake_llm_server import FakeLLMServer
```

在测试模块顶部加入：

```python
def tk_available() -> bool:
    try:
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except tk.TclError:
        return False
```

在 `if __name__ == "__main__":` 之前加入：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run:

```bash
python -m unittest tests.test_gui.StandaloneWorkerTests -v
```

Expected: `AttributeError: 'SubtitleTranslatorApp' object has no attribute '_run_subtitle_task'`。

- [ ] **Step 3: 替换 `_start`**

将 `app/gui.py` 的 `_start` 方法替换为：

```python
    def _start(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            return
        if self.probing:
            messagebox.showinfo("请稍候", "正在探测字幕轨，请稍候")
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
```

- [ ] **Step 4: 添加 `_run_subtitle_task`**

在 `_run_task` 方法之后、`_on_progress` 之前加入：

```python
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
```

- [ ] **Step 5: 更新完成消息处理**

将 `_run_task` 中的：

```python
            self._post("done", output, export_warning)
```

改为：

```python
            self._post("done", [output], export_warning)
```

将 `_poll_messages` 中的 `done` 分支替换为：

```python
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
```

- [ ] **Step 6: 运行测试，确认通过**

Run:

```bash
python -m unittest tests.test_gui.StandaloneWorkerTests -v
python -m unittest tests.test_gui -v
```

Expected: 全部通过；端到端测试生成 `.ass` 和 `.srt`，两者正文都包含 `中Hello`。

- [ ] **Step 7: 运行完整测试套件**

Run:

```bash
python -m unittest discover -v
```

Expected: 138 个测试全部通过。

- [ ] **Step 8: 提交**

```bash
git commit --only movie_subtitle_translator/app/gui.py movie_subtitle_translator/tests/test_gui.py -m "feat: translate standalone subtitle files"
```

---

### Task 6: README 补充

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 补充功能说明**

在“- 内置 30 种常用语言...”这一条后面加入：

```markdown
- 仅翻译字幕：不选电影，直接点击“选外挂字幕...”选择 `.srt/.ass/.ssa`，只输出翻译后的字幕，不封装视频。
```

- [ ] **Step 2: 补充使用步骤**

在“## 使用步骤”的第 6 步之后加入：

```markdown
### 仅翻译字幕

1. 不选电影，直接点“选外挂字幕...”选择 `.srt/.ass/.ssa`。
2. 确认源/目标语言和输出路径。
3. 点“开始翻译”。

输出文件默认在源字幕同目录，后缀按目标语言生成（简中 `.zh`、繁中 `.cht`、英语 `.en`、日语 `.ja` 等）。ASS/SSA 源会生成原格式主文件和一份 `.srt` 兼容版；SRT 源只生成一份 `.srt`。
```

- [ ] **Step 3: 检查文档改动**

Run:

```bash
git diff -- README.md
```

Expected: 只出现上述两条新增内容（README 可能已有上一轮语言说明的未提交改动）。

- [ ] **Step 4: 提交**

```bash
git commit --only movie_subtitle_translator/README.md -m "docs: describe subtitle-only mode"
```

---

### Task 7: 完整验证与打包

**Files:**
- No source changes

- [ ] **Step 1: 运行完整测试套件**

Run:

```bash
python -m unittest discover -v
```

Expected: 138 个测试全部通过。

- [ ] **Step 2: 重新打包 exe**

Run:

```bash
pyinstaller subtitle_translator.spec --noconfirm
```

Expected: 最后输出 `Build complete!`，生成 `dist/SubtitleTranslator.exe`。

- [ ] **Step 3: 检查产物**

Run:

```bash
ls -l dist/SubtitleTranslator.exe
```

Expected: 文件存在，修改时间为本次构建时间。

- [ ] **Step 4: 手工验收仅字幕模式**

1. 准备两个临时样例：
   - `sample.en.srt`：2 条英文对白
   - `sample.en.ass`：2 条英文对白
2. 启动 `dist/SubtitleTranslator.exe`（已带 dist 配置）；如果使用 `python main.py`，先在“LLM 设置...”中填好 API。
3. 不选电影，点“选外挂字幕...”，选择 `sample.en.srt`。
4. 确认输出框自动变为 `sample.en.zh.srt`；把目标语言改为“日语”，确认输出变为 `sample.en.ja.srt`。
5. 改回“简体中文”，点“开始翻译”；确认只生成 `sample.en.zh.srt`。
6. 不选电影，选择 `sample.en.ass`；确认输出为 `sample.en.zh.ass`，翻译后同时生成 `sample.en.zh.ass` 和 `sample.en.zh.srt`。
7. 点击日志框下方的“版权说明”，确认弹窗内容包含“软件开发者：天龙”和免责声明，能正常关闭。
8. 选择一个 MKV，确认原有“翻译并封装 MKV”流程仍可正常探测字幕轨和开始翻译；确认“同时导出翻译后的字幕”复选框恢复可用。

Expected: 所有步骤正常；仅字幕模式不调用 ffmpeg 封装，不修改源字幕。

- [ ] **Step 5: 如发现回归，先写失败测试再修复**

任何手工验收发现的问题，先在 `tests/test_gui.py` 写成可复现的失败测试，再修复实现并重新运行完整测试套件。
