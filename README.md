# Lecture Live Notes — Gemini Transcribe Live + Claude Haiku

一個適合「大學上課時放著跑」的即時課堂筆記 App。

首次從 GitHub 下載時，請依下方安裝步驟建立 Python 環境，並複製 `.env.example` 為 `.env` 填入自己的 API key。Repo 包含程式、測試與啟動器原始碼；本機 API key、課堂資料及產生的 `Lecture.app` 不納入版本控制。macOS 啟動器的建置方式見第 26 節。

## macOS：雙擊開啟

這台 Mac 已完成環境設定，日常使用只需要：

1. 雙擊桌面的 **Lecture.app**，瀏覽器會自動開啟課堂工作台。
2. 按「開始上課」，允許瀏覽器使用麥克風。
3. 下課按「下課／停止」，等到「已儲存」出現後再關閉頁面。

不需要開 Terminal，也不需要手動啟動 uvicorn。重複開啟會沿用已啟動的服務；原本的 port 被其他程式占用時，啟動器會自動選擇可用的 port。請使用啟動器打開的網址。

桌面的 **課堂筆記檔案** 捷徑會打開 `lectures/`。每堂課的逐字稿位於 `lectures/日期_時間/transcript.txt`；同一資料夾也包含錄音與筆記。

`Lecture.app` 是這個專案的本機啟動器，沿用現有 `.venv` 與 `.env`，請保留專案資料夾原位。關閉網頁後，本機服務會留在背景供下次使用；Mac 重新開機後，再雙擊即可重新啟動。

---

它會在你的電腦上開一個本機網頁，持續使用麥克風收音，將音訊串流到 Gemini 3.5 Transcribe Live 做即時語音辨識，再由 Anthropic Claude Haiku 4.5 把已確認的逐字稿分段整理成課堂筆記。下課按下停止後，App 會再把整堂課的分段筆記整併成一份完整複習筆記。

> 重要：錄音檔會保存在你的電腦，即時音訊會傳送到 Google Gemini API 做語音辨識，逐字稿與筆記素材會傳送到 Anthropic API 做摘要。請只在你有權錄音、且允許將內容送至雲端服務的場合使用。

---

## 1. 這個 App 會做什麼？

整體流程：

```text
老師講課
   ↓
電腦麥克風
   ↓
Browser 將音訊轉成 PCM16 / 16 kHz mono
   ↓
本機 FastAPI server
   ├── 同時保存 lecture.wav
   ↓
Gemini 3.5 Transcribe Live
   ├── interim transcript → 畫面即時顯示
   └── finalized transcript → 正式寫入逐字稿
                                ↓
                         約 60 秒一段
                                ↓
                         Claude Haiku 4.5
                                ↓
                         即時課堂筆記 block
                                ↓
                         多個 block 整併成章節
                                ↓
                         下課後完整筆記
```

你上課時會看到：

- 桌面左側：課程設定；主區並排顯示即時逐字稿與課堂筆記
- 手機：可展開課程設定，並切換逐字稿或課堂筆記
- 逐字稿最下方：尚未確認的 interim transcription
- 上方：上課計時器、錄音狀態與麥克風音量指示
- `標記重點`：老師提到重要內容時手動留一個時間標記
- `自動捲動`：可關閉以閱讀前面的內容，開啟後回到最新記錄
- 下課後：文件排版的完整筆記，以及所有輸出檔案的下載按鈕

即時逐字稿（含 interim）、課堂筆記與完整筆記都會經 OpenCC 轉為繁體中文（臺灣正體），英文術語保留原文。畫面上的筆記會將 Markdown 顯示為標題、粗體、清單及表格；下載的 `.md` 檔保留 Markdown 格式。

---

## 2. 使用的模型

### 即時 ASR

```text
gemini-3.5-transcribe-live
```

用途：低延遲即時語音辨識。

本 App 使用：

- automatic language detection
- Chinese / English code-switching
- interim transcription
- finalized transcription
- custom vocabulary biasing
- SMART / VERBATIM transcription mode

Gemini Live Transcribe 單次 continuous streaming session 最長 10 分鐘。本 App 預設在 570 秒（9 分 30 秒）自動切換到下一個 Gemini session，因此一堂 1–3 小時的課不需要手動重連。

官方文件：

https://ai.google.dev/gemini-api/docs/live-api/live-transcribe

### 筆記與摘要

```text
claude-haiku-4-5-20251001
```

用途：

- 約每 60 秒整理一個 note block
- 將多個 note blocks 壓縮為 chapter summary
- 下課後整併為 final lecture notes

官方模型頁：

https://platform.claude.com/docs/en/models/overview

---

# 3. 第一次使用：完整安裝流程

以下只需要做一次。

## Step 1 — 解壓縮專案

下載並解壓縮：

```text
lecture_live_notes_gemini.zip
```

你會得到：

```text
lecture_live_notes/
├── app.py
├── launcher.py
├── Lecture.app/
├── scripts/
├── requirements.txt
├── .env.example
├── README.md
└── static/
    ├── index.html
    ├── styles.css
    └── app.js
```

接下來的所有指令，都在 `lecture_live_notes` 這個資料夾裡執行。

---

## Step 2 — 確認 Python

建議使用 Python 3.11 或 3.12。

在 Terminal / PowerShell 輸入：

```bash
python --version
```

如果 Windows 找不到 `python`，可以試：

```powershell
py --version
```

理想情況會看到類似：

```text
Python 3.12.x
```

---

## Step 3 — 進入專案資料夾

### macOS / Linux

假設你把專案放在 Downloads：

```bash
cd ~/Downloads/lecture_live_notes
```

### Windows PowerShell

例如：

```powershell
cd "$HOME\Downloads\lecture_live_notes"
```

確認目前目錄中有 `app.py`：

### macOS / Linux

```bash
ls
```

### Windows

```powershell
dir
```

你應該看到：

```text
app.py
requirements.txt
.env.example
README.md
static/
```

---

## Step 4 — 建立 Python virtual environment

強烈建議使用 virtual environment，避免污染系統 Python。

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
```

啟用後 Terminal 通常會出現：

```text
(.venv)
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

如果 PowerShell 顯示 execution policy 阻擋，可以在目前視窗暫時允許：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

然後再執行：

```powershell
.\.venv\Scripts\Activate.ps1
```

---

## Step 5 — 安裝套件

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

會安裝：

- FastAPI
- Uvicorn
- httpx
- websockets
- python-dotenv
- opencc-python-reimplemented（繁體轉換）
- markdown-it-py（筆記排版）

這個版本完全不需要下載 Whisper model，也不需要 GPU。

---

# 4. 建立 API Keys

你需要 Gemini API key（即時轉錄）和 Anthropic API key（筆記摘要）。以下先設定 Gemini。

## Step 1

前往 Google AI Studio：

https://aistudio.google.com/

登入 Google 帳號。

## Step 2

進入 API Keys 頁面，取得既有 key，或建立新的 API key。

Google 官方 Gemini API Getting Started：

https://ai.google.dev/gemini-api/docs/get-started

## Step 3

不要把 API key 貼到 `index.html`、JavaScript 或 GitHub。

這個專案的 key 放在本機 `.env`，只由 Python backend 使用。

## Anthropic API key

前往 https://platform.claude.com/，取得可使用 Claude Haiku 4.5 的 API key，填入 `.env` 的 `ANTHROPIC_API_KEY`。

摘要直接使用 Anthropic Messages API：`https://api.anthropic.com/v1/messages`。

---

# 5. 建立 `.env`

專案附有：

```text
.env.example
```

先複製一份成 `.env`。

### macOS / Linux

```bash
cp .env.example .env
```

### Windows PowerShell

```powershell
Copy-Item .env.example .env
```

然後用 VS Code 或其他文字編輯器打開 `.env`。

例如 VS Code：

```bash
code .env
```

找到：

```env
GEMINI_API_KEY=
ANTHROPIC_API_KEY=
```

改成：

```env
GEMINI_API_KEY=你的_Gemini_API_Key
ANTHROPIC_API_KEY=你的_Anthropic_API_Key
```

例如：

```env
GEMINI_API_KEY=AIzaSyxxxxxxxxxxxxxxxxxxxxxxxx
ANTHROPIC_API_KEY=你的_Anthropic_API_Key
```

不要加引號。

`ANTHROPIC_API_KEY` 優先使用 `.env` 中的非空值，未填才使用 shell 環境變數，避免舊的 shell key 覆蓋專案設定。修改 `.env` 後需重啟 server。

`.env` 其餘預設設定可以先完全不改：

```env
TRANSCRIBE_MODEL=gemini-3.5-transcribe-live
SUMMARY_MODEL=claude-haiku-4-5-20251001
TRANSCRIPTION_MODE=SMART
CUSTOM_VOCABULARY=
NOTE_WINDOW_SECONDS=60
ROLLUP_EVERY_BLOCKS=10
SESSION_ROTATE_SECONDS=570
GEMINI_FINALIZE_GRACE_SECONDS=1.5
GEMINI_PING_INTERVAL_SECONDS=30
GEMINI_PING_TIMEOUT_SECONDS=60
OUTPUT_DIR=lectures
```

---

# 6. 啟動 App

每次要上課前，都先進入專案資料夾並啟用 virtual environment。

## macOS / Linux

```bash
cd ~/Downloads/lecture_live_notes
source .venv/bin/activate
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## Windows PowerShell

```powershell
cd "$HOME\Downloads\lecture_live_notes"
.\.venv\Scripts\Activate.ps1
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

如果成功，你會看到類似：

```text
Uvicorn running on http://127.0.0.1:8000
```

不要關掉這個 Terminal / PowerShell 視窗。

---

# 7. 第一次先做 Health Check

瀏覽器打開：

```text
http://127.0.0.1:8000/health
```

正常時會看到類似：

```json
{
  "ok": true,
  "transcribe_model": "gemini-3.5-transcribe-live",
  "summary_provider": "anthropic",
  "summary_model": "claude-haiku-4-5-20251001",
  "api_key_configured": true,
  "summary_api_key_configured": true
}
```

兩把 key 都需要載入：

```json
{
  "api_key_configured": true,
  "summary_api_key_configured": true
}
```

`api_key_configured` 對應 Gemini，`summary_api_key_configured` 對應 Anthropic。`false` 表示沒有讀到非空 key；`true` 僅表示已載入，不代表 API 已驗證有效。Anthropic 若回傳 `401`，請更新 `.env` 的 `ANTHROPIC_API_KEY` 並重啟 server。

---

# 8. 打開課堂介面

瀏覽器開：

```text
http://127.0.0.1:8000
```

建議使用目前版本的 Chrome 或 Edge。

第一次按開始時，瀏覽器會要求麥克風權限。

請選：

```text
Allow / 允許
```

如果拒絕，App 無法取得老師的聲音。

---

# 9. 每堂課開始前怎麼設定

桌面左側的「課程設定」有三個欄位；手機請先展開上方的「課程設定」。錄音與整理筆記期間，設定會暫時鎖定。

## 9.1 課程名稱

例如：

```text
Machine Learning Week 3
```

或：

```text
Medical Informatics 2026-09-07
```

這個名稱會放入最後的筆記標題和 metadata。

---

## 9.2 Custom Vocabulary

這是非常重要的功能。

把老師今天可能會講到、ASR 容易辨識錯誤的專有名詞填進去，以逗號分隔。

例如機器學習課：

```text
PyTorch, ResNet, backpropagation, cross entropy, DataLoader, stochastic gradient descent, overfitting, regularization
```

醫學課：

```text
mesorectum, TME, circumferential resection margin, KRAS, BRAF, anastomotic leakage, neoadjuvant therapy
```

統計課：

```text
heteroscedasticity, multicollinearity, logistic regression, Cox proportional hazards, Kaplan-Meier, bootstrap
```

建議：

- 放真正容易辨識錯的專有名詞
- 不需要塞一般常用字
- 通常 20–100 個最重要的詞就很夠
- App 目前最多取前 100 個 terms

Gemini API 官方最多接受 1,000 個 custom vocabulary terms，但官方也表示實務上通常約 100 個以內效果最好。

---

## 9.3 SMART vs VERBATIM

### SMART — 建議上課使用

```text
SMART：去贅詞＋格式化
```

例如老師講：

```text
那個，呃，我們今天其實先講 logistic regression，然後，呃，這個主要是用在 binary outcome……
```

SMART transcription 會傾向整理成較可讀的形式。

適合：

- 大學上課
- 會議筆記
- 複習用逐字稿
- 不在乎每一個「嗯、呃、那個」

### VERBATIM

```text
VERBATIM：盡量逐字
```

適合：

- 希望保留原始語句
- 語言研究
- 需要觀察講者實際措辭

如果你的目標是「上課筆記」，建議保留 SMART。

---

# 10. 開始上課

設定完成後按：

```text
開始上課
```

正常流程會是：

```text
正在連接語音轉錄服務…
↓
請允許使用麥克風…
↓
正在聆聽，記錄每一句話
```

計時器開始計時。

此時 App 已經同時做三件事：

1. 保存本機 WAV 錄音
2. 將音訊送到 Gemini Live 做 ASR
3. 將 finalized transcript 累積成課堂筆記

---

# 11. 上課中你會看到什麼？

## 左側：即時逐字稿

老師正在說話時，底部可能先出現：

```text
… logistic regression is generally used for binary...
```

這是 interim transcription。

它可能持續修改，因此不會直接寫入正式筆記。

老師停頓後 Gemini 產生 finalized transcript，例如：

```text
[00:14:23] Logistic regression is generally used for binary outcomes.
```

這時才正式：

- 顯示在 transcript
- 寫入 `transcript.txt`
- 送入筆記摘要 pipeline

這樣可以避免未完成的 ASR hypothesis 污染正式筆記。

---

## 右側：即時課堂筆記

大約每 60 秒左右的 finalized transcript 會整理成一個 note block，例如：

```markdown
### 00:14:02–00:15:10
**主題：** Logistic regression 的基本用途

- 重點：Logistic regression 主要用於 binary outcome。
- 重點：模型輸出需要經過 logistic function 轉換。
- 定義／公式／數字：老師提到 log odds 與 predictors 的關係。
- 老師特別強調：不要直接把 coefficient 解釋成 probability change。
```

如果 ASR 內容本身不可靠，prompt 會要求模型使用：

```text
[待確認]
```

而不是自行腦補。

---

# 12. 老師講到重要內容時：按 ⭐ 標記重點

如果老師說：

```text
「這個期末會考。」
```

或：

```text
「這個概念非常重要。」
```

你可以按：

```text
⭐ 標記重點
```

App 會在即時筆記留下時間點：

```text
> ⭐ 使用者標記重點：00:37:42
```

之後你可以直接回到錄音附近的位置複習。

目前 marker 只記時間，不會自動切 WAV；這是刻意保持 MVP 簡單。

---

# 13. 長時間上課怎麼處理？

Gemini Live Transcribe 的單一 continuous streaming session 有 10 分鐘限制。

本 App 會在：

```env
SESSION_ROTATE_SECONDS=570
```

也就是 9 分 30 秒左右，自動關閉舊 Gemini session 並建立下一個。

畫面可能短暫看到：

```text
Gemini Live session 自動輪替中，錄音會持續緩衝。
```

你不需要按任何按鈕。

重要的是：

```text
Browser → FastAPI 的錄音不中斷
lecture.wav 繼續寫入
新的音訊先放進 queue
下一個 Gemini session 建立後繼續送
```

因此它是以「數小時 lecture」為設計目標，而不是只做 10 分鐘 demo。

---

# 14. 下課

老師講完後按：

```text
下課／停止
```

App 會：

1. 停止麥克風
2. 結束最後的 Gemini Live transcription
3. 接收最後 finalized transcript
4. 完成尚未產生的 note block
5. 整理 remaining note blocks
6. 產生 final lecture notes
7. 關閉 WAV
8. 顯示完整筆記與下載按鈕

完成後畫面會出現：

```text
本堂課已儲存，筆記準備好複習了
```

整理期間，開始按鈕會顯示「整理筆記中…」並暫時停用。檔案儲存完成後，按下「開始新課堂」即可開始下一堂課。

不要直接把瀏覽器頁面關掉來代替「下課／停止」；正常按停止才能完成最後的整併流程。

---

# 15. 每堂課會產生哪些檔案？

所有資料預設存在：

```text
lecture_live_notes/lectures/
```

`.env` 的 `OUTPUT_DIR=lectures` 以專案資料夾為基準，從桌面啟動時也相同；若設定為絕對路徑，則使用該路徑。

每堂課一個資料夾：

```text
lectures/
└── 20260907_090001/
    ├── lecture.wav
    ├── transcript.txt
    ├── live_notes.md
    ├── final_notes.md
    └── session.json
```

## `lecture.wav`

整堂課的本機錄音。

格式：

```text
PCM WAV
mono
16 kHz
16-bit
```

用途：

- 回放
- 重新做 ASR
- 對照錯誤逐字稿

---

## `transcript.txt`

Gemini finalized transcription。

例如：

```text
[00:02:15] Today we are going to discuss logistic regression.
[00:02:22] Logistic regression is generally used for binary outcomes.
```

只有 finalized transcript 會寫入這裡。

---

## `live_notes.md`

上課過程中持續生成的分鐘級筆記。

即使最後 final summarization 出問題，前面已完成的即時筆記仍然在這個檔案。

---

## `final_notes.md`

最適合你下課後閱讀的版本。

預設格式：

```markdown
# 課程名稱

## 本堂課總覽

## 核心概念與架構

## 詳細重點

## 重要定義／公式／數字

## 老師特別強調或明示考點

## 待釐清事項
```

---

## `finalize_input.json`

整併完整筆記時用的素材，包含課程名稱、章節摘要，以及還沒併進章節的最後幾段筆記。

留著這個檔案是為了讓整併可以重跑。按「重新整併」時讀的是它，不是去解析 `final_notes.md`。你手動編輯過筆記也不會影響重跑。

---

## `session.json`

記錄這堂課的 metadata，例如：

- course title
- 使用的 ASR model
- summary model
- SMART / VERBATIM
- custom vocabulary
- audio duration
- note window
- session rotation interval

適合之後做 lecture library 或資料庫索引。

---

# 16. 最推薦的「每次上課」操作流程

第一次安裝完成後，以後每堂課只需要以下幾步。

## macOS

### 1. 打開 Terminal

```bash
cd ~/Downloads/lecture_live_notes
source .venv/bin/activate
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

### 2. Chrome 打開

```text
http://127.0.0.1:8000
```

### 3. 填課程名稱

例如：

```text
Deep Learning Week 5
```

### 4. 貼今天的專有名詞

例如：

```text
Transformer, attention, self-attention, positional encoding, query, key, value, softmax
```

### 5. 選 SMART

### 6. 按「開始上課」

### 7. 老師講到重要內容就按 ⭐

### 8. 下課按「下課／停止」

### 9. 看 `final_notes.md`

完成。

---

## Windows

### 1. 打開 PowerShell

```powershell
cd "$HOME\Downloads\lecture_live_notes"
.\.venv\Scripts\Activate.ps1
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

### 2–9

跟 macOS 完全相同。

---

# 17. 建議的實際上課配置

## 小教室

通常直接使用 laptop microphone 即可。

```text
老師
 ↓
MacBook / Windows laptop 麥克風
```

## 大型階梯教室

ASR 的上限通常不是模型，而是收音品質。

如果你坐得離老師很遠，建議：

- 坐靠近講台
- 使用較好的外接 USB microphone
- 避免把電腦放在會受到鍵盤敲擊、桌面震動的位置
- 如果老師使用 PA system，靠近喇叭但不要過度靠近造成 clipping

## 使用前先測 30 秒

第一次到新的教室，建議正式上課前測一小段：

1. 開始錄音
2. 確認左側有 transcript
3. 確認中英文術語辨識是否合理
4. 停止
5. 聽一下 WAV 音量

如果 WAV 本身聽不清楚，換 ASR 模型通常也救不了。

---

# 18. 如果是 Zoom / Teams / YouTube 課程

目前版本抓的是：

```text
microphone input
```

不是：

```text
system audio
```

因此如果你播放線上課程，現在的做法會變成：

```text
喇叭播放
 ↓
麥克風重新收音
```

這不是最佳做法。

若要專門處理：

- Zoom
- Microsoft Teams
- Google Meet
- YouTube lecture
- 本機影片

應該加入 system-audio capture / loopback input。這不在目前 MVP 中。

---

# 19. `.env` 進階設定

## NOTE_WINDOW_SECONDS

```env
NOTE_WINDOW_SECONDS=60
```

代表大約累積 60 秒逐字稿後產生一段即時筆記。

### 更即時

```env
NOTE_WINDOW_SECONDS=30
```

優點：筆記更快出現。

缺點：

- API request 增加
- 每段 context 比較碎

### 更完整

```env
NOTE_WINDOW_SECONDS=90
```

或：

```env
NOTE_WINDOW_SECONDS=120
```

優點：每個筆記 block context 更完整。

對一般大學 lecture，我建議先用：

```env
NOTE_WINDOW_SECONDS=60
```

---

## ROLLUP_EVERY_BLOCKS

```env
ROLLUP_EVERY_BLOCKS=10
```

代表每累積 10 個 note blocks，就在背景做一次章節整併。

如果 `NOTE_WINDOW_SECONDS=60`，大致相當於每約 10 分鐘形成一個 chapter summary。

這是 hierarchical summarization：

```text
raw transcript
     ↓
1-minute notes
     ↓
~10-minute chapter
     ↓
final lecture summary
```

避免兩三小時後把整份原始 transcript 一次塞回模型。

---

## SESSION_ROTATE_SECONDS

```env
SESSION_ROTATE_SECONDS=570
```

不要設成 600 或更大。

Gemini Live Transcribe 官方 continuous session limit 是 10 分鐘，預留一點安全 margin 比較合理。

---

## CUSTOM_VOCABULARY

除了每堂課在 UI 輸入，你也可以在 `.env` 放「每堂課都會用到」的固定詞彙。

例如：

```env
CUSTOM_VOCABULARY=Python,PyTorch,NumPy,pandas,SQL,GitHub
```

UI 輸入的詞會和 `.env` 合併、去除重複，再取前 100 個。

---

## GEMINI_PING_INTERVAL_SECONDS 與 GEMINI_PING_TIMEOUT_SECONDS

這兩個值控制 App 多久確認一次 Gemini Live 連線還活著。預設每 30 秒送一次 ping，60 秒內沒有回應就判定連線已死並重連。

網路不穩時會需要放寬。keepalive ping 跟音訊資料排在同一條上傳通道，上傳一慢，音訊就積壓，ping 被推到佇列後面，回應自然遲到。這種情況下連線其實是好的，只是回應晚到，太短的期限會把健康的連線誤判成斷線。

`server.log` 出現這行，代表期限太緊：

```text
sent 1011 (internal error) keepalive ping timeout
```

`sent` 表示是 App 這端主動關閉連線，不是 Google 把你踢掉。校園或醫院的共用 Wi-Fi 常有這個問題，可以把 timeout 調到 90 或 120：

```env
GEMINI_PING_TIMEOUT_SECONDS=120
```

代價是真的斷線時要多花這段時間才會被發現，這段期間的逐字稿會掉。錄音不受影響。

---

# 20. 常見問題與排錯

## 問題 1：`api_key_configured` 是 false

打開：

```text
http://127.0.0.1:8000/health
```

如果看到：

```json
"api_key_configured": false
```

檢查：

1. 是否真的有 `.env`，不是只有 `.env.example`
2. `.env` 是否和 `app.py` 在同一資料夾
3. 是否寫成：

```env
GEMINI_API_KEY=xxxxx
```

4. 修改 `.env` 後重新啟動 Uvicorn

---

## 問題 2：瀏覽器說沒有麥克風權限

### Chrome / Edge

點網址列旁邊的網站權限圖示，將 Microphone 設成 Allow。

### macOS

另外確認：

```text
System Settings
→ Privacy & Security
→ Microphone
```

Chrome / Edge 是否有權限。

### Windows

確認：

```text
Settings
→ Privacy & security
→ Microphone
```

瀏覽器是否允許存取麥克風。

---

## 問題 3：有錄音但沒有逐字稿

先檢查：

```text
http://127.0.0.1:8000/health
```

然後看 Terminal 是否有 Gemini API error。

常見原因：

- API key 錯誤
- Gemini API quota / billing 問題
- 網路阻擋 WebSocket
- 公司 / 學校防火牆阻擋 `wss://generativelanguage.googleapis.com`
- 麥克風音量太低

---

## 問題 4：出現 429

HTTP / API 429 通常代表 quota 或 rate limit。

轉錄錯誤請檢查 Google AI Studio / Gemini API 的使用量與 quota；摘要錯誤請檢查 Anthropic Console 的使用量與限額。

如果只有 summary request 429，而 ASR 還在跑，`lecture.wav` 和 finalized transcript 仍可繼續保存；摘要部分可能會顯示錯誤訊息。

### 摘要出現 529、503，或暫時連線失敗

Anthropic 的 `529 overloaded_error` 表示服務暫時過載。摘要請求遇到 `429`、`500`、`502`、`503`、`504`、`529` 或網路傳輸錯誤時，最多嘗試 4 次，重試前分別等待 2、4、8 秒。分段筆記、章節摘要與最終筆記皆使用相同的重試流程。

重試期間仍持續接收錄音與逐字稿。若持續失敗，分段筆記會保留繁體逐字稿片段，最終筆記則保留已完成的摘要與尚未整併的筆記，並附上失敗原因。重試無法保證服務恢復；API key 或參數等 `400`、`401`、`403`、`404` 錯誤不會重試。

若摘要達到單次 8,192 tokens 的輸出上限，App 會回報未完成並保留素材，不會把截斷的結果當成完整筆記。

官方說明：https://platform.claude.com/docs/en/api/errors

---

## 問題 5：Gemini Live 連線錯誤

App 會自動重新嘗試 Gemini Live 連線。

連續失敗 5 次後，介面會顯示：

```text
Gemini Live 連續連線失敗 5 次。
錄音仍會保存，但本堂課不再產生即時逐字稿。
```

也就是說：

```text
ASR 掛掉 ≠ WAV 錄音消失
```

至少原始錄音仍然保留，可事後重新轉錄。

---

## 問題 6：專有名詞一直辨識錯

優先做三件事：

1. 把該詞加入 Custom Vocabulary
2. 確認老師聲音本身夠清楚
3. 只放真正重要的 terms，不要一次塞大量無關詞彙

例如不要只寫縮寫：

```text
CRM
```

可以同時放：

```text
CRM, circumferential resection margin
```

---

## 問題 7：Chrome 關掉後筆記怎麼辦？

已經寫入硬碟的檔案仍存在：

```text
lectures/<session_id>/
```

但如果沒有正常按「下課／停止」，最後的 final summary 可能沒有完成。

因此正式下課時仍建議使用 App 的停止按鈕。

---

## 問題 8：完整筆記整併失敗

`final_notes.md` 開頭出現這行，表示最後一次整併沒有成功：

```text
最終整併失敗：RuntimeError: Anthropic 摘要請求失敗（ConnectError），已嘗試 5 次。
```

素材沒有丟。錯誤訊息下面就是章節摘要和還沒整併的筆記，逐字稿、即時筆記和錄音也都完整。

有三條路可以救回來，不用手動處理：

1. 完整筆記面板右上角的「重新整併」按鈕，立刻重跑一次。
2. server 如果還開著，會在背景自動重試（1 分鐘、5 分鐘、15 分鐘後各一次），成功就停。
3. 下次打開 App 時，頁面上方會出現提示，列出上一堂沒整併完成的課，按「重新整併」即可。

第 2 條只在 server 還活著時有效。下課直接關機的話，靠第 3 條。

整併失敗幾乎都是網路問題。整併是整堂課最長的一次 API 呼叫，要一口氣生成五千字左右，比逐塊筆記久得多。網路不穩時它最容易中招。可以先確認網路，再按重新整併。

---

## 問題 9：電腦睡眠或闔上螢幕

不要讓電腦進入 sleep。

如果 laptop sleep、browser 被 OS suspend 或網路中斷，麥克風串流也會停止。

長課建議：

- 接電源
- 關閉自動睡眠
- 保持瀏覽器 tab 開啟

---

# 21. Privacy / Security

## API key

API key 存在：

```text
.env
```

Python backend 才能讀取。

Browser 不會直接取得 API key。

不要：

- 把 `.env` 上傳 GitHub
- 把 API key 寫進 `static/index.html`
- 把 key 貼到公開 screenshot

本專案附有 `.gitignore`，預設排除 `.env` 和 `lectures/`。

## 音訊

本機：

```text
lecture.wav
```

會保存在專案的 `lectures/` 目錄。

同時，錄音中的 audio chunks 會透過網路送往 Gemini API 做 transcription。

已確認的逐字稿與筆記素材會傳送到 Anthropic API，由 Claude Haiku 整理成筆記。

因此這不是「完全 local」的錄音系統。

---

# 22. 關閉 App

下課且 final notes 完成後，可回到 Terminal / PowerShell 按：

```text
Ctrl + C
```

停止 Uvicorn server。

下次上課重新執行：

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

即可。

---

# 23. 建議你實際第一次這樣測

不要第一次就在正式兩小時課堂才測。

先在家做一個 3–5 分鐘 dry run：

1. 啟動 App
2. `/health` 確認 API key
3. 開首頁
4. Course title 填 `Test Lecture`
5. Custom vocabulary 填 5–10 個詞
6. 選 SMART
7. 按開始
8. 播放或自己念一段中英混合內容
9. 看 interim transcript
10. 看 finalized transcript
11. 等至少產生一個 note block
12. 按 ⭐
13. 按停止
14. 確認畫面出現 final notes
15. 打開 `lectures/<session_id>/`
16. 確認五個檔案都存在
17. 播放 `lecture.wav` 確認收音品質

如果這個 dry run 沒問題，再拿去正式上課。

---

# 24. 目前 MVP 的限制

目前版本刻意先把核心功能做好，因此還沒有：

- speaker diarization
- system audio capture
- slide / PDF synchronization
- 搜尋歷史課堂
- 自動產生 flashcards
- 自動產生考題
- lecture semantic search / RAG
- AudioWorklet
- persistent database
- account / authentication

另外，Gemini Live Transcribe 本身目前不支援 live speaker diarization 和 word-level timestamps；如果未來需要精確 speaker attribution 或逐字 timestamp，需要額外的離線處理 pipeline。

---

# 25. 最重要的操作原則

如果只記五件事：

```text
1. 上課前雙擊桌面的 Lecture.app
2. 等瀏覽器自動開啟課堂工作台
3. 專有名詞先貼進 Custom Vocabulary
4. 上課用 SMART，重要內容按 ⭐
5. 下課一定按「下課／停止」，再關頁面
```

完成。

---

# 26. 開發測試

測試使用 Python 內建 `unittest`；瀏覽器 E2E 使用 Playwright。Gemini 轉錄、Anthropic 摘要與麥克風均使用測試資料，不呼叫真實 API，輸出寫入暫存目錄。

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m unittest discover -s tests -v
```

涵蓋 unit、啟動 smoke、WebSocket 完整流程及瀏覽器 E2E，包括暫時失敗重試、持續 `503` 時保留逐字稿、繁體輸出、Markdown 排版、標記重點與下載檔案。摘要以 streaming 讀取，測試也涵蓋串流中途斷線後重試不會重複輸出、串流內回報的錯誤事件、整併失敗後重跑，以及下次啟動時列出未完成的課。

UI 測試涵蓋 320、390、768、1024、1440px 五種寬度，以及連線中與整理期間防止重複開始、麥克風授權失敗、連線中斷後釋放麥克風、自動捲動切換與鍵盤操作。

macOS 啟動器測試會啟動真實的本機服務，驗證同時啟動只產生一個服務、避開已占用的 port、啟動失敗與逾時處理，以及輸出路徑固定於專案資料夾。

只執行不需要瀏覽器的測試：

```bash
.venv/bin/python -m unittest tests.test_app tests.test_workflow tests.test_launcher -v
```

開發時若需重新製作 macOS 啟動器（一般使用不需要），先安裝上述開發依賴與 Chromium，再執行：

```bash
.venv/bin/python scripts/build_macos_app.py
```

啟動器執行記錄在 `.runtime/server.log`；`.runtime/server.json` 記錄目前的本機服務位置，不包含 API key。
