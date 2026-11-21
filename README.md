# 电影字幕翻译器

选择一个 MKV 电影，自动提取其中的英文字幕轨，用 OpenAI 兼容的大模型翻译成中文，再作为一条新字幕轨封装回 MKV。也可以不选电影，直接选择字幕文件，只翻译并输出字幕。原片不会被修改。

## 功能

- 自动探测 MKV 内的文本字幕轨（SRT/ASS/SSA）。
- 无文本字幕时，可手动选择外挂 `.srt/.ass/.ssa` 文件。
- SRT/ASS 保留原格式；SSA 会转换为 ASS（样式保留）；WebVTT 会转换为 SRT。时间轴不变。
- 输出 `电影名.<目标后缀>.mkv`（简中 `.zh`、繁中 `.cht`、英语 `.en`）：视频、音频、原字幕轨、字体附件全部流复制，新增一条目标语言字幕轨，并放在字幕轨首位、标记为默认，便于只认第一条字幕的播放器（如 QQ 影音）直接显示。
- 可选同时导出翻译后的外挂字幕（默认勾选）。
- OpenAI 兼容接口：可接 DeepSeek、OpenAI、通义千问、Kimi、本地 Ollama 等。
- 内置 30 种常用语言（按使用人数选取，含简体/繁体中文），源语言与目标语言可选范围一致；默认英语 → 简体中文。
- 仅翻译字幕：不选电影，直接点击“选外挂字幕...”选择 `.srt/.ass/.ssa`，只输出翻译后的字幕，不封装视频。
- 源语言右侧提供“AI 识别”按钮：不确定源语言时点击，程序会从当前外挂字幕或文本字幕轨取样并调用 LLM 识别；识别结果可手动修改。
- 实时进度、日志、取消；失败时保留已翻译的 partial 字幕。

## 环境

- Windows 10/11 64 位
- Python 3.13（仅源码运行/打包需要）
- 依赖见 `requirements.txt`

## 源码运行

```bash
cd movie_subtitle_translator
python -m pip install -r requirements.txt
python main.py
```

## 使用步骤

1. 点“浏览...”选择 `.mkv`。
2. 在“字幕轨”下拉框选择英文字幕轨；如果没有文本轨，点“选外挂字幕...”。
3. 确认源语言（默认英语；不确定时点右侧“AI 识别”按钮）和目标语言（默认简体中文）；两者均可从 30 种常用语言中选择。
4. 点“LLM 设置...”，填写 API 地址、API Key、模型，点“测试连接”验证后保存。
5. 确认输出路径，选择是否导出翻译后的字幕。
6. 点“开始翻译”。

### 仅翻译字幕

1. 不选电影，直接点“选外挂字幕...”选择 `.srt/.ass/.ssa`。
2. 确认源/目标语言和输出路径；不确定源语言时点源语言右侧的“AI 识别”按钮。
3. 点“开始翻译”。

输出文件默认在源字幕同目录，后缀按目标语言生成（简中 `.zh`、繁中 `.cht`、英语 `.en`、日语 `.ja` 等）。ASS/SSA 源会生成原格式主文件和一份 `.srt` 兼容版；SRT 源只生成一份 `.srt`。

默认配置指向 DeepSeek：

- API 地址：`https://api.deepseek.com/v1`
- 模型：`deepseek-chat`

## 支持与不支持

支持：

- MKV 内的文本字幕：`subrip`、`ass`、`ssa`、`webvtt`（WebVTT 会转换为 SRT）
- 外挂 `.srt`、`.ass`、`.ssa`
- 任意 OpenAI 兼容 `/v1/chat/completions` 接口

不支持（第一版）：

- PGS/DVD 等图形字幕的 OCR
- `mov_text` 原始字幕轨（MP4 专用；MKV 封装不支持，请先转换为 SRT/ASS）
- 无字幕影片的语音识别
- 字幕时间轴调整
- 多文件批量队列
- 在原文件上直接修改

## 配置与日志

- 配置文件：`subtitle_translator_config.json`，与 exe（源码运行时为项目目录）同目录。
- 日志：`translator.log`，1MB 轮转，保留 2 份。
- API Key 明文保存在配置文件中，请勿把配置文件提交到公开仓库。
- exe 所在目录必须可写；不要放在 `Program Files` 等只读位置，否则配置无法保存、日志无法写入。

## 打包 exe

```bash
cd movie_subtitle_translator
pyinstaller subtitle_translator.spec --noconfirm
```

构建验证环境：Python 3.13.2、PyInstaller 6.20.0、openai 2.33.0、pysubs2 1.9.0、imageio-ffmpeg 0.6.0。

产物：`dist/SubtitleTranslator.exe`。ffmpeg 已内置，目标电脑无需安装 Python 或 ffmpeg。单文件 exe 每次启动都会解压到临时目录，约需 2-5 秒，并占用约 120MB 临时空间；正常退出会清理。

内置 ffmpeg 为 gyan.dev 7.1 构建；分发 exe 时请遵守其许可证。

## 测试

```bash
python -m unittest discover -v
```

集成测试会调用本机 ffmpeg 生成 10 秒测试 MKV，并使用本地假 LLM 服务，不产生 API 费用。若本机没有 ffmpeg，集成测试会自动跳过；完整构建环境应确保 ffmpeg 可用。

## 已知限制

- 模型偶尔会破坏 ASS 占位符；程序会校验并在必要时降级为纯文本翻译，极少数复杂特效可能丢失。
- 长电影翻译耗时取决于模型速度和并发设置；默认并发 3，可在设置中调整。
- 部分 OpenAI 兼容服务不支持高并发，遇到 429 请把并发数调低。
