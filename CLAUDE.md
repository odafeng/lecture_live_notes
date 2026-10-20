# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

錄音一整堂課，邊上課邊生出可複習的中文筆記。FastAPI 後端 + 無框架前端，全部跑在使用者自己的機器上。

## 指令

virtualenv 一律是專案裡的 `.venv`。這不是偏好問題：`launcher.py` 會用 `.venv/bin/python` 去啟動 server，而 `tests/test_launcher.py` 真的會跑到那段，所以 CI 也是裝進 `.venv` 而不是 runner 的 Python。

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium        # E2E 需要

.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
curl 127.0.0.1:8000/health                              # key 讀到了嗎

.venv/bin/python -m unittest discover -s tests -t .     # 全部
.venv/bin/python -m unittest tests.test_workflow        # 單一檔案
.venv/bin/python -m unittest tests.test_app.Translations.test_every_language_the_group_reads_is_produced

.venv/bin/python scripts/build_macos_app.py             # 建 Lecture.app（僅 macOS）
```

用 `unittest`，不是 pytest——pytest 沒有裝。專案沒有 linter、formatter 或 type checker，CI 只跑測試。

## 一堂課的資料流

`app.py` 是單一檔案（約 1,200 行），整條管線都在裡面：

瀏覽器送 16kHz PCM 進 `/ws` → 後端另開一條 WebSocket 到 Gemini Transcribe Live → 逐字稿累積 → 每 `NOTE_WINDOW_SECONDS`（60 秒）叫一次 Gemini Flash 產出一段 note block → 每累積 `ROLLUP_EVERY_BLOCKS`（10 段）壓成一個章節摘要 → 下課時把章節摘要加上未整併的段落做最終整併 → 整併成功後翻成四種語言。

Gemini 的連線每 `SESSION_ROTATE_SECONDS`（570 秒）主動輪替，因為它本身有時間上限（ADR 0003）。輪替期間錄音不中斷。

重試分三層，因為三種呼叫的失敗代價不同：`LIVE_RETRY_DELAYS` 很快放棄（逐字稿還在，補不到那段筆記不致命）、`FINAL_RETRY_DELAYS` 等到 120 秒（整併是最貴也最不能丟的一次）、`BACKGROUND_RETRY_DELAYS` 用分鐘級間隔在瀏覽器關掉之後繼續試。

## 兩條寫入路徑

這是最容易誤判的地方。最終筆記有兩條產生路徑，改動任何一條都要想到另一條：

1. websocket 下課收尾
2. `POST /finalize/{session_id}` 重新整併——使用者手動按，或 `retry_finalize_in_background()` 在背景重試

兩條都必須經過 `session_lock(session_id)` 與 `publish_final_notes()`。沒有這把鎖的話，手動重整併成功之後，一次晚到的失敗會把 fallback 蓋回去；背景重試也會在等到鎖之後重跑一次已經成功的整併（所以 `skip_if_merged` 的檢查必須在**取得鎖之後**）。

`finalize_input.json` 在整併開始前就寫好，重跑因此不需要 websocket（ADR 0002）。

## 幾個不變量

- **譯本不得比正本舊。** `is_current()` 比對 mtime，比 `final_notes.md` 舊的譯本不進下載連結、不進 ZIP、`/download` 直接 404。刪除舊譯本只是盡力而為（唯讀檔會讓 `unlink` 失敗），把關的是時間戳（ADR 0006）。
- **OpenCC (`s2tw`) 套用在逐字稿與筆記，不套用在譯本。** 課程名稱也轉。
- **更正是指示，手寫筆記是素材。** 更正累積成清單注入三個 prompt 並宣告「衝突時以更正為準」；手寫筆記由後端原文附加在 `USER_NOTES_HEADING` 底下，不交給模型轉述（ADR 0005）。
- **翻譯是整併之後獨立的一步。** 不併進 `final_prompt`，整併失敗時完全跳過（ADR 0006）。
- **摘要的 thinking 固定壓到最低。** Gemini 的 thought token 計入 `maxOutputTokens`，預設等級會把預算吃光、回傳空正文；`thinkingLevel: minimal` 寫死在 `SUMMARY_GENERATION_CONFIG`，它不保證完全不思考，換 `SUMMARY_MODEL` 前要先確認新模型接受它（ADR 0008）。
- 一天一個資料夾：`lectures/YYYYMMDD/HHMMSS/`（ADR 0004）。`lectures/` 被 `.gitignore` 擋住，裡面是真實課堂內容。

決策理由都在 `docs/adr/`。改到相關行為時一併更新對應的 ADR。

## 測試

測試從不呼叫真實服務。`tests/fakes.py` 提供 `fake_services()`（假的 Gemini Live socket + 假的 Gemini generateContent transport）給 workflow 與 E2E 用。

三個容易踩的坑，都實際發生過：

- **新的測試類別必須 `patch.object(app, "GEMINI_API_KEY", "test-key")`。** 沒 patch 的話，本機因為 `.env` 有真 key 會全綠，CI 沒有 key 則在碰到 mock transport 之前就 `RuntimeError`。
- **判斷翻譯請求是哪個語言，要讀 prompt 的第一行。** prompt 內文含有 "Leave English technical terms in English"，子字串比對會讓每個語言都命中 English。
- **不要拿 `app.BUNDLE_FILES` 當 ZIP 測試的期望值。** 那個常數就是 ZIP 的定義，拿它比對自己，從常數裡刪掉一個檔案測試照樣會過。要明列檔名。

## 前端

`static/` 底下是原生 HTML/CSS/JS，沒有建置步驟，改完重新整理即可。`renderDownloads()` 以 `labels` 這個 map 決定顯示哪些下載項目與順序——後端回傳了但 `labels` 沒有的 key 會被靜默略過。視覺與狀態機的規範在 `DESIGN.md`。
