# -*- mode: python ; coding: utf-8 -*-

import os

import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_submodules

ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()

a = Analysis(
    [os.path.join(SPECPATH, "main.py")],
    pathex=[SPECPATH],
    binaries=[(ffmpeg_bin, ".")],
    datas=[],
    hiddenimports=collect_submodules("pysubs2") + ["openai"],
    hookspath=[],
    runtime_hooks=[],
    # imageio_ffmpeg is excluded because the ffmpeg binary is bundled explicitly.
    # pandas/numpy/matplotlib are optional openai extras; this app only uses chat.completions.
    excludes=["imageio_ffmpeg", "pandas", "matplotlib", "numpy"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="SubtitleTranslator",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
