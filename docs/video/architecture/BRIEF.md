---
workflow: faceless-explainer
flow: automation
storyboard: no
message: "Lecture Live Notes 的每一個架構設計，都對應一個上課時真的會遇到的需求或失敗"
destination: youtube
aspect: 1920x1080
language: zh-TW
audience: "懂一點程式的開發者：專案維護者本人、未來的協作者"
length: 120-150s
angle: concept
narration: yes
style_preset: cobalt-grid
voice: minimax-clone
---

## Intent

用動畫講解 Lecture Live Notes 整個 app 的架構與設計，著重「需求」以及「解決需求的對應設計」。
使用者原話：「幫我把這整個 app 的架構用 hyperframes 做成一個動畫講解整個架構以及設計，著重在需求以及解決需求的相對應設計」。

敘事角度（使用者選定）：**架構總覽圖逐層展開**。先給一張全貌（瀏覽器 → FastAPI → Gemini Live / Gemini Flash → 磁碟），
再逐個方塊放大，每一層都講清楚「這裡要解決什麼需求」→「所以設計成這樣」。

## Customizations

- 旁白：使用者本人的 MiniMax 克隆聲。設定沿用 ai-agent-eli5 系列：
  model `speech-2.8-hd`、voice_id `moss_audio_ae939d41-6788-11f1-a909-feb3e5c18eb0`、speed 1.15、emotion happy、vol 1.0、pitch 0。
  不經 HyperFrames 內建 TTS；以 host audio 方式接入 audio_meta.json。
- BGM：輕柔純樂器背景樂，旁白出現時 ducking。
- 視覺：cobalt-grid preset。

## Notes

- 事實來源僅限 repo：`docs/adr/0001`–`0008`、根目錄 `CLAUDE.md`、`app.py`。不編造數字。
- 專案位置：`docs/video/architecture/`（repo 內）。渲染出的 MP4、音檔加入 .gitignore，專案原始檔 commit。
- 畫面文字與旁白一律繁體中文，技術名詞保留英文（API、WebSocket、mtime…）。
- `lectures/` 是真實課堂內容，影片不得引用任何實際課堂資料。
